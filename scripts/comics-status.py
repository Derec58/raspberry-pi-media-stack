#!/usr/bin/env python3
"""Where is everything? One command, per reading-order item.

Why this exists
---------------
Answering "is the reading list actually complete?" used to mean hand-writing
SQL against mylar.db and kavita.db every time, which is exactly why nobody
noticed that reading list 01 had been empty for two days: its single issue had
never downloaded, and nothing surfaced that.

It also checks for DRIFT. The reading order lives in three places - the PLAN
table in sync-kavita-reading-lists.py, the Mylar3 watchlist, and the markdown
doc - and they can disagree silently. PLAN is treated as the source of truth
here: anything in it that Mylar3 is not watching is a gap, and anything Mylar3
watches that no PLAN segment references is either a mistake or something that
should be in the reading order.

Usage:
    scripts/comics-status.py              # per-item table plus drift
    scripts/comics-status.py --gaps       # only items that are incomplete
    scripts/comics-status.py --no-kavita  # skip the reading-list columns
"""

import argparse
import importlib.util
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collection_config  # noqa: E402  (needs the path above)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MYLAR_DB = os.path.join(REPO, "config", "mylar3", "mylar", "mylar.db")
KAVITA_DB = os.path.join(REPO, "config", "kavita", "kavita.db")
SYNC_SCRIPT = os.path.join(REPO, "scripts", "sync-kavita-reading-lists.py")

ARCHIVE_EXT = (".cbz", ".cbr", ".cb7")

# Watchlisted series that no PLAN segment references ON PURPOSE. Some team-book
# Avengers volumes are here because their issues exist only inside the Epic
# Collections, which PLAN addresses by Kavita folder instead. They stay in the
# watchlist so that if single scans ever surface, Mylar3 already knows the
# series - but they will never be referenced by CVID.
# Entries deliberately parked. They stay in PLAN because they are genuinely part
# of the reading order, but they are reported as deferred rather than as gaps, and
# do not make the exit status non-zero.
DEFERRED = {
    "164509": "DC All In 2025 FCBD Special Edition - a Free Comic Book Day "
              "giveaway; no scan exists on any mirror. Left Wanted so Mylar3 "
              "picks it up automatically if one ever appears.",
}

# Series watchlisted for a Kavita COLLECTION rather than the character
# reading order. They are legitimately absent from PLAN - a collection is a flat
# grouping with no reading order - so they must not be reported as drift, but
# they should still be visible here rather than silently ignored.
# Collected editions owned as volumes rather than tracked issue by issue, and the
# volumes deliberately absent from the reading plan. Both describe a personal
# collection, so they live in the gitignored collection-config.json rather than in
# source. See collection-config.example.json for the shape.
COLLECTIONS = collection_config.collections()
IN_COLLECTIONS = {cid for ids in COLLECTIONS.values() for cid in ids}

DRIFT_OK = collection_config.drift_ok()

CONTAINER_ROOT, HOST_ROOT = "/data", "/mnt/jellyfin"


