#!/usr/bin/env python3
"""Audit the Mylar3 comics library for mis-filed and inconsistent downloads.

Written after a real incident on 2026-08-17: Mylar3 matched a GetComics pack
titled "Vision and the Scarlet Witch #1-12 (1985-1986)" against ComicVine
volume 3155, which is the 1982 four-issue series. It did that because the pack's
issue range (1-12) covers the target issues (1-4); the year mismatch did not
block it. Nothing stopped the wrong 349MB of comics from being filed into the
wrong series except catching it by hand.

Series with sibling volumes sharing a name are the exposure here, and this
reading order is full of them: four volumes called "Scarlet Witch", three called
"Vision and the Scarlet Witch", plus Avengers, New Avengers, Excalibur and
Young Avengers.

Checks performed:
  1. PACK-YEAR    a download's release year does not overlap the series year
  2. MISSING-FILE an issue is Downloaded but its file is not on disk
  3. UNTRACKED    a file sits in a series folder that no issue points at
  4. COUNT        a series reports more issues held than the volume contains
  5. STRAY-FOLDER a library folder no watched series points at, and which is
                  not in the deliberate-exceptions list

Exit status is 1 if anything was flagged, so it can gate a cron job.

Usage:
    scripts/verify-comics-library.py
    scripts/verify-comics-library.py --quiet   # only show problems
"""
import argparse
import os
import re
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MYLAR_DB = os.path.join(REPO, "config", "mylar3", "mylar", "mylar.db")

# Mylar3 sees the library as /data/Comics; this host sees the same bytes here.
CONTAINER_ROOT = "/data"
HOST_ROOT = "/mnt/jellyfin"

ARCHIVE_EXT = (".cbz", ".cbr", ".cb7")

COMICS_ROOT = os.path.join(HOST_ROOT, "Comics")

# Folders in the library that Mylar3 deliberately does not track. Collected
# editions live here: an Epic Collection is one volume, not thirteen numbered
# issues, so Mylar3 has nothing useful to say about it and the reading list
# addresses it by folder instead (see PLAN in sync-kavita-reading-lists.py).
# Anything in the library that is NOT tracked and NOT listed here is a stray.
UNTRACKED_OK = {
    "Avengers West Coast Epic Collection",
    "Avengers - Nights of Wundagore",
    # No ComicVine volume exists for this one-shot, so Mylar3 cannot track it.
    # Hand-fetched and addressed by folder, like the collected editions above.
    "Absolute Batman - Ark M Special (2026)",
}


def host_path(container_path):
    if container_path and container_path.startswith(CONTAINER_ROOT):
        return HOST_ROOT + container_path[len(CONTAINER_ROOT):]
    return container_path


