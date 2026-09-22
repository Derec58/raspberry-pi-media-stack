#!/usr/bin/env python3
"""Write external .en.srt sidecars for anime so Jellyfin stops transcoding on mobile.

Why this exists
---------------
Jellyfin cannot deliver ASS/SSA subtitles to a client as text. It has to burn them
into the video, which forces a full re-encode even when the video itself would have
direct-played. On this host that is fatal: the Raspberry Pi 5 has no hardware video
encoder at all (the Pi 4's was removed and never replaced), so every transcode is
software libx264 on four ARM cores.

Measured on this machine with `libx264 veryfast`:

    1080p HEVC10 -> x264                 6.0 fps   0.249x
    1080p HEVC10 + burn ASS -> x264      2.7 fps   0.111x

Burning subtitles roughly halves an already-unusable rate: a 24 minute episode takes
about 96 minutes to transcode, or 3.6 hours with subtitles burned in.

An external SRT is delivered to the client as *text*, so the video direct-plays and
the client renders the subtitles itself. That is the entire point of this script.

What it does NOT do: modify the source file. The embedded ASS track stays exactly
where it is, so typesetting is still available on clients where transcoding is
cheap (or where you are playing on the TV and do not care).

Track selection is the subtle part
----------------------------------
Taking the first English subtitle track is wrong. Real examples from this library:

    Zom 100:            3 ass eng "Signs & Songs [LostYears]"
                        4 ass eng "Full Subtitles [Zoombie]"
    Violet Evergarden:  5 ass eng "Signs & Songs@SCY"
                        6 ass eng "Dialogue@Vivid-Asenshi"

A "Signs & Songs" track only translates on-screen text. Picking it produces a
subtitle file with essentially no dialogue in it -- a silent, baffling failure.
So tracks are scored: dialogue-ish titles win, signs-only titles are pushed to last.

Bitmap subtitles (hdmv_pgs_subtitle, dvd_subtitle) cannot become text without OCR
and are skipped. In this library only "Kabaneri of the Iron Fortress" is affected;
every other PGS series also carries an ASS track.

Usage:
    scripts/extract-anime-subs.py                      # dry run, whole library
    scripts/extract-anime-subs.py --apply
    scripts/extract-anime-subs.py --apply --series "Zom 100" "Violet Evergarden"
"""

import argparse
import json
import os
import re
import subprocess
import sys

FFPROBE = "/usr/lib/jellyfin-ffmpeg/ffprobe"
FFMPEG = "/usr/lib/jellyfin-ffmpeg/ffmpeg"
ANIME_ROOT = "/mnt/jellyfin/Anime"
DEFAULT_ROOTS = ["/mnt/jellyfin/Movies", "/mnt/jellyfin/Anime", "/mnt/jellyfin/TV Shows"]
VIDEO_EXT = (".mkv", ".mp4", ".m4v")

# Library files must be owned by the *arr runtime uid or imports break later.
PUID, PGID = 112, 122

TEXT_CODECS = {"ass", "ssa", "subrip", "srt", "mov_text", "webvtt"}
BITMAP_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}

# Higher is better. A track with no title at all is fine -- it is usually the only one.
GOOD_TITLE = re.compile(r"full|dialogue|complete|main", re.I)
SIGNS_ONLY = re.compile(r"sign|song|karaoke|op/ed|forced", re.I)
# A commentary track is text, English and often the ONLY text English track on a
# disc rip whose real dialogue subs are PGS. Fight Club's remux had four of them
# and the picker happily chose "English (Commentary #1)", producing a sidecar full
# of "we ended up doing a reshoot of this". Never use one.
COMMENTARY = re.compile(r"(?i)\bcommentar|\bdirector'?s?\b|\bcast\s*&|\bfilmmaker")
SDH = re.compile(r"\bsdh\b|hearing", re.I)


def probe(path):
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "s",
         "-show_entries", "stream=index,codec_name:stream_tags=language,title",
         "-of", "json", path],
        capture_output=True, text=True)
    try:
        return json.loads(r.stdout).get("streams", [])
    except (ValueError, TypeError):
        return []


