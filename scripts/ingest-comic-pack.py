#!/usr/bin/env python3
"""Extract selected issues from a comic pack archive and hand them to Mylar3.

Why not just drop the pack in Mylar3's download folder
------------------------------------------------------
Because Mylar3 matches packs by issue RANGE and does not reliably check the
year. On 2026-08-17 a "Vision and the Scarlet Witch #1-12 (1985-1986)" pack was
filed onto ComicID 3155, the *1982* four-issue volume, purely because the pack's
range covered #1-4. This library has four series called "Scarlet Witch" and
three called "Vision and the Scarlet Witch", so the exposure is constant.

This script therefore never lets Mylar3 guess: it extracts only the issues asked
for, into their own staging directory, then calls forceProcess with an explicit
ComicID.

Two other traps it handles:

- Extracted files are owned by whoever ran the script. Mylar3 runs as PUID 112
  and cannot move or rename files it does not own, so ownership is fixed up
  before import. Run this with sudo.
- Mylar3 cannot parse date-style filenames such as
  "Series, 1982-12-00 (_04) (digital).cbz" - it reads 1982-12-00 as the issue
  number, matches nothing, and leaves the issues Snatched forever with no error
  line. Filenames are checked for a parseable issue number first, and the run
  stops rather than importing something that will hang silently.

Usage:
    sudo scripts/ingest-comic-pack.py \
        --archive "/mnt/jellyfin/downloads/manual-packs/Mighty Avengers (01-36).zip" \
        --comicid 18239 --issues 21-32
    ... add --dry-run to see the selection without extracting.
"""

import argparse
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MYLAR_URL = os.environ.get("MYLAR_URL", "http://localhost:8090")

PUID, PGID = 112, 122
HOST_STAGING = "/mnt/jellyfin/downloads/manual-packs"
CONTAINER_STAGING = "/data/downloads/manual-packs"

COMIC_EXT = (".cbz", ".cbr", ".cb7", ".pdf")

# "Mighty Avengers 021.cbr", "The Mighty Avengers 033 (2010) (Digital).cbr",
# "Avengers 500 (2004).cbz". Take the last standalone number that is not a year
# in parentheses, which is what distinguishes the issue from the release year.
#
# The decimal part is NOT optional to handle. Marvel ships half-issues, and
# "Avengers 001.5 (1999).cbr" parsed as plain integers yields 1 and 5, takes the
# last, and files a 1999 flashback special as issue #5 - on top of the real #5.
# That is a mis-attribution of exactly the kind the verifier exists to catch,
# produced before the verifier ever sees it.
ISSUE_RE = re.compile(r"(?<![\d(.])(\d{1,4}(?:\.\d+)?)(?![\d)])")
DATEY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Bare scene tags carrying a digit, which ISSUE_RE would otherwise read as
# the issue number. Matched only as whole words.
SCENE_TAG_RE = re.compile(r"(?<![\w-])(?:f?-?c2c|2c)(?![\w-])", re.I)


def mylar_api_key():
    """Read the API key from Mylar3's config. Needs root, like the rest of this."""
    if os.environ.get("MYLAR_API_KEY"):
        return os.environ["MYLAR_API_KEY"]
    path = os.path.join(REPO, "config", "mylar3", "mylar", "config.ini")
    with open(path) as fh:
        for line in fh:
            if line.startswith("api_key"):
                return line.split("=", 1)[1].strip()
    sys.exit(f"no api_key found in {path}")


def parse_issue(name):
    """Issue number from a filename, or None if it cannot be trusted."""
    base = os.path.splitext(os.path.basename(name))[0]
    if DATEY_RE.search(base):
        return None
    # Drop parenthesised groups (years, scanner tags) before looking for the number.
    stripped = re.sub(r"\([^)]*\)", " ", base)
    # Then drop bare scene tags that contain digits. "c2c" (cover-to-cover) is
    # the common one and it is genuinely dangerous: the number below is taken
    # from the LAST match, so "Giant-Size Avengers 005 (Duke-DCP) c2c" parsed
    # as issue 2 and would have filed #5 onto #2 silently. That is the same
    # mis-attribution class the verifier's DUP-FILE check exists to catch.
    stripped = SCENE_TAG_RE.sub(" ", stripped)
    matches = ISSUE_RE.findall(stripped)
    if not matches:
        return None
    value = float(matches[-1])
    return int(value) if value.is_integer() else value


