#!/usr/bin/env python3
"""Audit Radarr/Sonarr filter settings against the agreed policy. Read-only.

Exits 0 when the live config matches, 1 on drift. Run by hand or from
media-stack-extract-subs.timer's sibling; it changes nothing.

WHY THESE VALUES: the Pi 5 has no video encoder (/dev/video19 is rpi-hevc-dec,
decode only), so any Jellyfin transcode is software libx264 -- measured 0.249x
real time, 0.111x with subtitle burn-in. A transcode is not "slow", it is
unplayable. Every rule below exists to keep files direct-playing.
"""
import json, subprocess, sys, urllib.request, urllib.error

APPS = {"radarr": 7878, "sonarr": 8989}


def key(app):
    return subprocess.run(
        ["sudo", "grep", "-oP", "(?<=<ApiKey>)[^<]+",
         f"/home/korn/media-stack/config/{app}/config.xml"],
        capture_output=True, text=True).stdout.strip()


def get(app, path):
    r = urllib.request.Request(f"http://127.0.0.1:{APPS[app]}{path}",
                               headers={"X-Api-Key": key(app)})
    with urllib.request.urlopen(r, timeout=60) as x:
        return json.loads(x.read().decode())


# ---- expected state -------------------------------------------------------
# (min, preferred, max) in MB/min. A 2h film is x120: 110 -> 13.0 GB / 14.7 Mbps.
SIZES = {
    "radarr": {"Bluray-1080p": (20, 95, 110),
               "WEBDL-1080p":  (20, 95, 100),
               "WEBRip-1080p": (20, 95, 100)},
    "sonarr": {"Bluray-1080p": (4, 95, 130)},
}

# HDR at -10000 sits below minFormatScore (-1000), so it is an effective ban:
# tone-mapping HDR->SDR is the most expensive transcode of all.
SCORES = {
    "radarr": {"Mobile 1080p": {
        "AV1": -500, "Hi10P": -500, "HEVC-x265": -300, "HDR-or-DV": -10000,
        "Lossless-Audio": -100, "Opus Audio": -50, "Multi-Subs": 100,
        "Text-Subs-Likely": 150, "English Audio": 300, "Chinese Audio": 400,
        "Dual Audio": 500}},
    "sonarr": {n: {
        "AV1": -500, "Hi10P": -500, "HEVC-x265": -300, "HDR-or-DV": -10000,
        "Lossless-Audio": -100, "Opus Audio": -50, "Multi-Subs": 100,
        "Text-Subs-Likely": 150, "English Audio": 300, "Chinese Audio": 400,
        "Dual Audio": 500} for n in ("Anime 1080p", "TV 1080p")},
}

# Remux anywhere is how a 35 Mbps file gets back in; 2160p cannot play at all.
BANNED = {"Any": {"Remux-1080p", "Remux-2160p", "Bluray-2160p", "BR-DISK",
                  "HDTV-2160p", "WEBDL-2160p", "WEBRip-2160p"},
          "HD-1080p": {"Remux-1080p"},
          "HD - 720p/1080p": {"Remux-1080p"},
          "Mobile 1080p": {"Remux-1080p", "Remux-2160p", "Bluray-2160p"}}

MEDIA = {"importExtraFiles": True, "extraFileExtensions": "srt,ass,ssa",
         "recycleBin": "/data/.recyclebin"}

fails = []


def check(cond, label, got=None, want=None):
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  DRIFT {label}: got {got!r}, want {want!r}")
        fails.append(label)


print("=== quality definition size ceilings ===")
for app, want in SIZES.items():
    for q in get(app, "/api/v3/qualitydefinition"):
        n = q["quality"]["name"]
        if n not in want:
            continue
        got = (q.get("minSize"), q.get("preferredSize"), q.get("maxSize"))
        check(got == want[n], f"{app} {n}", got, want[n])

print("\n=== custom format scores ===")
for app, profs in SCORES.items():
    live = {p["name"]: p for p in get(app, "/api/v3/qualityprofile")}
    for pname, want in profs.items():
        p = live.get(pname)
        if not p:
            check(False, f"{app} profile {pname}", "missing", "present"); continue
        got = {f["name"]: f["score"] for f in p.get("formatItems", [])}
        for fmt, sc in want.items():
            check(got.get(fmt) == sc, f"{app} {pname} / {fmt}", got.get(fmt), sc)
        check(p.get("minFormatScore") == -1000,
              f"{app} {pname} / minFormatScore", p.get("minFormatScore"), -1000)

print("\n=== banned qualities ===")
for p in get("radarr", "/api/v3/qualityprofile"):
    want = BANNED.get(p["name"])
    if not want:
        continue
    allowed = set()

    def walk(items):
        for i in items:
            q = i.get("quality")
            if q:
                if i["allowed"]:
                    allowed.add(q["name"])
            else:
                walk(i.get("items", []))
    walk(p["items"])
    bad = sorted(want & allowed)
    check(not bad, f"radarr {p['name']} bans remux/4K", bad or None, "none allowed")

print("\n=== media management ===")
for app in APPS:
    c = get(app, "/api/v3/config/mediamanagement")
    for k, v in MEDIA.items():
        check(c.get(k) == v, f"{app} {k}", c.get(k), v)

print()
if fails:
    print(f"FAIL: {len(fails)} setting(s) drifted from policy")
    sys.exit(1)
print("PASS: all filter settings match policy")