def load_sync_module():
    """PLAN lives in a hyphenated filename, so it needs loading by path.

    That script holds personal collection data and is deliberately untracked, so
    a fresh clone will not have it. Fail with a clear message instead of an
    ImportError traceback that looks like a bug.
    """
    if not os.path.exists(SYNC_SCRIPT):
        sys.exit(f"reading-list script not present: {SYNC_SCRIPT}\n"
                 "It is kept out of version control because it contains personal "
                 "collection data. This report needs it to resolve the plan.")
    spec = importlib.util.spec_from_file_location("syncmod", SYNC_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.argv = [SYNC_SCRIPT]          # its argparse runs only under main()
    spec.loader.exec_module(mod)
    return mod


def read_only(path):
    if not os.path.exists(path):
        sys.exit(f"database not found: {path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def host_path(p):
    if p and p.startswith(CONTAINER_ROOT):
        return HOST_ROOT + p[len(CONTAINER_ROOT):]
    return p


def mylar_series():
    db = read_only(MYLAR_DB)
    db.row_factory = sqlite3.Row
    out = {}
    for c in db.execute("SELECT ComicID, ComicName, ComicYear, ComicLocation FROM comics"):
        cid = str(c["ComicID"])
        counts = {r[0]: r[1] for r in db.execute(
            "SELECT Status, COUNT(*) FROM issues WHERE ComicID=? GROUP BY Status", (cid,))}
        # Per-issue status, so a partial-range segment can be scored against the
        # issues it actually asked for. Counting whole-series downloads against a
        # range size reported nonsense like "14/4 held" for a #11-14 range.
        issues = []
        for num, status in db.execute(
                "SELECT Issue_Number, Status FROM issues WHERE ComicID=?", (cid,)):
            try:
                issues.append((float(str(num).strip()), status))
            except (TypeError, ValueError):
                issues.append((None, status))
        folder = host_path(c["ComicLocation"] or "")
        on_disk = 0
        if folder and os.path.isdir(folder):
            on_disk = sum(1 for f in os.listdir(folder)
                          if f.lower().endswith(ARCHIVE_EXT))
        out[cid] = {
            "name": c["ComicName"], "year": c["ComicYear"],
            "counts": counts, "issues": issues, "on_disk": on_disk,
            "folder": os.path.basename(folder.rstrip("/")) if folder else None,
        }
    return out


def kavita_lists():
    """reading list title -> item count, straight from Kavita's own tables."""
    if not os.path.exists(KAVITA_DB):
        return {}
    db = read_only(KAVITA_DB)
    out = {}
    for title, n in db.execute(
            """SELECT rl.Title, COUNT(i.Id) FROM ReadingList rl
               LEFT JOIN ReadingListItem i ON i.ReadingListId = rl.Id
               GROUP BY rl.Id"""):
        out[title] = n
    return out


def kavita_folders():
    if not os.path.exists(KAVITA_DB):
        return set()
    db = read_only(KAVITA_DB)
    return {os.path.basename((p or "").rstrip("/"))
            for (p,) in db.execute("SELECT FolderPath FROM Series") if p}


def span(ranges):
    if ranges is None:
        return "all"
    return ",".join(f"{lo}-{hi}" if lo != hi else str(lo) for lo, hi in ranges)


def in_ranges(num, ranges):
    if num is None:
        return ranges is None
    return ranges is None or any(lo <= num <= hi for lo, hi in ranges)


def score(issues, ranges, total):
    """(held, wanted_total, outstanding) for just the issues this segment covers."""
    scoped = [(n, st) for n, st in issues if in_ranges(n, ranges)]
    held = sum(1 for _, st in scoped if st == "Downloaded")
    outstanding = sum(1 for _, st in scoped if st in ("Wanted", "Snatched"))
    if ranges is None:
        want = total
    else:
        want = sum(hi - lo + 1 for lo, hi in ranges)
    return held, want, outstanding


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gaps", action="store_true", help="only show incomplete items")
    ap.add_argument("--no-kavita", action="store_true")
    args = ap.parse_args()

    sync = load_sync_module()
    plan = sync.PLAN
    prefix = sync.LIST_PREFIX
    folder_prefix = sync.FOLDER_PREFIX

    series = mylar_series()
    kfolders = set() if args.no_kavita else kavita_folders()
    klists = {} if args.no_kavita else kavita_lists()

    planned_cvids = set()
    gaps = []
    deferred = []

    for suffix, segments in plan:
        title = f"{prefix} {suffix}"
        header_shown = False
        for cvid, ranges in segments:
            if cvid.startswith(folder_prefix):
                folder = cvid[len(folder_prefix):]
                present = folder in kfolders
                line = (f"    {'ok ' if present else 'GAP'}  {folder[:44]:<44} "
                        f"{'collected edition':>17}  "
                        f"{'in Kavita' if present else 'NOT INDEXED'}")
                if not present:
                    gaps.append(f"{title}: {folder} not indexed by Kavita")
                if present and args.gaps:
                    continue
                if not header_shown:
                    print(f"\n{title}" + (f"   [{klists[title]} in list]" if title in klists else ""))
                    header_shown = True
                print(line)
                continue

            planned_cvids.add(cvid)
            if cvid in DEFERRED:
                deferred.append(f"{title}: {DEFERRED[cvid]}")
                continue
            s = series.get(cvid)
            if s is None:
                gaps.append(f"{title}: cvid {cvid} is in PLAN but NOT in the Mylar3 watchlist")
                if not header_shown:
                    print(f"\n{title}" + (f"   [{klists[title]} in list]" if title in klists else ""))
                    header_shown = True
                print(f"    GAP  cvid {cvid:<39} {span(ranges):>17}  NOT WATCHLISTED")
                continue

            total = sum(s["counts"].values())
            have, want, outstanding = score(s["issues"], ranges, total)
            complete = have >= want
            if complete and not outstanding:
                if args.gaps:
                    continue
                mark = "ok "
            else:
                mark = "GAP"
                gaps.append(f"{title}: {s['name']} ({s['year']}) "
                            f"{have}/{want} held, {outstanding} outstanding")
            if not header_shown:
                print(f"\n{title}" + (f"   [{klists[title]} in list]" if title in klists else ""))
                header_shown = True
            name = f"{s['name']} ({s['year']})"
            print(f"    {mark}  {name[:44]:<44} {span(ranges):>17}  "
                  f"{have}/{want} held, {s['on_disk']} on disk"
                  + (f", {outstanding} outstanding" if outstanding else ""))

    # ---- drift -----------------------------------------------------------
    print("\n" + "=" * 78)
    unreferenced = sorted(set(series) - planned_cvids - set(DRIFT_OK) - IN_COLLECTIONS,
                          key=lambda c: series[c]["name"] or "")
    if unreferenced:
        print(f"\n{len(unreferenced)} watchlisted series no PLAN segment references:")
        for cid in unreferenced:
            s = series[cid]
            print(f"    cvid {cid:<8} {s['name']} ({s['year']})  "
                  f"{s['counts'].get('Downloaded', 0)} downloaded")
        print("    Either add them to PLAN or remove them from the watchlist.")
    else:
        print("\nno drift: every watchlisted series is referenced by PLAN")

    for cname, cids in COLLECTIONS.items():
        held = sum(series[c]["counts"].get("Downloaded", 0) for c in cids if c in series)
        total = sum(sum(series[c]["counts"].values()) for c in cids if c in series)
        out = sum(series[c]["counts"].get("Wanted", 0) + series[c]["counts"].get("Snatched", 0)
                  for c in cids if c in series)
        print(f"\ncollection {cname!r}: {held}/{total} held across "
              f"{len([c for c in cids if c in series])} series"
              + (f", {out} outstanding" if out else ""))
        for c in cids:
            s_ = series.get(c)
            if not s_:
                print(f"    MISSING  cvid {c} is in COLLECTIONS but not watchlisted")
                continue
            cc = s_["counts"]
            dn, tt = cc.get("Downloaded", 0), sum(cc.values())
            mark = "ok " if dn >= tt and tt else "   "
            print(f"    {mark}  {s_['name'][:40]:<40} {dn}/{tt} held, {s_['on_disk']} on disk")

    if deferred:
        print(f"\n{len(deferred)} deferred (not counted as gaps):")
        for d in deferred:
            print(f"    {d}")

    if gaps:
        print(f"\n{len(gaps)} gap(s):")
        for g in gaps:
            print(f"    {g}")
    else:
        print("no gaps: every reading-order item is complete")

    if klists:
        total = sum(n for t, n in klists.items() if t.startswith(prefix))
        print(f"\nreading lists hold {total} issues across "
              f"{sum(1 for t in klists if t.startswith(prefix))} lists")

    return 1 if (gaps or unreferenced) else 0


if __name__ == "__main__":
    sys.exit(main())
