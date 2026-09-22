#!/usr/bin/env python3
"""Audit the MEDIA FILES for things that break playback. Read-only.

audit-filters.py checks the *arr CONFIG. Nothing checked the actual files, which
is how 112 episodes ended up defaulting to a signs-and-songs subtitle track and a
Fight Club sidecar ended up being the director's commentary. Both were found by
scanning, not by reasoning ahead -- so this runs on a timer.

The Pi 5 has no video encoder: every transcode is software libx264 at 0.249x real
time, 0.111x with subtitle burn-in. Each check below is a thing that silently
forces a transcode or silently shows the viewer the wrong track.

Exit 0 always -- this is a report, not a gate. Run it from a timer and read it.
"""
import json, os, subprocess, sys
from collections import Counter

FFPROBE = "/usr/lib/jellyfin-ffmpeg/ffprobe"
ROOTS = ["/mnt/jellyfin/Movies", "/mnt/jellyfin/Anime", "/mnt/jellyfin/TV Shows"]
CEILING_MBPS = 15.0            # 110 MB/min, the Radarr Bluray-1080p ceiling
SIGNS = ("signs", "songs", "s&s")
COMMENTARY = ("commentar", "director's", "filmmaker")
BITMAP = ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub")
LOSSLESS = ("truehd", "dts", "flac", "pcm_s24le", "pcm_s16le", "mlp")


def probe(path):
    p = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
        "format=duration,bit_rate,size:"
        "stream=index,codec_type,codec_name,profile,field_order:"
        "stream_tags=language,title:stream_disposition=default,forced",
        "-of", "json", path], capture_output=True, text=True)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except Exception:
        return None


def main():
    findings = Counter()
    detail = {k: [] for k in ("signs_default", "commentary_default", "bitmap_only",
                              "interlaced", "lossless_default", "over_ceiling",
                              "no_eng_sub", "orphan_sidecar")}
    vids, n = [], 0
    for r in ROOTS:
        for dp, _, fs in os.walk(r):
            names = {os.path.splitext(f)[0] for f in fs
                     if f.lower().endswith((".mkv", ".mp4", ".m4v"))}
            for f in fs:
                if f.endswith(".en.srt") and f[:-len(".en.srt")] not in names:
                    findings["orphan_sidecar"] += 1
                    detail["orphan_sidecar"].append(os.path.join(dp, f))
                if f.lower().endswith((".mkv", ".mp4", ".m4v")):
                    vids.append(os.path.join(dp, f))
    vids.sort()

    for v in vids:
        d = probe(v)
        if not d:
            continue
        n += 1
        name = os.path.basename(v)[:66]
        fmt = d.get("format", {})
        br = int(fmt.get("bit_rate") or 0) / 1e6
        if br > CEILING_MBPS:
            findings["over_ceiling"] += 1
            detail["over_ceiling"].append(f"{br:5.1f} Mbps  {name}")

        subs = [s for s in d.get("streams", []) if s["codec_type"] == "subtitle"]
        auds = [s for s in d.get("streams", []) if s["codec_type"] == "audio"]

        def ttl(s):
            return ((s.get("tags") or {}).get("title") or "").lower()

        def eng(s):
            return ((s.get("tags") or {}).get("language") or "").lower() in ("eng", "en")

        for s in subs:
            if (s.get("disposition") or {}).get("default") and any(k in ttl(s) for k in SIGNS):
                findings["signs_default"] += 1
                detail["signs_default"].append(name); break
        for s in auds:
            if (s.get("disposition") or {}).get("default") and any(k in ttl(s) for k in COMMENTARY):
                findings["commentary_default"] += 1
                detail["commentary_default"].append(name); break
            if (s.get("disposition") or {}).get("default") and s.get("codec_name") in LOSSLESS:
                findings["lossless_default"] += 1
                detail["lossless_default"].append(f"{s.get('codec_name'):<9} {name}"); break

        es = [s for s in subs if eng(s)]
        sidecar = os.path.exists(os.path.splitext(v)[0] + ".en.srt")
        if es and all(s["codec_name"] in BITMAP for s in es) and not sidecar:
            findings["bitmap_only"] += 1
            detail["bitmap_only"].append(name)
        if not es and not sidecar:
            findings["no_eng_sub"] += 1
            detail["no_eng_sub"].append(name)

        for s in d.get("streams", []):
            if s["codec_type"] == "video" and s.get("field_order") not in (None, "progressive", "unknown"):
                findings["interlaced"] += 1
                detail["interlaced"].append(f"{s.get('field_order'):<12} {name}"); break

    LABEL = {
        "signs_default":      "signs/songs subtitle set as DEFAULT (viewer sees no dialogue)",
        "commentary_default": "commentary AUDIO set as default",
        "bitmap_only":        "English subs are bitmap-only and no sidecar (burn-in -> transcode)",
        "interlaced":         "interlaced video (forces yadif -> transcode)",
        "lossless_default":   "lossless/DTS default audio (audio transcode on most clients)",
        "over_ceiling":       f"bitrate above {CEILING_MBPS} Mbps",
        "no_eng_sub":         "no English subtitles at all and no sidecar",
        "orphan_sidecar":     "sidecar .en.srt with no matching video",
    }
    print(f"=== library audit: {n} files ===\n")
    clean = True
    for k, lbl in LABEL.items():
        c = findings[k]
        if not c:
            print(f"  ok    {lbl}")
            continue
        clean = False
        print(f"  {c:5d} {lbl}")
        for x in detail[k][:6]:
            print(f"          {x}")
        if c > 6:
            print(f"          ... and {c - 6} more")
    print("\n  clean" if clean else "\n  see above; fix-track-flags.py repairs the flag issues")
    return 0


if __name__ == "__main__":
    sys.exit(main())
