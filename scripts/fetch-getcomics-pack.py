#!/usr/bin/env python3
"""Fetch a comic pack from a GetComics post URL, resumably and verifiably.

Why this exists
---------------
Mylar3 cannot fetch multi-issue packs from torrent indexers at all: pack search
is gated behind ENABLE_32P (mylar/search.py:706 forces allow_packs = False
unless the 32P private tracker is enabled). And its DDL search appends a
zero-padded issue number to every query, which GetComics' own search does not
match -- "mighty avengers 021" returns nothing while "mighty avengers 2007"
returns the complete run. So back catalogue sometimes has to be pulled by hand.

The naive way to do that loses data silently. A previous ad-hoc script looped on
read() and treated an empty read as end-of-stream; a dropped connection is
indistinguishable from EOF that way, so it reported success on a file that was
21.5% complete (540,352,807 of 2,517,918,266 bytes). Nothing downstream caught
it because there was no length check and no archive validation.

This script therefore refuses to report success unless the bytes on disk match
the length the server declared AND the file's own format checks out. Format is
sniffed from magic bytes, not the extension: GetComics serves everything as
application/octet-stream, and a .cbr is a RAR while a .cbz is a ZIP.

Runs inside the mylar3 container so it inherits HTTP_PROXY, which means the
download goes out over the VPN like every other comic fetch:

    docker cp scripts/fetch-getcomics-pack.py mylar3:/tmp/f.py
    docker exec mylar3 python3 /tmp/f.py <post-url> <output-path>
"""

import argparse
import html
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

UA = {"User-Agent": "Mozilla/5.0"}

# Mirror first: on the packs pulled so far it served the whole run as one file,
# where "Main Server" was split into per-volume parts.
LINK_PREFERENCE = ("mirror", "main server", "download now")


def fetch(url, headers=None, timeout=120):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def resolve_links(post_url):
    """Return [(anchor_text, dls_url)] from a GetComics post, best link first."""
    body = fetch(post_url, timeout=60).read().decode("utf8", "replace")
    found = []
    for m in re.finditer(
        r'<a[^>]+href="(https://getcomics\.org/dls/[^"]+)"[^>]*>(.*?)</a>', body, re.S
    ):
        text = html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        found.append((text, m.group(1)))

    def rank(item):
        label = item[0].lower()
        for i, want in enumerate(LINK_PREFERENCE):
            if want in label:
                return i
        return len(LINK_PREFERENCE)

    return sorted(found, key=rank)


PIXELDRAIN_RE = re.compile(r"^https://pixeldrain\.com/u/([A-Za-z0-9]+)")


def resolve_target(url):
    """Follow a /dls/ link to whatever actually serves bytes.

    GetComics mirrors are not equivalent. On the Infinity Comics posts the
    primary "DOWNLOAD NOW" link 403s from this VPN exit, MEGA lands on a
    JavaScript app that serves a 2KB HTML page instead of the file, and only
    Pixeldrain has a plain API. Pixeldrain's /u/ URL is a viewer page, so it has
    to be rewritten to /api/file/<id> or you download the HTML wrapper.
    """
    with fetch(url, timeout=60) as r:
        final = r.url
        ctype = r.headers.get("Content-Type", "")
    m = PIXELDRAIN_RE.match(final)
    if m:
        return f"https://pixeldrain.com/api/file/{m.group(1)}?download"
    if ctype.startswith("text/html"):
        # A viewer page we do not know how to unwrap (MEGA, and friends).
        return None
    return url


def content_length(url):
    with fetch(url, timeout=60) as r:
        return int(r.headers.get("Content-Length") or 0)


def supports_resume(url, offset):
    """True only on a real 206. A 200 means the server ignored Range."""
    if offset <= 0:
        return False
    try:
        with fetch(url, headers={"Range": f"bytes={offset}-"}, timeout=60) as r:
            return r.status == 206
    except urllib.error.HTTPError as e:
        # 416 = we already asked for more than exists; treat as no-resume so the
        # size check below decides what to do.
        return e.code == 206


# Magic bytes, checked rather than trusting the extension. GetComics serves
# everything as application/octet-stream, and a .cbr is a RAR while a .cbz is a
# ZIP, so the filename is the only hint and it is not always right.
MAGIC = {
    b"PK\x03\x04": "zip",
    b"Rar!\x1a\x07": "rar",
    b"7z\xbc\xaf\x27\x1c": "7z",
    b"%PDF": "pdf",
}


def sniff(path):
    with open(path, "rb") as fh:
        head = fh.read(8)
    for sig, kind in MAGIC.items():
        if head.startswith(sig):
            return kind
    return None