def score_track(s):
    """Rank a subtitle stream. None means 'never use this one'."""
    codec = (s.get("codec_name") or "").lower()
    if codec in BITMAP_CODECS or codec not in TEXT_CODECS:
        return None
    tags = s.get("tags") or {}
    lang = (tags.get("language") or "").lower()
    if lang not in ("eng", "en", ""):
        return None

    title = tags.get("title") or ""
    score = 100
    if GOOD_TITLE.search(title):
        score += 50
    if SDH.search(title):
        score -= 20          # usable, but prefer a clean dialogue track
    if SIGNS_ONLY.search(title):
        score -= 200         # signs-only: last resort, never over a dialogue track
    if COMMENTARY.search(title):
        return None          # never a substitute for dialogue subtitles
    if lang == "":
        score -= 10          # untagged language is a weaker signal than an explicit eng
    return score


def pick_track(streams):
    ranked = []
    for s in streams:
        sc = score_track(s)
        if sc is not None:
            ranked.append((sc, s))
    if not ranked:
        return None
    ranked.sort(key=lambda t: -t[0])
    return ranked[0][1]


# ffmpeg carries ASS styling into SRT as <font face="X" size="78">, which renders as
# enormous text on clients. Strip the styling but keep basic bold/italic.
FONT_TAG = re.compile(r"</?font[^>]*>", re.I)
# Any brace group, not just {\override}. ffmpeg also emits ASS line markers such as
# {=0} and {=10}, which the narrower \{\\...\} pattern missed and left on screen.
ASS_BRACES = re.compile(r"\{[^}]*\}")
TRAILING_WS = re.compile(r"[ \t]+\n")
CUE = re.compile(
    r"^\d+\s*\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})[^\n]*\n(.*?)(?=\n\n|\Z)",
    re.S | re.M)


# Identical text repeated inside this window is an animation artefact, not dialogue.
DEDUP_WINDOW_MS = 15000

TAGS = re.compile(r"<[^>]+>")
# ASS vector drawing (\p1 mode): runs of path commands and coordinates, e.g.
# "m 15 -10 l 425 -10 413 729 -1 725". Converted to SRT these become on-screen gibberish.
DRAWING = re.compile(r"^[mlbspnc\s\d.,-]+$", re.I)


def is_typeset_noise(body):
    """True for cues that are typesetting artefacts rather than readable subtitles."""
    visible = TAGS.sub("", body).strip()
    if not visible:
        return True
    # Letter-by-letter sign animations emit one cue per character.
    if len(visible) <= 2:
        return True
    # Vector drawing commands: mostly digits and path letters, no real words.
    if DRAWING.match(visible) and not re.search(r"[A-Za-z]{3}", visible):
        return True
    return False


def _ts(t):
    h, m, rest = t.split(":")
    sec, ms = rest.split(",")
    return ((int(h) * 60 + int(m)) * 60 + int(sec)) * 1000 + int(ms)


def clean_srt(text):
    """Strip ASS styling, then collapse the duplicate cues it leaves behind.

    Typeset signs in an ASS track are drawn as several stacked layers (fill, outline,
    shadow). Converted to SRT each layer becomes its own cue, so a single on-screen
    sign arrives as three or four identical overlapping subtitles. On one Zom 100
    episode that inflated a 24 minute file to 2921 cues, about 121 per minute where
    ordinary dialogue runs 12-20. Identical text over an overlapping time range is
    always a layer artefact, never real repeated dialogue, so it is safe to drop.
    """
    text = FONT_TAG.sub("", text)
    text = ASS_BRACES.sub("", text)
    text = TRAILING_WS.sub("\n", text)

    cues = []
    for start, end, body in CUE.findall(text):
        body = body.strip()
        if not body or is_typeset_noise(body):
            continue
        s_ms, e_ms = _ts(start), _ts(end)
        dup = False
        # Animated signs repeat the same string many times across a few seconds, so
        # compare against a time window rather than only strict overlap.
        for ps, pe, pbody in reversed(cues[-40:]):
            if pbody == body and s_ms - ps < DEDUP_WINDOW_MS:
                dup = True
                break
        if not dup:
            cues.append((s_ms, e_ms, body))

    def fmt(ms):
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        sec, ms = divmod(ms, 1000)
        return "%02d:%02d:%02d,%03d" % (h, m, sec, ms)

    out = []
    for i, (s_ms, e_ms, body) in enumerate(cues, 1):
        out.append("%d\n%s --> %s\n%s\n" % (i, fmt(s_ms), fmt(e_ms), body))
    return "\n".join(out)


