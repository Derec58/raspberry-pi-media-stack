#!/usr/bin/env python3
"""Repair default/forced flags that point at the wrong subtitle or audio track.

A signs-and-songs subtitle track flagged default+forced means the viewer gets
karaoke and street signs instead of dialogue, while the real "Full Subtitles"
track sits there unselected. Chainsaw Man, SPY x FAMILY, Solo Leveling and seven
other series ship this way -- 112 files. The same class of mistake as a commentary
subtitle track being the only text one: the right track exists, the flags point
elsewhere.

Likewise a commentary AUDIO track flagged default (Dragon Ball Daima flags *every*
audio track default, commentary included) leaves track choice to the client.

mkvpropedit rewrites only the container header, so this is instant regardless of
file size and involves no re-encode. Dry run by default.
"""
import argparse, json, os, subprocess, sys

FFPROBE = "/usr/lib/jellyfin-ffmpeg/ffprobe"
MKVPROPEDIT = "/usr/bin/mkvpropedit"
ROOTS = ["/mnt/jellyfin/Movies", "/mnt/jellyfin/Anime", "/mnt/jellyfin/TV Shows"]

SIGNS = ("signs", "songs", "s&s", "signs/songs")
COMMENTARY = ("commentar", "director's", "filmmaker")
FULL = ("full", "dialogue", "complete", "main")


def probe(path):
    p = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
        "stream=index,codec_type,codec_name:stream_tags=language,title:"
        "stream_disposition=default,forced", "-of", "json", path],
        capture_output=True, text=True)
    if p.returncode != 0:
        return None
    try:
        return json.load.__self__.loads(p.stdout) if False else json.loads(p.stdout)
    except Exception:
        return None


def plan_for(path):
    """Return a list of (mkvpropedit selector, flag, value, why)."""
    d = probe(path)
    if not d:
        return []
    subs = [s for s in d.get("streams", []) if s["codec_type"] == "subtitle"]
    auds = [s for s in d.get("streams", []) if s["codec_type"] == "audio"]
    ops = []

    def title(s):
        return ((s.get("tags") or {}).get("title") or "").lower()

    def is_eng(s):
        return ((s.get("tags") or {}).get("language") or "").lower() in ("eng", "en", "")

    # --- subtitles: never default to a signs-only track when a full one exists ---
    eng_subs = [s for s in subs if is_eng(s)]
    signs = [s for s in eng_subs if any(k in title(s) for k in SIGNS)]
    full = [s for s in eng_subs if any(k in title(s) for k in FULL)
            and not any(k in title(s) for k in SIGNS)
            and not any(k in title(s) for k in COMMENTARY)]
    bad_default = [s for s in signs if (s.get("disposition") or {}).get("default")]
    if bad_default and full:
        for n, s in enumerate(subs, start=1):
            if s in bad_default:
                ops.append((f"track:s{n}", "flag-default", 0, f"signs-only was default: {title(s)[:34]}"))
                if (s.get("disposition") or {}).get("forced"):
                    ops.append((f"track:s{n}", "flag-forced", 0, "signs-only was forced"))
            elif s is full[0]:
                ops.append((f"track:s{n}", "flag-default", 1, f"promote dialogue: {title(s)[:34]}"))

    # --- audio: a commentary track must never be default ---
    for n, s in enumerate(auds, start=1):
        if any(k in title(s) for k in COMMENTARY) and (s.get("disposition") or {}).get("default"):
            ops.append((f"track:a{n}", "flag-default", 0, f"commentary audio was default: {title(s)[:30]}"))
    return ops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--root", nargs="*", default=ROOTS)
    ap.add_argument("--series", nargs="*", help="only paths matching these substrings")
    args = ap.parse_args()

    vids = []
    for r in args.root:
        for dp, _, fs in os.walk(r):
            for f in sorted(fs):
                if not f.lower().endswith((".mkv",)):
                    continue
                p = os.path.join(dp, f)
                if args.series and not any(x.lower() in p.lower() for x in args.series):
                    continue
                vids.append(p)
    vids.sort()

    if not args.apply:
        print("=== DRY RUN - no files modified (pass --apply) ===")
    print(f"{len(vids)} mkv file(s)\n")

    touched = failed = 0
    for v in vids:
        ops = plan_for(v)
        if not ops:
            continue
        touched += 1
        print(f"  {os.path.basename(v)[:74]}")
        for sel, flag, val, why in ops:
            print(f"      {sel:<10} {flag}={val}   {why}")
        if args.apply:
            cmd = [MKVPROPEDIT, v]
            for sel, flag, val, _ in ops:
                cmd += ["--edit", sel, "--set", f"{flag}={val}"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                failed += 1
                print(f"      !! mkvpropedit failed: {(r.stderr or r.stdout).strip()[:90]}")

    print(f"\n  files needing repair: {touched}")
    if args.apply:
        print(f"  failures: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
