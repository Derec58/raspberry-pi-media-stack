#!/usr/bin/env python3
"""Remove downloads that never started, blocklist them, and search again.

A torrent with zero reachable peers sits in qBittorrent's "downloading metadata"
state forever. Nothing in the stack gives up on it: Radarr's autoRedownloadFailed
only fires on a FAILED download, and a stalled one never fails. 13 of 15 downloads
sat at 0 bytes for 17 hours before anyone noticed.

Only items at EXACTLY zero bytes are touched, so a slow-but-live download is never
killed. Blocklisting matters as much as removing -- without it the next search can
pick the same dead release straight back.

Dry run by default.
"""
import argparse, sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/korn/media-stack/scripts")

import json, subprocess, urllib.request

APPS = {"radarr": (7878, "v3"), "sonarr": (8989, "v3")}


def key(app):
    return subprocess.run(["sudo", "grep", "-oP", "(?<=<ApiKey>)[^<]+",
        f"/home/korn/media-stack/config/{app}/config.xml"],
        capture_output=True, text=True).stdout.strip()


def call(app, method, path, timeout=90):
    port, _ = APPS[app]
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                               headers={"X-Api-Key": key(app),
                                        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            raw = x.read().decode()
            return x.status, (json.loads(raw) if raw.strip() else None)
    except Exception as e:
        return 0, str(e)[:200]


def qbit_states():
    """name -> (state, seeds, progress) straight from qBittorrent.

    Radarr reports a torrent queued behind qBittorrent's active-download limit
    identically to one with no peers: both sit at 0 bytes. Reaping on "0 bytes and
    old" would therefore kill perfectly healthy torrents that simply had not been
    given a slot yet. qBittorrent distinguishes them -- queuedDL vs stalledDL --
    so ask it directly and only ever reap stalledDL/metaDL.
    """
    out = subprocess.run(["sudo", "docker", "exec", "gluetun", "sh", "-c",
        'wget -qO- --timeout=10 "http://127.0.0.1:8080/api/v2/torrents/info" 2>/dev/null'],
        capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        ts = json.loads(out.stdout)
    except Exception:
        return None
    return {t["name"]: (t.get("state"), t.get("num_seeds", 0), t.get("progress", 0.0))
            for t in ts}


# Only these mean "qBittorrent is trying and getting nowhere". queuedDL means it
# has not started yet, which is not a stall.
DEAD_STATES = ("stalledDL", "metaDL", "missingFiles", "error")


def age_hours(added):
    if not added:
        return 0.0
    try:
        t = datetime.fromisoformat(added.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually remove (default: dry run)")
    ap.add_argument("--hours", type=float, default=2.0, help="stall age before reaping")
    args = ap.parse_args()

    if not args.apply:
        print("=== DRY RUN - nothing removed (pass --apply) ===")

    qb = qbit_states()
    if qb is None:
        print("  WARNING: qBittorrent unreachable; using conservative fallback")
    total = 0
    for app in APPS:
        st, q = call(app, "GET", "/api/v3/queue?pageSize=200&includeMovie=true&includeSeries=true")
        if st != 200 or not isinstance(q, dict):
            print(f"  {app}: queue unavailable ({st})")
            continue
        recs = q.get("records", [])
        dead = []
        for r in recs:
            hrs = age_hours(r.get("added"))
            if hrs < args.hours:
                continue
            st_, seeds, prog = (qb or {}).get(r.get("title", ""), (None, None, None))
            if qb is None:
                # qBittorrent unreachable: fall back to the old heuristic but only
                # for items far past the window, to avoid killing queued torrents.
                size = r.get("size") or 0
                if size - (r.get("sizeleft") or 0) <= 0 and hrs >= args.hours * 4:
                    dead.append((r, hrs, "no qbit; 0 bytes"))
                continue
            if st_ in DEAD_STATES and prog == 0.0:
                dead.append((r, hrs, f"{st_}, {seeds} seeds"))

        print(f"  {app}: {len(recs)} queued, {len(dead)} stalled >= {args.hours}h")
        for r, hrs, why in dead:
            title = ((r.get("movie") or r.get("series") or {}).get("title")
                     or r.get("title", "?"))
            print(f"      {hrs:5.1f}h  {title[:40]:<40} {why}")
            if not args.apply:
                continue
            st, _ = call(app, "DELETE",
                         f"/api/v3/queue/{r['id']}?removeFromClient=true&blocklist=true")
            if st not in (200, 202):
                print(f"              remove failed [{st}]"); continue
            # search again; the indexer seeder floor keeps the next pick alive
            mid, sid = r.get("movieId"), r.get("seriesId")
            if app == "radarr" and mid:
                _post(app, {"name": "MoviesSearch", "movieIds": [mid]})
            elif app == "sonarr" and sid:
                _post(app, {"name": "SeriesSearch", "seriesId": sid})
            total += 1

    if args.apply:
        print(f"\n  reaped and re-searched: {total}")
    return 0


def _post(app, body):
    port, _ = APPS[app]
    data = json.dumps(body).encode()
    r = urllib.request.Request(f"http://127.0.0.1:{port}/api/v3/command", data=data,
                               method="POST",
                               headers={"X-Api-Key": key(app),
                                        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60):
            return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