def parse_ranges(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.append((int(lo), int(hi)))
        else:
            out.append((int(part), int(part)))
    return out


def wanted(issue, ranges):
    return issue is not None and any(lo <= issue <= hi for lo, hi in ranges)


def report(entries, ranges, issues_spec, name_filter=None, require_complete=False,
           assume_issue=None):
    """Shared selection reporting. Returns [(issue, name)] sorted."""
    if name_filter:
        keep = re.compile(name_filter, re.I)
        before = len(entries)
        entries = [n for n in entries if keep.search(os.path.basename(n))]
        print(f"name filter {name_filter!r}: {before} -> {len(entries)} file(s)")

    selected, unparseable = [], []
    for name in entries:
        issue = parse_issue(name)
        if issue is None and assume_issue is not None:
            if len(entries) != 1:
                sys.exit(f"--assume-issue needs exactly one source file, found "
                         f"{len(entries)}; refusing to guess which is which")
            issue = int(assume_issue) if float(assume_issue).is_integer() else assume_issue
            print(f"assuming issue {issue} for {os.path.basename(name)}")
        if issue is None:
            unparseable.append(name)
        elif wanted(issue, ranges):
            selected.append((issue, name))
    selected.sort()

    if unparseable:
        print(f"WARNING: {len(unparseable)} filename(s) have no parseable "
              f"issue number; Mylar3 would hang on these:")
        for n in unparseable[:5]:
            print(f"    {os.path.basename(n)}")

    if selected:
        got = [i for i, _ in selected]
        print(f"selected {len(selected)}: {got}")
        missing = [n for lo, hi in ranges for n in range(lo, hi + 1) if n not in got]
        if missing:
            print(f"WARNING: requested but not present: {missing}")
            if require_complete:
                sys.exit(f"--require-complete: {len(missing)} issue(s) missing from "
                         f"this source; refusing to file a partial run")
    return selected


def ingest_loose(args, ranges):
    """Files already on disk (individual issues fetched one at a time).

    Individual GetComics issue pages serve a bare .cbr, not an archive, so there
    is nothing to extract - the files just need selecting, owning correctly, and
    handing to Mylar3 with an explicit ComicID.
    """
    src = args.from_dir
    entries = [os.path.join(src, f) for f in sorted(os.listdir(src))
               if f.lower().endswith(COMIC_EXT)]
    print(f"{len(entries)} comic file(s) in {src}")

    selected = report(entries, ranges, args.issues, args.name_filter,
                      args.require_complete, args.assume_issue)
    if not selected:
        sys.exit(f"no files matched issues {args.issues}")
    if args.dry_run:
        for issue, name in selected:
            print(f"    {issue:>4}  {os.path.basename(name)}")
        return

    dest_name = args.dest_name or f"ingest-{args.comicid}-{args.issues}"
    dest = os.path.join(HOST_STAGING, dest_name)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for _, name in selected:
        shutil.copy2(name, os.path.join(dest, os.path.basename(name)))

    finalise(dest, len(selected))
    if not args.no_import:
        push(dest_name, args.comicid)


def finalise(dest, count):
    """Ownership must be 112:122 or Mylar3 (PUID 112) cannot move the files."""
    os.chown(dest, PUID, PGID)
    os.chmod(dest, 0o775)
    for entry in os.listdir(dest):
        path = os.path.join(dest, entry)
        os.chown(path, PUID, PGID)
        os.chmod(path, 0o664)
    print(f"staged {count} file(s) in {dest} (owned {PUID}:{PGID})")


def push(dest_name, comicid):
    """forceProcess lives on the API endpoint, not the web UI: /forceProcess
    returns 404. nzb_name is mandatory even though the folder is what matters,
    and the parameter is lowercase 'comicid' here (webserve.py uses 'ComicID')."""
    container_dir = os.path.join(CONTAINER_STAGING, dest_name)
    url = (f"{MYLAR_URL}/api?"
           + urllib.parse.urlencode({"apikey": mylar_api_key(),
                                     "cmd": "forceProcess",
                                     "nzb_name": dest_name,
                                     "nzb_folder": container_dir,
                                     "comicid": comicid}))
    print(f"forceProcess comicid={comicid} folder={container_dir}")
    with urllib.request.urlopen(url, timeout=120) as r:
        print(f"  HTTP {r.status}: {r.read().decode('utf8', 'replace').strip()[:120]}")


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--archive", help="a .zip/.cbz pack to extract from")
    src.add_argument("--from-dir", help="a directory of already-downloaded comic files")
    ap.add_argument("--comicid", required=True)
    ap.add_argument("--issues", required=True, help="e.g. 21-32 or 500-503")
    ap.add_argument("--name-filter",
                    help="only take files whose name matches this regex. Needed for "
                         "one-shot collections and anthologies, where every file is "
                         "issue #1 of a different series and the number cannot "
                         "disambiguate them.")
    ap.add_argument("--dest-name", help="staging subdirectory name")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-import", action="store_true",
                    help="extract and fix ownership but do not call Mylar3")
    ap.add_argument("--assume-issue", type=float,
                    help="issue number to use for files whose name carries none. "
                         "One-shots frequently ship as e.g. "
                         "'Avengers - The Children's Crusade - Young Avengers (2011).cbr' "
                         "with no number anywhere, which is otherwise unparseable. Only "
                         "safe when the source holds exactly one issue.")
    ap.add_argument("--require-complete", action="store_true",
                    help="exit non-zero if any requested issue is missing from the "
                         "source. A valid archive is not a complete one: a post "
                         "titled '#1 - 22' served only #1-11 and passed every CRC "
                         "check. Use for a single-source run; leave off when a run "
                         "spans several archives, where each legitimately reports "
                         "the others' issues as absent.")
    args = ap.parse_args()

    ranges = parse_ranges(args.issues)

    if args.from_dir:
        return ingest_loose(args, ranges)

    with zipfile.ZipFile(args.archive) as z:
        if z.testzip() is not None:
            sys.exit("archive fails CRC check; re-download before ingesting")
        entries = [n for n in z.namelist()
                   if n.lower().endswith(COMIC_EXT) and not n.endswith("/")]
        print(f"archive holds {len(entries)} comic file(s)")

        selected = report(entries, ranges, args.issues, args.name_filter,
                      args.require_complete, args.assume_issue)
        if not selected:
            sys.exit(f"no files matched issues {args.issues}")
        if args.dry_run:
            for issue, name in selected:
                print(f"    {issue:>4}  {os.path.basename(name)}")
            return

        dest_name = args.dest_name or f"ingest-{args.comicid}-{args.issues}"
        dest = os.path.join(HOST_STAGING, dest_name)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(dest)

        for _, name in selected:
            target = os.path.join(dest, os.path.basename(name))
            with z.open(name) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)

    finalise(dest, len(selected))
    if not args.no_import:
        push(dest_name, args.comicid)


if __name__ == "__main__":
    main()