def cue_count(text):
    return len(re.findall(r"^\d+\s*$", text, re.M))


def process(video, apply_changes):
    base, _ = os.path.splitext(video)
    out = base + ".en.srt"
    if os.path.exists(out):
        return ("skip", "sidecar exists", None)

    streams = probe(video)
    if not streams:
        return ("skip", "no subtitle streams", None)

    track = pick_track(streams)
    if track is None:
        codecs = ",".join(sorted({(s.get("codec_name") or "?") for s in streams}))
        return ("skip", f"no usable English text track ({codecs})", None)

    title = (track.get("tags") or {}).get("title") or "<untitled>"
    desc = f"idx {track['index']} {track.get('codec_name')} \"{title[:34]}\""
    if not apply_changes:
        return ("would", desc, None)

    tmp = out + ".tmp"
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-i", video,
         "-map", f"0:{track['index']}", "-c:s", "srt", "-f", "srt", tmp],
        capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        return ("fail", (r.stderr or "ffmpeg failed").strip()[:90], None)

    with open(tmp, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    cleaned = clean_srt(raw)
    if cue_count(cleaned) == 0:
        # Creditless openings/endings and signs-only tracks legitimately carry no
        # dialogue. That is nothing to fix, so it is a skip rather than a failure.
        os.remove(tmp)
        return ("skip", "no dialogue cues (creditless/signs-only)", None)

    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(cleaned)
    os.replace(tmp, out)
    try:
        os.chown(out, PUID, PGID)
        os.chmod(out, 0o664)
    except PermissionError:
        return ("ok", desc + "  [chown failed - run with sudo]", cue_count(cleaned))
    return ("ok", desc, cue_count(cleaned))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write files (default is a dry run)")
    ap.add_argument("--root", nargs="*", default=DEFAULT_ROOTS,
                    help="one or more library roots (default: Movies, Anime, TV Shows)")
    ap.add_argument("--series", nargs="*", help="only these series folders (substring match)")
    ap.add_argument("--limit", type=int, help="stop after N videos, for testing")
    args = ap.parse_args()

    roots = args.root if isinstance(args.root, list) else [args.root]
    videos = []
    for root in roots:
        if not os.path.isdir(root):
            print(f"  !! skipping missing root: {root}")
            continue
        for dirpath, _, files in os.walk(root):
            for f in sorted(files):
                if not f.lower().endswith(VIDEO_EXT):
                    continue
                if args.series:
                    rel = os.path.relpath(dirpath, root)
                    if not any(s.lower() in rel.lower() for s in args.series):
                        continue
                videos.append(os.path.join(dirpath, f))
    videos.sort()
    if args.limit:
        videos = videos[:args.limit]

    if not args.apply:
        print("=== DRY RUN - no files written (pass --apply) ===")
    print(f"{len(videos)} video file(s) under {', '.join(roots)}\n")

    tally = {}
    for v in videos:
        status, detail, cues = process(v, args.apply)
        tally[status] = tally.get(status, 0) + 1
        if status in ("ok", "would", "fail"):
            mark = {"ok": "+", "would": ".", "fail": "!"}[status]
            name = next((os.path.relpath(v, r) for r in roots if v.startswith(r)), v)
            extra = f"  ({cues} cues)" if cues else ""
            print(f"  {mark} {name[:78]:<78} {detail}{extra}")

    print()
    for k in ("ok", "would", "skip", "fail"):
        if k in tally:
            print(f"  {k:<6} {tally[k]}")
    # Per-file failures are logged above and are not a reason to fail the systemd
    # unit -- doing that would leave it permanently "failed" and make OnFailure=
    # useless for detecting a real problem. Only a crash should be non-zero.
    return 0


if __name__ == "__main__":
    sys.exit(main())