def download(url, out, expected, attempts=5):
    # A partial file is only resumable if it came from THIS url. Falling back to
    # a different mirror and resuming onto the previous mirror's leftovers
    # produces a file of exactly the right length that is still corrupt - which
    # happened here, appending a real archive onto a 2KB HTML error page. Any
    # legitimate partial starts with its format's magic bytes, so anything that
    # does not is junk from a previous attempt and gets discarded.
    if os.path.exists(out) and os.path.getsize(out) and sniff(out) is None:
        print(f"  discarding unusable partial ({os.path.getsize(out):,} bytes, "
              f"no recognisable header)")
        os.remove(out)

    for attempt in range(1, attempts + 1):
        have = os.path.getsize(out) if os.path.exists(out) else 0
        if expected and have == expected:
            return True
        if have > expected > 0:
            print(f"  local file larger than remote ({have} > {expected}), restarting")
            os.remove(out)
            have = 0

        resume = supports_resume(url, have)
        mode = "ab" if resume else "wb"
        if have and not resume:
            print("  server ignored Range, restarting from 0")
            have = 0

        headers = {"Range": f"bytes={have}-"} if resume else {}
        print(f"  attempt {attempt}: {'resuming at' if resume else 'starting from'} {have:,}")

        try:
            with fetch(url, headers=headers) as r, open(out, mode) as f:
                done, t0, last = have, time.time(), 0.0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if time.time() - last > 15:
                        last = time.time()
                        rate = (done - have) / 2**20 / max(time.time() - t0, 1)
                        pct = f"{done / expected * 100:5.1f}%" if expected else "  ?  "
                        print(f"  {done / 2**20:8.1f}/{expected / 2**20:.1f} MB {pct} {rate:5.2f} MB/s",
                              flush=True)
        except Exception as e:
            print(f"  transfer error: {type(e).__name__}: {e}")

        have = os.path.getsize(out) if os.path.exists(out) else 0
        if expected and have == expected:
            print(f"  size OK: {have:,} bytes")
            return True
        print(f"  short: {have:,} of {expected:,} bytes")
        time.sleep(min(5 * attempt, 30))

    return False


def validate(path):
    """Integrity, not just size. A short file that happens to end on a record
    boundary still passes a length check, so check the format itself."""
    kind = sniff(path)
    if kind is None:
        print(f"  unrecognised file type (first bytes are not zip/rar/7z/pdf)")
        return False

    if kind == "zip":
        try:
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
                if bad:
                    print(f"  archive CRC failure on entry: {bad}")
                    return False
                print(f"  archive OK: zip, {len(z.namelist())} entries")
                return True
        except Exception as e:
            print(f"  archive invalid: {type(e).__name__}: {e}")
            return False

    # RAR (.cbr) and 7z have no stdlib reader here. The length check already ran
    # and the header is right, which is as far as we can honestly verify.
    print(f"  file OK: {kind}, header valid, length matches")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("post_url", help="GetComics post URL")
    ap.add_argument("output", nargs="?", help="destination file path")
    ap.add_argument("--link-index", type=int, help="use the Nth resolved link instead of the best")
    ap.add_argument("--match",
                    help="pick the link whose served FILENAME matches this regex. More "
                         "robust than --link-index, which shifts when a post is edited.")
    ap.add_argument("--list-links", action="store_true", help="show links and exit")
    args = ap.parse_args()

    links = resolve_links(args.post_url)
    if not links:
        sys.exit("no /dls/ links found on that page")

    if args.list_links:
        for i, (text, url) in enumerate(links):
            print(f"  [{i}] {text[:24]:<24} {url[:80]}")
        return

    if not args.output:
        sys.exit("output path is required unless --list-links is given")

    if args.match:
        wanted = re.compile(args.match, re.I)
        matched = []
        for text, url in links:
            try:
                with fetch(url, timeout=60) as r:
                    name = urllib.parse.unquote(r.url.rsplit("/", 1)[-1])
            except Exception:
                continue
            if wanted.search(name):
                print(f"  match: {name[:70]}")
                matched.append((text, url))
        if not matched:
            sys.exit(f"no link served a filename matching {args.match!r}")
        candidates = matched
    elif args.link_index is not None:
        candidates = [links[args.link_index]]
    else:
        candidates = links

    for text, url in candidates:
        print(f"link [{text}]")
        try:
            target = resolve_target(url)
            if target is None:
                print("  serves an HTML viewer page, not a file; skipping")
                continue
            url = target
            expected = content_length(url)
        except Exception as e:
            print(f"  unreachable: {type(e).__name__}: {e}")
            continue
        if not expected:
            print("  no Content-Length, cannot verify completeness; skipping")
            continue
        print(f"  expecting {expected:,} bytes")

        if download(url, args.output, expected) and validate(args.output):
            print(f"OK {args.output}")
            return
        print("  falling back to next link")

    sys.exit("all links failed verification")


if __name__ == "__main__":
    main()