def years_in(text):
    """Every 4-digit year mentioned in a string."""
    return {int(y) for y in re.findall(r"(?:19|20)\d{2}", str(text or ""))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the per-series OK lines")
    ap.add_argument("--db", default=MYLAR_DB,
                    help="path to mylar.db (used to self-test the checks)")
    ap.add_argument("--skip-disk", action="store_true",
                    help="only run database checks, do not stat the library")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"mylar db not found: {args.db}")
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    comics = {
        str(r["ComicID"]): r
        for r in db.execute(
            "SELECT ComicID, ComicName, ComicYear, ComicLocation, Have, Total "
            "FROM comics"
        )
    }

    problems = []

    # ---- 1. pack/release year vs the series' real publication span ---------
    # Comparing against ComicYear alone is useless: a 2024 series routinely runs
    # into 2025, which flagged healthy downloads. The volume's true span comes
    # from its ComicVine issue dates, widened by one year at each end to absorb
    # cover-date versus on-sale-date skew.
    spans = {}
    for cvid in comics:
        years = set()
        for i in db.execute(
            "SELECT IssueDate FROM issues WHERE ComicID=?", (cvid,)
        ):
            years |= years_in(i["IssueDate"])
        if not years:
            years = years_in(comics[cvid]["ComicYear"])
        if years:
            spans[cvid] = (min(years) - 1, max(years) + 1)

    for r in db.execute(
        "SELECT ID, series, year, comicid, issueid, status, pack "
        "FROM ddl_info WHERE status IN ('Completed','Post-Processed')"
    ):
        cvid = str(r["comicid"])
        comic = comics.get(cvid)
        span = spans.get(cvid)
        if comic is None or span is None:
            continue
        release_years = years_in(r["year"]) | years_in(r["series"])
        if not release_years:
            continue
        lo, hi = span
        # flag only when the release's whole year range sits outside the span
        if not any(lo <= y <= hi for y in release_years):
            problems.append(
                f"PACK-YEAR    cvid {cvid} '{comic['ComicName']}' publishes "
                f"{lo + 1}-{hi - 1} but was filled from a release dated "
                f"{r['year']}: \"{r['series']}\" [ddl id {r['ID']}]"
            )

    # ---- 1b. one release filling two different series ----------------------
    # The year check cannot separate volumes whose runs overlap. Scarlet Witch
    # Vol 3 (2023-2024) and Vol 4 (2024-2025) both number issues #1-10 and share
    # 2024, so a wrong match between them slips past a date comparison. But a
    # single GetComics page can only really belong to one volume, so the same
    # mainlink feeding two comicids is proof that one of them is wrong.
    by_link = {}
    for r in db.execute(
        "SELECT mainlink, comicid, series FROM ddl_info "
        "WHERE status IN ('Completed','Post-Processed') AND mainlink IS NOT NULL"
    ):
        by_link.setdefault(r["mainlink"], set()).add((str(r["comicid"]), r["series"]))
    for link, users in by_link.items():
        if len({cvid for cvid, _ in users}) > 1:
            names = ", ".join(
                f"cvid {cvid} ('{comics[cvid]['ComicName']}' {comics[cvid]['ComicYear']})"
                if cvid in comics else f"cvid {cvid}"
                for cvid, _ in sorted(users)
            )
            problems.append(
                f"DUP-LINK     one release filled several series: {names} "
                f"all came from {link}"
            )

    # ---- 1c. the same filename filed under two series ----------------------
    seen_file = {}
    for cvid, comic in comics.items():
        for i in db.execute(
            "SELECT Location FROM issues WHERE ComicID=? AND Location IS NOT NULL",
            (cvid,),
        ):
            seen_file.setdefault(i["Location"], set()).add(cvid)
    for location, cvids in seen_file.items():
        if len(cvids) > 1:
            names = ", ".join(
                f"cvid {c} ('{comics[c]['ComicName']}')" for c in sorted(cvids)
            )
            problems.append(
                f"DUP-FILE     '{location}' is claimed by several series: {names}"
            )

    # ---- 2 & 3. disk versus database ---------------------------------------
    for cvid, comic in sorted(comics.items(), key=lambda kv: kv[1]["ComicName"]):
        folder = host_path(comic["ComicLocation"])
        tracked = {}
        for i in db.execute(
            "SELECT Issue_Number, Status, Location FROM issues WHERE ComicID=?",
            (cvid,),
        ):
            if i["Location"]:
                tracked[i["Location"]] = (i["Issue_Number"], i["Status"])

        on_disk = set()
        if not args.skip_disk:
            if folder and os.path.isdir(folder):
                on_disk = {
                    name for name in os.listdir(folder)
                    if name.lower().endswith(ARCHIVE_EXT)
                }

            for location, (number, status) in sorted(tracked.items()):
                if status == "Downloaded" and location not in on_disk:
                    problems.append(
                        f"MISSING-FILE cvid {cvid} '{comic['ComicName']}' "
                        f"#{number} is Downloaded but '{location}' is not in "
                        f"{folder}"
                    )

            for name in sorted(on_disk - set(tracked)):
                problems.append(
                    f"UNTRACKED    cvid {cvid} '{comic['ComicName']}' has an "
                    f"unreferenced file: {name}"
                )

        # ---- 4. impossible counts -----------------------------------------
        try:
            have, total = int(comic["Have"] or 0), int(comic["Total"] or 0)
        except (TypeError, ValueError):
            have = total = 0
        if total and have > total:
            problems.append(
                f"COUNT        cvid {cvid} '{comic['ComicName']}' reports "
                f"{have} held of {total} total"
            )

        if not args.quiet:
            print(f"  checked {comic['ComicName']} ({comic['ComicYear']}): "
                  f"{len(on_disk)} file(s) on disk, {comic['Have']}/{comic['Total']} held")

    # ---- 5. whole folders nothing points at ------------------------------
    # The UNTRACKED check above only looks inside folders Mylar3 knows about, so
    # an entire stray folder - a pack extracted to the wrong place, a rename
    # gone wrong - is invisible to it. Catch that separately.
    if not args.skip_disk and os.path.isdir(COMICS_ROOT):
        known = set()
        for comic in comics.values():
            loc = host_path(comic["ComicLocation"])
            if loc:
                known.add(os.path.basename(loc.rstrip("/")))
        for name in sorted(os.listdir(COMICS_ROOT)):
            full = os.path.join(COMICS_ROOT, name)
            if not os.path.isdir(full) or name.startswith("."):
                continue
            if name in known or name in UNTRACKED_OK:
                continue
            n = sum(1 for f in os.listdir(full) if f.lower().endswith(ARCHIVE_EXT))
            problems.append(
                f"STRAY-FOLDER '{name}' holds {n} comic file(s) but no watched "
                f"series points at it"
            )

    print()
    if problems:
        print(f"{len(problems)} problem(s) found:\n")
        for p in problems:
            print(f"  {p}")
        print("\nPACK-YEAR is the dangerous one: it means content from the wrong")
        print("volume may have been filed into a series. Verify before reading,")
        print("and consider pinning that series' search term with a priority")
        print("alternate name, e.g. alt_search=!!Exact Release Title As Listed")
        return 1
    print("no problems found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
