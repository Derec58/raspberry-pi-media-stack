#!/usr/bin/env python3
"""Search GetComics across name variants and report what actually exists.

Why this exists
---------------
Twice now, concluding "not available" from a single search was wrong and cost
real time. A 1980s team-book run was called unobtainable for two
days: searches were run for the series name and for individual issue numbers,
but never for the arc titles, under which both halves were sitting the whole
time as Epic Collections.

The failure mode is always the same shape. GetComics indexes a post by the title
its uploader chose, and that title may be the arc, the collected edition, the
writer, or a run range - rarely the thing you typed. So one query is a sample,
not an answer.

This runs a spread of variants and shows everything found, so absence means
absence rather than "I guessed the wrong noun".

Known title conventions, all of which have bitten:
  - apostrophes are dropped:  "Childrens Crusade", not "Children's Crusade"
  - "&" is spelled out:       "<Hero> and <Hero>"
  - separators are en dashes: "<Team> - The Trial Of <Villain>"
  - runs are ranges:          "#1 - 36 (2007-2010)"
  - a padded issue number matches nothing: "<series> 006" -> 0 results

Runs inside the mylar3 container so it inherits HTTP_PROXY and searches over the
VPN:
    docker cp scripts/find-comic-source.py mylar3:/tmp/find.py
    docker exec mylar3 python3 /tmp/find.py "<series name>" --year 2016
    docker exec mylar3 python3 /tmp/find.py "<series name>" --arc "<story arc>"
"""

import argparse
import html
import re
import sys
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0"}
SEARCH = "https://getcomics.org/?s="
TITLE_RE = re.compile(r'<h1 class="post-title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def clean(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def search(query, retries=3):
    url = SEARCH + urllib.parse.quote(query)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            body = urllib.request.urlopen(req, timeout=60).read().decode("utf8", "replace")
            return [(u, clean(t)) for u, t in TITLE_RE.findall(body)]
        except Exception as exc:
            if attempt == retries - 1:
                print(f"    ! {type(exc).__name__} on {query!r}", file=sys.stderr)
                return []
            time.sleep(3)
    return []


def variants(title, year=None, arc=None, issue=None):
    """Name forms worth trying, cheapest and most likely first."""
    base = title.strip()
    no_punct = re.sub(r"[^\w\s]", " ", base)
    no_punct = re.sub(r"\s+", " ", no_punct).strip()
    amp = base.replace("&", "and")
    no_the = re.sub(r"^the\s+", "", base, flags=re.I)

    out = [base]
    for v in (no_punct, amp, no_the):
        if v.lower() != base.lower():
            out.append(v)
    if year:
        out.append(f"{base} {year}")
    if arc:
        out.append(arc)
        out.append(f"{base} {arc}")
    # Collected-edition forms: the ones that found the 1980s team-book run.
    out.append(f"{base} epic collection")
    out.append(f"{base} tpb")
    out.append(f"{base} complete collection")
    if issue is not None:
        # Unpadded only. A padded number matches no GetComics title.
        out.append(f"{base} {issue}")

    seen, uniq = set(), []
    for v in out:
        k = v.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(v)
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("title", help="series title as you know it")
    ap.add_argument("--year", help="series year, added as one variant")
    ap.add_argument("--arc", help="story arc title - the variant that finds collected editions")
    ap.add_argument("--issue", help="a specific issue number (unpadded)")
    ap.add_argument("--filter", help="only show results matching this regex (case-insensitive)")
    ap.add_argument("--all", action="store_true", help="show every hit, not just filtered ones")
    args = ap.parse_args()

    keep = re.compile(args.filter, re.I) if args.filter else None
    found = {}

    for variant in variants(args.title, args.year, args.arc, args.issue):
        hits = search(variant)
        print(f"  {len(hits):>3} hits  {variant!r}")
        for url, title in hits:
            if keep and not args.all and not keep.search(title):
                continue
            found.setdefault(url, (title, []))[1].append(variant)

    print(f"\n{len(found)} distinct post(s):\n")
    for url, (title, vias) in sorted(found.items(), key=lambda kv: kv[1][0]):
        print(f"  {title}")
        print(f"     {url}")
        print(f"     found via: {', '.join(sorted(set(vias)))}")
        print()

    if not found:
        print("  nothing matched. Absence is only credible once several variants")
        print("  have been tried - consider --arc with the story arc's own title.")


if __name__ == "__main__":
    main()
