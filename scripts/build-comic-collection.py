#!/usr/bin/env python3
"""Repack loose comic files into a Kavita series with consistent metadata.

Why this exists
---------------
Kavita normally takes a series name from the parent folder, which is fine when a
run ships under one consistent title. It does not work for a set like the Marvel
Swimsuit Specials, where the 1991 issue is called "Marvel Illustrated: The
Swimsuit Issue", the 1992-95 run is "Marvel Swimsuit Special #1-4", and the
modern revivals carry subtitles instead of numbers. Left alone those land as
several unrelated series in a random order.

Writing ComicInfo.xml fixes it properly: Series groups them, Number orders them,
Title keeps each book's real name, and SeriesGroup drives the Kavita collection.

ComicInfo.xml cannot be added to a .cbr, because that is a RAR and nothing here
writes RAR. Those are converted to .cbz (a zip) on the way through, which is the
better format anyway - it can be verified with the stdlib.

Takes a JSON manifest:

    [{"src": "/path/Marvel Illustrated Swimsuit (1991).cbr",
      "number": 1, "year": 1991,
      "title": "Marvel Illustrated: The Swimsuit Issue #1",
      "summary": "Savage Land. Stark Enterprises summer games."}]

    sudo scripts/build-comic-collection.py --manifest m.json \\
        --series "Marvel Swimsuit Special" \\
        --collection "Marvel Swimsuit Specials" \\
        --dest "/mnt/jellyfin/Magazines/Marvel Swimsuit Special"
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

PUID, PGID = 112, 122
PAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


def sniff(path):
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    return None


HOST_ROOT, CONTAINER_ROOT = "/mnt/jellyfin", "/data"


def to_container(path):
    if not path.startswith(HOST_ROOT):
        return None
    return CONTAINER_ROOT + path[len(HOST_ROOT):]


def extract(src, into):
    """Unpack a .cbz or .cbr into a directory, whatever its extension claims.

    RAR needs care. The host's 7z is p7zip without RAR5 support and fails with
    "Unsupported Method" on every page of a modern .cbr. The mylar3 container has
    a real unrar, so RAR extraction is delegated there - which means both the
    source and the destination have to sit under /mnt/jellyfin, the one tree both
    sides can see.
    """
    kind = sniff(src)
    if kind == "zip":
        with zipfile.ZipFile(src) as z:
            z.extractall(into)
        return

    if kind != "rar":
        sys.exit(f"unrecognised archive: {src}")

    csrc, cinto = to_container(src), to_container(into)
    if not csrc or not cinto:
        sys.exit(f"RAR extraction needs paths under {HOST_ROOT}; got {src} -> {into}")
    r = subprocess.run(
        ["docker", "exec", "mylar3", "unrar", "x", "-y", "-idq", csrc, cinto + "/"],
        capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"unrar failed on {src}: {(r.stderr or r.stdout).strip()[:200]}")


def comicinfo(series, collection, entry, page_count):
    fields = [
        ("Series", series),
        ("Number", str(entry["number"])),
        ("Title", entry["title"]),
        ("Year", str(entry["year"])),
        ("SeriesGroup", collection),
        ("PageCount", str(page_count)),
        ("Publisher", entry.get("publisher", "Marvel")),
        ("Summary", entry.get("summary", "")),
        ("Notes", entry.get("notes", "")),
    ]
    body = "\n".join(f"  <{k}>{escape(v)}</{k}>" for k, v in fields if v)
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
            f"{body}\n</ComicInfo>\n")


def build(entry, series, collection, dest):
    scratch = os.path.join(HOST_ROOT, "downloads", ".collection-build")
    os.makedirs(scratch, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        os.chmod(tmp, 0o777)
        extract(entry["src"], tmp)
        pages = sorted(
            os.path.join(root, f)
            for root, _, files in os.walk(tmp)
            for f in files if f.lower().endswith(PAGE_EXT)
        )
        if not pages:
            sys.exit(f"no page images inside {entry['src']}")

        safe = entry["title"].replace("/", "-").replace(":", " -")
        out = os.path.join(dest, f"{series} {entry['number']:03d} - {safe} ({entry['year']}).cbz")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
            # ZIP_STORED, not DEFLATE: the pages are already JPEG, so compressing
            # them again costs CPU and saves nothing.
            for i, p in enumerate(pages):
                z.write(p, f"{i:04d}{os.path.splitext(p)[1].lower()}")
            z.writestr("ComicInfo.xml", comicinfo(series, collection, entry, len(pages)))

        if zipfile.ZipFile(out).testzip() is not None:
            sys.exit(f"built archive fails CRC: {out}")
        os.chown(out, PUID, PGID)
        os.chmod(out, 0o664)
        return out, len(pages)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--series", required=True)
    ap.add_argument("--collection", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    entries = sorted(json.load(open(args.manifest)), key=lambda e: e["number"])
    print(f"{len(entries)} entry(s) -> series {args.series!r}, collection {args.collection!r}")
    for e in entries:
        if not os.path.exists(e["src"]):
            sys.exit(f"missing source: {e['src']}")
        print(f"  {e['number']:>2}  {e['year']}  {e['title'][:52]:<52} {sniff(e['src'])}")
    if args.dry_run:
        return

    os.makedirs(args.dest, exist_ok=True)
    os.chown(args.dest, PUID, PGID)
    os.chmod(args.dest, 0o775)
    for e in entries:
        out, n = build(e, args.series, args.collection, args.dest)
        print(f"  built {os.path.basename(out)[:64]}  ({n} pages)")


if __name__ == "__main__":
    main()
