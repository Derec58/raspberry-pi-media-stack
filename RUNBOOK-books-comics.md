# Runbook: Ebook and Comic Automation

Covers the Shelfmark install, the Readarr retirement, and the Mylar3
configuration that replaces the originally planned Omnibus.

Last updated: 2026-08-17

---

## 1. What changed

| Change | Detail |
| --- | --- |
| Added | `shelfmark` container (ebooks + audiobooks search and download) |
| Removed | `bookshelf` container (this was Readarr, renamed) |
| Configured | `mylar3` (already installed, now verified end to end) |
| Not installed | Omnibus (no arm64 build exists, see section 8) |

### Why Omnibus was not installed

Omnibus requires three containers, and neither the web app
(`ghcr.io/hankscafe/omnibus`) nor the mandatory Rust engine
(`ghcr.io/hankscafe/omnibus-engine`) publishes a `linux/arm64` image on any
tag. Verified directly against the GHCR manifests, not assumed. This Pi is
`aarch64`, so Omnibus cannot run here without amd64 emulation, which is not
advisable for a three container app including a Rust engine.

Mylar3 covers the same requirement (monitored ongoing series, automatic
downloads of new issues) and was already installed. Kavita remains the reader,
which is the only thing Omnibus would have added.

Note: there is no `v1.2.0` of Omnibus. The newest published web app tag is
`v1.0.1-beta.010` and the newest engine tag is `v1.1.0-beta.084-unified`.
The project is still pre 1.0 stable and its container topology has already
changed once (tags moved from `-rust` to `-unified`). Worth rechecking for
arm64 support later, but it is beta software.

---

## 2. Ports and URLs

All services below are LAN only, consistent with the existing convention where
only Jellyfin and Jellyseerr are exposed externally through Nginx Proxy Manager.

| Service | URL | Purpose |
| --- | --- | --- |
| Shelfmark | http://<PI_LAN_IP>:8084 | Ebook and audiobook search/download |
| Mylar3 | http://<PI_LAN_IP>:8090 | Comic series monitoring and downloads |
| Kavita | http://<PI_LAN_IP>:5000 | Reader (books, comics, magazines) |
| Audiobookshelf | http://<PI_LAN_IP>:13378 | Audiobook player |
| qBittorrent | http://<PI_LAN_IP>:8080 | Download client (behind Gluetun) |
| Prowlarr | http://<PI_LAN_IP>:9696 | Indexer manager |
| Byparr | http://<PI_LAN_IP>:8191 | Cloudflare bypass (container name `flaresolverr`) |

Port 8787 (the old Readarr port) is now free.

---

## 3. Config file locations

| Item | Path |
| --- | --- |
| Compose file | `/home/korn/media-stack/docker-compose.yml` |
| Environment file | `/home/korn/media-stack/.env` |
| Shelfmark config | `/home/korn/media-stack/config/shelfmark/` |
| Mylar3 config | `/home/korn/media-stack/config/mylar3/mylar/config.ini` |
| Mylar3 database | `/home/korn/media-stack/config/mylar3/mylar/mylar.db` |
| Retired Readarr config | `/home/korn/media-stack/config/bookshelf/` (kept, not deleted) |
| Backups | `/home/korn/media-stack/backups/` (gitignored) |

### Path model

Every download-facing container mounts the same unified volume,
`/mnt/jellyfin:/data`. This is what makes hardlinks and atomic moves work
instead of slow cross-filesystem copies. Do not give a new container a
narrower mount such as `/mnt/jellyfin/Books:/books`, because that breaks
hardlinking from the download folder.

| Purpose | Host path | Container path |
| --- | --- | --- |
| Ebook destination | `/mnt/jellyfin/Books` | `/data/Books` |
| Audiobook destination | `/mnt/jellyfin/Audiobooks` | `/data/Audiobooks` |
| Comic destination | `/mnt/jellyfin/Comics` | `/data/Comics` |
| Completed downloads | `/mnt/jellyfin/downloads/completed/<category>` | same |
| Incomplete downloads | `/mnt/jellyfin/downloads/incomplete` | same |

Ownership everywhere is `PUID=112`, `PGID=122` (group `feedbackd`),
`TZ=America/Los_Angeles`. These come from `.env` and must not be changed
casually, since Jellyfin runs natively as uid 112 outside Docker.

---

## 4. Backup and rollback

Backups taken during this change, timestamp `20260817-050029`:

```
/home/korn/media-stack/backups/docker-compose.yml.20260817-050029.bak
/home/korn/media-stack/backups/.env.20260817-050029.bak
```

### Restore the compose file exactly as it was

```bash
cd /home/korn/media-stack
cp backups/docker-compose.yml.20260817-050029.bak docker-compose.yml
docker compose up -d --no-deps bookshelf     # bring Readarr back
docker stop shelfmark && docker rm shelfmark # remove Shelfmark
```

Readarr's config directory was never deleted, so restoring the compose file is
sufficient to bring it back with all of its history intact.

### Remove Shelfmark only

```bash
cd /home/korn/media-stack
docker stop shelfmark && docker rm shelfmark
# then delete the shelfmark service block from docker-compose.yml
```

Its config lives at `config/shelfmark/`. Deleting that directory is
irreversible, so leave it unless you are certain.

---

## 5. Shelfmark notes

Image is pinned to `ghcr.io/calibrain/shelfmark-lite:1.0.4`.

Two deliberate choices:

1. **The `-lite` variant, not the standard image.** Standard Shelfmark bundles
   its own Chromium to solve Cloudflare challenges, and upstream advises about
   2 GB of RAM for the container. Byparr is already running for exactly that
   job, so Lite points at it through `EXT_BYPASSER_URL` instead. Measured idle
   footprint is about 94 MB, versus a likely 1 to 2 GB for standard. On an 8 GB
   Pi already running 12 containers plus native Jellyfin, this matters.
2. **VPN egress via Gluetun's HTTP proxy, not `network_mode: service:gluetun`
   and not native WireGuard.** This matches how Prowlarr and Byparr already
   route. It avoids provisioning a second ProtonVPN WireGuard peer, and avoids
   the netns coupling that previously caused the qBittorrent EPERM interface
   binding problem.

Verified at install time: egress IP through the proxy was `146.70.8.27`
(ProtonVPN) while the host's own IP was `<WAN_IP>`, confirming traffic
does leave via the tunnel.

If Shelfmark ever needs to be reachable from outside the LAN, add an Nginx
Proxy Manager host and a DuckDNS subdomain. See section 9 first, because the
DuckDNS updater appears to be missing from this host.

---

## 6. Mylar3: monitored series and automatic downloads

This is the piece that replaces Omnibus.

### Scheduled jobs and cadence

Viewable in the UI, and stored in the `jobhistory` table of `mylar.db`.

| Job | Cadence | Controlled by | Purpose |
| --- | --- | --- | --- |
| Auto-Search | 24 h | `search_interval = 1440` (minutes) | Searches indexers for wanted or missing issues on monitored series. This is the main automation job. |
| Weekly Pullist | about 4 h | `[Weekly]` section | Refreshes the weekly comic release calendar. |
| DB Updater | 24 h | `dynamic_update` | Refreshes series metadata. |
| RSS Feeds | 20 min | `rss_checkinterval = 20` | Fast pickup of new releases. Enabled and resumed on 2026-08-17. |
| Folder Monitor | n/a | `enable_check_folder` | Paused. Not needed, qBittorrent hands off by label. |
| Check Version | n/a | | Paused. Harmless. |

To change a cadence: Settings, then Scheduler in the Mylar3 UI, or edit
`config.ini` and restart the container.

### DDL (GetComics) and why VPN routing is done at the container level

DDL was enabled on 2026-08-17 (`enable_ddl = True`, `enable_getcomics = True`,
`ddl_location = /data/downloads/completed/comics`). With DDL, **Mylar3 fetches
the comic files itself** instead of handing a torrent to qBittorrent. That
changes the threat model: before DDL, Mylar3 only talked to Prowlarr and
ComicVine, and all actual content moved through qBittorrent inside gluetun's
network namespace. With DDL, Mylar3 is the downloader.

Mylar3 has its own proxy setting (`enable_proxy`, `http_proxy`, `https_proxy`),
**but it is not sufficient**. Only 3 of the 24 modules that make outbound
requests reference it: `getcomics.py`, `pixeldrain.py` and `config.py`.
`mediafire.py`, which sits in the default `ddl_priority_order`, does not, and
in practice mediafire is what actually served the verified download.

The fix is proxy environment variables on the container itself. Both `requests`
and `urllib.request` read `HTTP_PROXY`/`HTTPS_PROXY` automatically, and nothing
in Mylar3 sets `trust_env = False`, so this covers every module rather than
three. See the `mylar3` block in `docker-compose.yml`.

Verify egress at any time. All three must return the ProtonVPN IP, never the
WAN IP:

```bash
docker exec mylar3 curl -s https://api.ipify.org
docker exec mylar3 python3 -c "import requests;print(requests.get('https://api.ipify.org',timeout=30).text)"
docker exec mylar3 python3 -c "import urllib.request;print(urllib.request.urlopen('https://api.ipify.org',timeout=30).read().decode())"
```

If any returns the WAN IP, stop and fix it before downloading anything.

Note this is proxy-based, not a kill-switch. If gluetun stops, the proxy
becomes unreachable and downloads fail rather than leaking, which is the
desired direction. For an absolute guarantee you would move mylar3 into
`network_mode: service:gluetun`, but that requires publishing 8090 on gluetun
and adding the docker subnet to `FIREWALL_OUTBOUND_SUBNETS`, which means
recreating gluetun and qBittorrent.

### RSS: two separate switches (important gotcha)

`Auto-Search` only runs once every 24 hours, so RSS is what gives 20 minute
pickup on new issues. Enabling it takes **two** steps, and doing only the first
looks like it worked but does nothing:

1. `enable_rss = True` in `config.ini` (or Settings in the UI). This registers
   the job.
2. **Resume the job**, because Mylar3 stores the paused state separately in the
   `jobhistory` table of `mylar.db`. Setting the config flag alone leaves the
   job sitting at Paused forever.

Both were done on 2026-08-17. To resume or force it later without the UI:

```bash
# resume
curl -sG --data-urlencode "job=RSS Feeds" --data-urlencode "mode=resume" \
  http://localhost:8090/jobmanage
# force an immediate run
curl -s "http://localhost:8090/schedulerForceCheck?jobid=rss"
```

Valid `jobid` values are `rss`, `search`, `weekly`, `updater`, `monitor`,
`version`. In the UI the same controls live under Manage, Job Management.

To read the true job state (the `jobhistory` DB table can lag), check which
button the UI offers: a job showing **Pause** is running, a job showing
**Resume** is paused.

### Marking a series monitored

1. Mylar3 UI, Comics, Add Series.
2. Search by name. Results come from ComicVine (API key already configured
   and verified working).
3. Pick the series, choose the destination directory (defaults to
   `/data/Comics`), and add it.
4. Set the series status to Active so new issues are wanted.
5. The release calendar lives under Pullist in the top navigation.

### Forcing a check without waiting

UI: Manage, then Job Management, then run `Auto-Search` (or `Weekly Pullist`).
Watch `docker logs -f mylar3` while it runs to confirm it queries indexers.

### File renaming (enabled 2026-08-17)

Scene releases carry inconsistent group tags and occasional typos. One arrived
as `Mystc Title 008` (misspelled) next to `Mystic Title 007`, and the
group tag flipped between `(Shan-Empire)` and `(Lil-Empire)` mid-run. Mylar3 now
normalises every filename from ComicVine metadata instead of keeping the release
name:

```
rename_files = True
file_format   = $Series $Annual $Issue ($Year)
zero_level    = True
zero_level_n  = 00x
```

Result: `Mystic Title 008 (2026).cbz`. `$Annual` is stripped automatically
for non-annuals, and expands to `Annual` for the Hero Solo Annual, so one
format string covers both.

Only `zero_level_n` is functional. `zero_level` is read solely to render the UI
checkbox (`webserve.py` references it nowhere else), so set both to keep the UI
honest. `00x` gives three-digit padding.

**config.ini must be edited with mylar3 stopped.** Mylar3 rewrites the whole
file from memory on shutdown, so edits made while it runs are discarded.

```bash
docker stop mylar3
sudo sed -i 's/^rename_files = False$/rename_files = True/' \
  config/mylar3/mylar/config.ini
docker start mylar3
```

To rename files already on disk, per series:

```bash
curl -s "http://localhost:8090/manualRename?comicid=<COMICVINE_ID>"
```

It matches on the exact filename stored in `issues.Location`, so it finds
misspelled files too, then calls `forceRescan` to resync the database. Verified
on 2026-08-17: 8 issues of Mystic Title and 2 of Hero Solo (2024)
renamed, Mylar3 still reporting 8/8 and 2/10 Downloaded afterwards.

**Do not trust the Preview Renamer.** The UI preview (`previewRename`) renders
a literal, unsubstituted `$Issue` and makes it look as though every file will
collapse onto one name. That is a bug in the preview path only:
`filers.rename_file` unpacks `helpers.issue_number_parser(...)` without
`pretty_string=True`, and the parser returns `None` for the string form unless
that flag is set. The real renamer (`helpers.rename_param`) passes the flag and
is correct. Confirm with the "Renaming X ... to ... Y" lines in
`docker logs mylar3` rather than the preview screen.

### Provider order: put DDL first (this is the main speed lever)

Changed 2026-08-17. `config.ini`:

```
provider_order = 0, DDL(GetComics), 1, 1337x (Prowlarr), 2, The Pirate Bay (Prowlarr)
```

Format is flat `index, name` pairs, sorted by index. Requires a restart.

Why it matters more than anything else here:

- **DDL is exempt from the search delay.** Only torznab and newznab providers
  pass through `check_the_search_delay`. Grep the logs for
  `PROVIDER-SEARCH-DELAY` and you will only ever see the Prowlarr entries.
- **A hit stops the provider loop.** `search_init` breaks out on
  `findit['status'] is True`, so a DDL hit means the two delayed torznab
  providers are never queried for that issue.
- **DDL is the only provider actually producing results.** Every single grab on
  2026-08-17 came from DDL(GetComics): Mystic Title #1-8, Hero Solo v4,
  both Hero Duo volumes. 1337x and The Pirate Bay returned
  nothing for comics all day.

With DDL last, every issue paid roughly 4 minutes of failing torznab queries
before DDL was even asked. With DDL first, an available issue resolves in
seconds.

### Search pace, and the one lever left

`search_delay` is the remaining cost, and it only applies to issues DDL does
*not* have. Budget ~3 minutes for each of those.

`SEARCH_DELAY` is typed `int` in `config.py`, so despite what
`check_the_search_delay` implies there is **no 30 second option**. The only
reachable values are:

| Value | Effect |
| --- | --- |
| `1` (current) | 60 seconds between queries to the same provider |
| `0` | no delay at all |

`0` is not recommended while both Prowlarr indexers are enabled: a full queue is
~500 queries, and firing those at public trackers with no spacing invites the
failure backoff described below (The Pirate Bay already returned 429 once on
2026-08-17).

If you want the queue to finish fast and accept losing the torrent fallback,
the better lever is to disable the two torznab entries in Mylar3 (Settings,
Search Providers) so misses fail instantly instead of costing 3 minutes.
Given they have produced zero comic results, this is mostly free, but it does
mean anything GetComics lacks will never be found.

Resources are **not** the bottleneck and shutting other containers down does not
help. Measured mid-queue: load average 0.42 on 4 cores, 4.2GB RAM available,
every container under 1% CPU. The delay is a `time.sleep()`.

### Series with sibling volumes: the mis-attribution trap

This is the most dangerous failure mode in this setup, and it happened for real
on 2026-08-17.

Mylar3 searched for **Hero Duo (1982)**, a 4 issue series,
and matched a GetComics pack titled **"Hero Duo #1-12
(1985-1986)"**, which is the *other* volume. It accepted it because the pack's
issue range (#1-12) covers the wanted issues (#1-4). The year mismatch did not
block it. 349MB of the wrong comics was queued to be filed into the wrong
series.

What saved it was luck: Vol 2 was next in the reading order, searched, found the
same pack, and post-processed it correctly to Vol 2. The Vol 1 copy was left
staged, still attributed to Vol 1.

This reading order is full of the same exposure: four volumes named "Hero Solo" (1994, 2016, 2023, 2024), three named "Hero Duo"
(1982, 1985, 2025), plus Avengers, New Avengers, Team Book and Young Avengers.
Hero Solo Vol 3 (2023-2024) and Vol 4 (2024-2025) are the worst pair: same
issue numbers #1-10 *and* overlapping years, so no date check can separate them.

**Detect it** with the verifier (see below). **Prevent it** with a priority
alternate search name.

### Priority alternate search names (`!!`)

Per-series, this pins Mylar3 to the exact release title a provider uses:

```bash
curl -G 'http://localhost:8090/comic_config' \
  --data-urlencode 'ComicID=<cvid>' \
  --data-urlencode 'com_location=/data/Comics/Hero Duo (1982)' \
  --data-urlencode 'alt_search=!!Hero Duo Vol.1' \
  --data-urlencode 'allow_packs=1'
```

The **`!!` prefix is the important part**. In `gen_altnames`, `AlternateSearch`
is scanned for `!!`; only then is the alternate queried *before* the real series
name. Without the prefix the primary name goes first and re-matches the wrong
release. `##` separates multiple alternates.

This fixed Vol 1: with `!!Hero Duo Vol.1` set, Mylar3
immediately found the correct `Hero Duo Vol.1 #1-4
(1982-1983)` pack (169MB) instead of the 1985 one.

Three cautions on `comic_config` (all confirmed by reading
`webserve.py:7131`, not guessed):

- It **resets any parameter you omit** to a default (`fuzzy_year` absent means
  `UseFuzzy=0`, `force_continuing` absent means 0, and so on). Read the series'
  current row first and pass anything you want to keep.
- **Always pass `allow_packs=1`.** Omitting it sets `AllowPacks=0`, which
  quietly stops Mylar3 grabbing multi-issue packs. Packs are how most back
  catalogue actually arrives here, so this would look like "searching found
  nothing" rather than like a setting change.
- **Pass the exact current `com_location`.** A different value is treated as a
  move and triggers a folder rename.
- `alt_search` must not be omitted, or it raises on `if '##' in alt_search`.

#### Measure an alternate name before you set it

This is the mistake worth avoiding: on 2026-08-17 three alternates were set from
what each series is *called*, and every one performed worse than the plain
series name. Count real results first, against the same torznab endpoint Mylar3
uses (the indexer ID and key come from `extra_torznabs` in `config.ini`):

```bash
docker exec mylar3 python3 -c "
import urllib.request, urllib.parse
for q in ['The Mighty Avengers', 'Mighty Avengers Vol. 1', 'Mighty Avengers']:
    u = 'http://prowlarr:9696/8/api?t=search&cat=7030&apikey=<KEY>&q=' + urllib.parse.quote(q)
    print(repr(q), urllib.request.urlopen(u, timeout=30).read().decode('utf8','replace').count('<item>'))
"
```

Measured output was `8`, `0`, `42`. Three patterns behind that:

- Scene releases **drop the leading "The"**.
- `Vol. 1`, `(Complete)` and similar qualifiers appear in no release title and
  only narrow the query. `Avengers Disassembled` returned 7 where
  `Avengers Disassembled (Complete)` returned 2.
- **Word order can be reversed** from the ComicVine title. The real release is
  `Origins- Speedster and the Hero Solo 01`, while the series is
  catalogued as "Origins: Hero Solo & Speedster".

Shorter, and closer to how a release is actually named, wins. An alternate that
returns fewer hits than the plain name is worse than no alternate at all, and it
costs double the search time on every miss.

Find the exact title a provider uses by searching it directly through the VPN:

```bash
docker exec mylar3 sh -c "curl -s -x http://gluetun:8888 \
  'https://getcomics.org/?s=vision+and+the+scarlet+witch'" \
  | grep -oiE 'Hero [&a-z ]*Duo[^<\"]{0,40}' | sort -u
```

### Verifying the library: scripts/verify-comics-library.py

```bash
scripts/verify-comics-library.py           # full audit
scripts/verify-comics-library.py --quiet   # problems only
```

Exits 1 if anything is flagged, so it can gate a cron job. Checks:

| Code | Meaning |
| --- | --- |
| `PACK-YEAR` | a release's year sits outside the series' real publication span |
| `DUP-LINK` | one GetComics page filled two different series (proof one is wrong) |
| `DUP-FILE` | the same filename is claimed by two series |
| `MISSING-FILE` | an issue is Downloaded but its file is not on disk |
| `UNTRACKED` | a file in a series folder that no issue points at |
| `COUNT` | a series reports holding more issues than the volume contains |

`PACK-YEAR` compares against the span of the volume's actual ComicVine issue
dates, widened a year each way, **not** against `ComicYear`. Comparing to
`ComicYear` alone produced false positives immediately: a 2024 series routinely
ships issues in 2025.

`DUP-LINK` exists because the year check cannot separate overlapping volumes
like Hero Solo Vol 3 and Vol 4. One GetComics page belongs to one volume, so
if it fed two comicids, one is misfiled.

Self-test the checks against a scratch copy rather than live data:

```bash
scripts/verify-comics-library.py --db /tmp/copy.db --skip-disk
```

All checks were validated this way by re-injecting the real 2026-08-17 incident
and confirming each fires.

### Packs whose filenames Mylar3 cannot parse

The correct Vol 1 pack downloaded, then post-processing silently matched
nothing. Its files were named by date, with the issue number hidden in
parentheses:

```
Hero Duo, 1982-12-00 (_04) (digital) (OkC.O.M.P.U.T.O.-Novus-HD).cbz
```

Mylar3 reads `1982-12-00` as the number, fails, logs only `this should have an
issue number to match to this particular series`, and leaves the issues
`Snatched` forever. **There is no error line, so this looks like a hang.**

Fix by renaming to something parseable, then re-running post-processing. Interim
names only need to parse, because `rename_files = True` rewrites them anyway:

```bash
cd "/mnt/jellyfin/downloads/completed/comics/<pack folder>/<inner folder>"
for f in *.cbz; do
  n=$(echo "$f" | grep -oP '\(_\K[0-9]{2}(?=\))')
  sudo mv -n "$f" "Hero Duo ${n} (1982).cbz"
done

KEY=$(sudo grep -oP '(?<=^api_key = ).*' config/mylar3/mylar/config.ini)
curl -G http://localhost:8090/api \
  --data-urlencode "apikey=$KEY" --data-urlencode "cmd=forceProcess" \
  --data-urlencode "nzb_name=<pack folder>" \
  --data-urlencode "nzb_folder=/data/downloads/completed/comics/<pack folder>"
```

Mylar3 then filed all 4 correctly and applied the right ComicVine years
(001-002 as 1982, 003-004 as 1983).

**`issueProcess` does not exist.** This runbook previously claimed
`cmd=issueProcess` (with `comicid`, `issueid` and `folder`) would force one file
to one issue. It returns:

```
{"success": false, "error": {"code": 460, "message": "Unknown command: issueProcess"}}
```

Corrected 2026-08-20 against the live API.

#### What to do instead when one issue will not post-process

`forceProcess` silently does nothing for an issue Mylar3 has already *snatched*
elsewhere: it logs `Now checking: <Series> [<id>]` and then stops, with no
DUPECHECK line, no error, and no completion line. Real example — Uncanny
Avengers #1 sat at 24/25 while its file was staged and healthy, because Mylar3's
own DDL had snatched the same issue as part of a pack.

The reliable route is to bypass post-processing entirely: put the file in the
series folder under the name `file_format` would have produced, fix ownership,
then force a rescan.

```bash
DEST="/mnt/jellyfin/Comics/<Series> (<Year>)/<Series> 001 (<Year>).cbr"
sudo cp "<staged file>" "$DEST"
sudo chown 112:122 "$DEST"; sudo chmod 664 "$DEST"
curl -s "http://localhost:8090/forceRescan?ComicID=<comicid>"
```

`forceRescan` counts what is physically present and flips the issue to
`Downloaded`. It took Uncanny Avengers (2012) from 24/25 to 25/25 immediately.
Note `forceRescan` is a **web route**, not an API command, so it takes
`ComicID` (capitalised) and no api key.

### Marking a precise set of issues Wanted (partial runs)

`changeStatus` in the API is whole-series only. For a partial run use the web
endpoint `markissues` instead:

```bash
curl -X POST http://localhost:8090/markissues \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-binary 'action=Wanted&issueids[]=12345&issueids[]=12346'
```

It marks exactly those issue ids and spawns one background thread
(`search.searchIssueIDList`) that searches them **in the order supplied**, so
submit one ordered batch rather than several. `queueIssue` is the per-issue
alternative but searches synchronously on every call.

Trap: `action=Wanted` skips issues already Wanted, but **not** ones already
Downloaded. Filter to `Status='Skipped'` first or completed issues get
re-queued and re-downloaded.

Pace: `search_delay = 1` means a 60 second wait between provider queries, times
three providers, times two query forms (series-level and issue-level). Budget
roughly 4 minutes per issue. A 143 issue queue takes 9 to 10 hours. Slow is
normal here; check for progress with:

```bash
docker logs mylar3 --since 10m 2>&1 | grep -oE "looking for [^(]*\(" | sort -u
```

### Kavita reading lists

Six reading lists mirror the reading-order categories. Refresh them with:

```bash
scripts/sync-kavita-reading-lists.py --dry-run
scripts/sync-kavita-reading-lists.py
scripts/sync-kavita-reading-lists.py --no-scan   # skip the library scan
```

Re-run as downloads land; each run rebuilds every list so late arrivals are
inserted in the right position rather than appended. Verified on 2026-08-17:
Hero Duo Vol 1 arrived after Vol 2 was already listed, and
the next run placed Vol 1's #1-4 ahead of Vol 2's #1-12 correctly. Idempotent,
and list ids stay stable. Details in `READING-ORDER-scarlet-witch.md`. Key is
`KAVITA_API_KEY` in `.env`.

Kavita 0.9.x API gotchas, all found the hard way:

- Swagger is **disabled** in release builds, so `/swagger/v1/swagger.json` 404s.
  Endpoints have to be probed (a 400 means it exists, 404 means it does not).
- Listing reading lists is `POST /api/ReadingList/lists` with a JSON body. A GET
  404s.
- Auth is `POST /api/Plugin/authenticate?apiKey=...&pluginName=...`, returning a
  JWT for `Authorization: Bearer`.
- `POST /api/ReadingList/update-by-multiple` preserves the order of the
  `chapterIds` array, which is what makes reading order expressible.
- Removing items is `POST /api/ReadingList/delete-item` (not DELETE).

**The folder watcher cannot be trusted, and `force=false` scans lie.** Two
separate problems, both observed on 2026-08-17:

1. With `FolderWatching` enabled, the Comics library went **19 minutes** without
   noticing 12 new files.
2. `POST /api/Library/scan?libraryId=2&force=false` returns **200 while doing
   nothing at all** - `LastScanned` never moved across repeated calls, then
   `force=true` indexed everything within seconds.

So the sync script always scans with `force=true`. It also does not wait on
`Library.LastScanned`, because a library Kavita declines to scan never updates
it (Magazines, which has no valid folder, never has). It instead polls
`Series`/`Chapter`/`MangaFile` row counts until they stop changing, with a
minimum floor of 40s so an index that has not started writing yet is not
mistaken for a finished scan.

If files are on disk but Kavita will not show them:

```bash
T=$(curl -s -X POST "http://localhost:5000/api/Plugin/authenticate?apiKey=$KAVITA_API_KEY&pluginName=cc" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -X POST 'http://localhost:5000/api/Library/scan?libraryId=2&force=true' \
  -H "Authorization: Bearer $T"
```

### Kavita names series from the folder, not the filename

Worth knowing before chasing filename bugs: Kavita took the typo'd
`Mystc Title 008` and still filed it under `Mystic Title (2026)`,
because the series name comes from the parent folder (`folder_format =
$Series ($Year)`). Filename typos affect only the issue number parse, not
series grouping. Renaming is still worth doing for consistency and correct
issue ordering, but a scene-release typo will not split a Kavita series as
long as the folder name is right.

After a rename, Kavita's folder watcher picks up the new paths on its own.
Measured lag on 2026-08-17: under one minute for one series, roughly 90
seconds for the other. No manual scan needed.

### 6.9 Manual pack ingestion (when Mylar3 structurally cannot get it)

Some back catalogue Mylar3 will never fetch, no matter how the search is tuned.
Two independent reasons, both confirmed by reading the source rather than
guessing:

1. **Pack search is gated behind the 32P private tracker.** From
   `mylar/search.py:706`:

   ```python
   if any([allow_packs == 1, allow_packs == '1']) and all(
       [mylar.CONFIG.ENABLE_TORRENT_SEARCH, mylar.CONFIG.ENABLE_32P]
   ):
       allow_packs = True
   else:
       allow_packs = False
   ```

   `enable_32p = False` here (no account), so the per-series `AllowPacks=1`
   setting is overridden to `False` on **every** torznab search. A pack sitting
   on The Pirate Bay is unreachable, however well the series name matches.

2. **DDL queries append a padded issue number that GetComics cannot match.**
   Measured: `mighty avengers 021` returns **0** results while
   `mighty avengers 2007` returns the complete run. So DDL misses complete-run
   packs even though the site is hosting them.

Together these explain a series that reports "Could not find Issue N" forever
while the issues are plainly available. **Do not keep retuning `alt_search` when
you see this; go and fetch it by hand.**

#### Fetching

`scripts/fetch-getcomics-pack.py` resolves a GetComics post URL to its download
links, resumes, and refuses to report success on a short file:

```bash
docker cp scripts/fetch-getcomics-pack.py mylar3:/tmp/fetchpack.py

# See what the post offers before committing to a 2.5GB download
docker exec mylar3 python3 /tmp/fetchpack.py --list-links '<post-url>'

# Fetch one specific link
docker exec mylar3 python3 /tmp/fetchpack.py --link-index 25 \
    '<post-url>' '/data/downloads/manual-packs/<name>.zip'
```

It runs **inside the mylar3 container** so it inherits `HTTP_PROXY` and the
download leaves over the VPN like every other comic fetch.

**Check `--list-links` first, and resolve the parts before downloading.** A post
titled "Avengers Vol. 3 #0-84 + 500-503" offered one 2.5GB mirror *and* eight
part files, one of which was `Avengers Vol. 3 500-503 (2004).zip` at 178MB. Same
four issues, 14x less traffic. The anchor text does not say which part is which,
so resolve each link and read the filename off the redirect.

**Why the verifying downloader exists.** An earlier ad-hoc script looped on
`read()` and treated an empty read as end-of-stream. A dropped connection is
indistinguishable from EOF that way, so it printed `DONE` and exited 0 on a file
that held 540,352,807 of 2,517,918,266 bytes. Nothing downstream noticed,
because there was no length check and no archive test. Always verify both:

```python
bytes_written == int(resp.headers['Content-Length'])   # completeness
zipfile.ZipFile(path).testzip() is None                # integrity
```

Note the GetComics CDN **ignores `Range`** and replies 200 to a resume request,
so a failed transfer restarts from zero. The script detects this and says so
rather than silently appending to a partial file and producing a corrupt
archive.

#### Ingesting

`scripts/ingest-comic-pack.py` extracts only the wanted issues and imports them
against an explicit ComicID:

```bash
python3 scripts/ingest-comic-pack.py --dry-run \
    --archive '/mnt/jellyfin/downloads/manual-packs/<name>.zip' \
    --comicid <cvid> --issues 21-32          # always dry-run first

sudo python3 scripts/ingest-comic-pack.py \
    --archive '/mnt/jellyfin/downloads/manual-packs/<name>.zip' \
    --comicid <cvid> --issues 21-32
```

Four things it handles that will bite anyone doing this by hand:

- **Never drop a pack in `ddl_location` and let Mylar3 match it.** Mylar3 matches
  packs by issue *range* and does not reliably check the year: that is how a
  1985 pack got filed onto the 1982 volume. Staging lives at
  `/mnt/jellyfin/downloads/manual-packs/`, deliberately outside the watched
  folder, and import is always by explicit ComicID.
- **`sudo` is required**, because extracted files must end up owned `112:122`.
  Mylar3 runs as PUID 112 and cannot move or rename files it does not own.
- **Filenames must contain a parseable issue number.** A date-style name such as
  `Series, 1982-12-00 (_04).cbz` makes Mylar3 read the date as the issue number,
  match nothing, and leave the issue `Snatched` forever with no error line. The
  script refuses to import those rather than letting them hang.
- **`forceProcess` is an API command, not a web route.** `/forceProcess` returns
  404. The working call is:

  ```bash
  curl -sG 'http://localhost:8090/api' \
    --data-urlencode "apikey=$(grep '^api_key' config/mylar3/mylar/config.ini | cut -d= -f2 | tr -d ' ')" \
    --data-urlencode 'cmd=forceProcess' \
    --data-urlencode 'nzb_name=<staging dir name>' \
    --data-urlencode 'nzb_folder=/data/downloads/manual-packs/<staging dir name>' \
    --data-urlencode 'comicid=18239'
  ```

  `nzb_name` is mandatory even though the folder is what matters, and the
  parameter is lowercase `comicid` on the API where `webserve.py` uses `ComicID`.

On success Mylar3 renames each file to `file_format`, moves it into the series
folder, drains the staging directory, and flips the issues to `Downloaded`.

### 6.10 Kavita scanning: scan one library at a time

Kavita runs **one scan globally**. A second scan requested while one is running
is not queued, it is pushed out by three hours:

```
Enqueuing library scan for: 1
A Scan is already running, rescheduling ScanLibrary in 3 hours
```

Firing every library in a loop therefore scans the first and silently defers the
rest. This is what produces the user-visible **"Scan Library task delayed"**
message, and it is why newly imported issues can sit unindexed for hours while a
sync script reports them as "not indexed by Kavita yet".

`scripts/sync-kavita-reading-lists.py` now:

- scans **sequentially**, waiting for the index to settle between libraries,
  Comics first since it is the only one that normally changes;
- **skips libraries with no files on disk.** Scanning the empty Magazines
  library is what generates *"Some of the root folders for the library,
  magazines, are empty"*. Note this only stops *this script* from triggering
  it. Kavita's own scan task is set to `daily` (`ServerSetting` key 0) and
  scans every library including Magazines, so the warning will still appear
  roughly once a day until the library is removed. The user chose to keep it;
  that is the accepted cost;
- **skips the scan entirely when no library's file count changed**, tracked in
  `logs/.kavita-sync-state.json`. A quiet cycle now takes about two seconds and
  does not touch Kavita at all. Override with `--force-scan`.

Diagnose scan problems from Kavita's own log, not from the API response, which
returns 200 either way:

```bash
docker logs kavita --since 20m 2>&1 | grep -E "Enqueuing|already running|Finished library scan"
```

#### The settle detector can stop early, and the sync then lies quietly

Found 2026-08-21, ingesting 13 issues for reading-order entries 03 and 08.
`force_scan()` decides the index has settled after three consecutive polls at
the same chapter count, with a floor and a timeout. Kavita's scan does not
always grow that count monotonically: it went quiet at **531** chapters long
enough for three polls to agree, the script declared the scan finished, and the
sync then resolved the **pre-ingest** counts — chapter 01 at 4 instead of 8,
chapter 02 at 60 instead of 69. About a minute later the count climbed to the
expected 544 on its own.

The dangerous part is that **nothing fails**. There is no warning, no non-zero
exit, no `pending:` line for the affected series. The sync reports success and
rebuilds every list with content that is simply out of date. The only signal is
a resolved count that does not match what you expect, which is why the reading
order records expected per-list totals.

So after any ingest, confirm the index reached the expected total *before*
trusting a sync:

```bash
# 13 files ingested, so the count must rise by 13 before syncing
sudo python3 -c "
import sqlite3
db=sqlite3.connect('file:/home/korn/media-stack/config/kavita/kavita.db?mode=ro',uri=True)
print(db.execute('SELECT COUNT(*) FROM Chapter').fetchone()[0])"
```

Re-running the sync is always safe and is the fix: it rebuilds the lists from
whatever Kavita currently holds. The second run reports
`no library changes on disk; skipping scan` and resolves the right numbers.

### 6.11 Pre-flight checklist

Every significant delay on this project traced to the same habit: acting on an
assumption that a cheap check would have settled. Each line is here because
skipping it cost real time.

1. **Read the source before tuning behaviour.** If a setting is not doing what
   its name suggests, find where it is consumed. Hours went into `alt_search`
   names against a wall no name could pass, while `ENABLE_32P` gating pack
   search sat visible in `search.py:706` the whole time.
2. **Measure with the real input.** Reproduce the exact query the system issues.
   Measuring with a bare series name produced a confident "42 hits" for a query
   Mylar3 never sends; the real padded query returns zero.
3. **Read a value before overwriting it,** and write the old value down.
   `max_active_torrents` was changed live without recording it, and could only
   be restored to an inferred default.
4. **Never conclude absence from one probe.** Vary the name: series title, arc
   title, collected-edition title, punctuation stripped. The Regional Team
   run was called unobtainable for two days; both halves were sitting there as
   Epic Collections under their arc names. `scripts/find-comic-source.py`
   automates this.
5. **Verify transfers two ways** - declared length *and* the format's own
   integrity check. See 6.9.
6. **Check the service's own log,** not the API status code. Kavita returns 200
   whether it scans, defers, or ignores.
7. **List the cheap option before taking the expensive one.** Whole-run packs
   usually have per-part siblings on the same page: 178 MB instead of 2.5 GB for
   the same four issues.

### 6.12 Mirror quirks, and a resume trap

GetComics mirrors are **not** interchangeable. On the Infinity Comics posts:

| Mirror | Behaviour |
| --- | --- |
| `DOWNLOAD NOW` | 403 from this VPN exit, or intermittent SSL errors |
| `MEGA` | 200 with a **2KB HTML page**, not the file - it is a JavaScript app |
| `PIXELDRAIN` | works, but `/u/<id>` is a viewer page; the file is at `/api/file/<id>` |

`fetch-getcomics-pack.py` resolves each link, rewrites Pixeldrain viewer URLs to
the API form, and skips anything answering with `text/html`.

**The resume trap this exposed.** A partial file is only resumable if it came
from *the same URL*. Falling back to a different mirror and resuming onto the
previous mirror's leftovers produced a file of exactly the right length that was
still corrupt - a real archive appended to a 2KB HTML error page. The fix is to
sniff the partial's magic bytes before resuming and discard anything without a
recognisable header. Length alone would never have caught it.

**Trust magic bytes over the extension**, in both directions. GetComics serves
everything as `application/octet-stream`; an "Avengers Finale .cbr" was really a
ZIP, and the Vision Quest Epic Collection is really a RAR. Name the output after
what `head -c8 | od -c` says, not what you expected.

### 6.13 Marking Wanted races Mylar3's own search

Marking issues Wanted and *then* hand-fetching means both paths run: Mylar3's
RSS job searches every 20 minutes for anything Wanted, and where GetComics
carries it under a matching name Mylar3 fetches and files its own copy first.
The hand-fetched copies are then left sitting in staging.

Harmless - the library ends up with exactly one copy of each and the verifier
stays clean - but it wastes a download. Either mark Wanted **or** hand-fetch,
not both. `forceProcess` with an explicit ComicID works on `Skipped` issues too,
so hand-fetching without marking is the tidier route.

### 6.14 Collected editions in the reading lists

An Epic Collection or TPB is one file, not thirteen issues, so it does not fit
`(CVID, issue range)`. Kavita also gives it the `-100000` sentinel instead of an
issue number, which the range filter deliberately rejects.

`PLAN` therefore accepts a segment addressed by Kavita folder instead:

```python
("folder:Regional Team Epic Collection", None),
```

That takes every chapter of that Kavita series in Kavita's own sort order and
bypasses Mylar3 entirely - the files go straight into
`/mnt/jellyfin/Comics/<folder>/`, where Kavita names the series from the folder.
Mylar3 never needs to know they exist.

### 6.15 One-shot collections need a name filter

In an anthology or one-shot collection every file is issue #1 of a *different*
series, so the issue number cannot pick the right one. The Avengers Origins
collection holds five #1s: Hero A, Hero B, Hero Solo & Speedster,
Thor, Vision. Use `--name-filter`:

```bash
sudo python3 scripts/ingest-comic-pack.py \
    --archive '.../Avengers Origins Vol 1 Collection (2011).zip' \
    --comicid <cvid> --issues 1 --name-filter 'Hero Solo'
```

#### The Annual collision: a right number on the wrong series

The same flag solves a second, more dangerous case that recurs in almost every
whole-run pack. Annuals sit alongside the ordinary issues and their numbers
collide:

| File in the pack | Parsed as | Should be |
| --- | --- | --- |
| `The Avengers Annual 016 (1987).cbz` | 16 | Avengers **Annual** #16, a different volume |
| `Uncanny Avengers Annual 001 (2014).cbr` | 1 | the Annual, cvid <cvid>, not cvid <cvid> |

Both would have been filed onto the wrong issue of the wrong series. The
parser cannot detect this — the number really is 16, it just belongs to another
volume — so it is purely a `--dry-run` responsibility. **The tell is a repeated
number in the `selected` line**, e.g. `selected 42: [16, 16, 57, ...]`.

Filter on the series name followed by a digit, which keeps the run and drops the
annual:

```bash
--name-filter 'The Avengers [0-9]'        # keeps 'The Avengers 016', drops 'The Avengers Annual 016'
--name-filter 'Uncanny Avengers [0-9]'    # 26 files -> 25
```

This also passes Marvel's `.NOW` style names (`Uncanny Avengers 018.NOW`)
unharmed.

### 6.16 scripts/comics-status.py - the one command to run

Answers "where is everything?" per reading-order item, and is the thing to run
before and after any batch of work.

```bash
sudo scripts/comics-status.py            # full table plus drift
sudo scripts/comics-status.py --gaps     # only what is incomplete
```

Exit status is 1 if anything is incomplete or drifting, so it gates a cron job.

It treats the `PLAN` table in `sync-kavita-reading-lists.py` as the **source of
truth** and reports **drift in both directions**: anything in `PLAN` that Mylar3
is not watching, and anything watchlisted that no `PLAN` segment references. The
reading order otherwise lives in three places - `PLAN`, the Mylar3 watchlist and
`READING-ORDER-scarlet-witch.md` - which can disagree silently. That is how
reading list 01 sat empty for two days without anyone noticing.

Deliberate exceptions go in `DRIFT_OK`, with a reason. Both Regional Team
volumes are listed there: their issues exist only inside the Epic Collections,
which `PLAN` addresses by Kavita folder, so they will never be referenced by
CVID - but they stay watchlisted in case single scans ever surface.

**Segments are scored against the issues they actually ask for.** An early
version compared whole-series download counts to the size of a range and
reported "14/4 held" for Team Book #11-14. Per-issue status is what makes
`Avengers (1998)` show `500-503 -> 4/4` and `1-56 -> 0/56` correctly in the same
run, since one volume appears in two places in the reading order.

### 6.17 A valid archive is not a complete one

Two Batch 4 downloads failed on 2026-08-17 in ways that produced **no error at
all**. Both are worth recognising on sight.

**A post titled "#1 - 22" served only #1-11.** GetComics splits long runs across
several links that all carry the *same* label, and `fetch-getcomics-pack.py`
ranks by label and stops at the first link that validates. The first part was a
perfectly good zip that passed every CRC check, so the fetch reported success
having collected half the run. The tool has no idea what an archive was supposed
to contain.

Check the post for sibling parts before assuming one link is the whole thing:

```bash
docker exec mylar3 python3 /tmp/fetchpack.py --list-links '<post-url>'
```

...then resolve each to its served filename, since the labels do not distinguish
them. The guard for this is on the ingest side:

```bash
python3 scripts/ingest-comic-pack.py --dry-run --require-complete \
    --from-dir <dir> --comicid <cvid> --issues 1-22
```

`--require-complete` exits non-zero when any requested issue is absent. **Leave
it off when a run spans several archives** - Avengers v3 #1-56 arrives as four
part files, and each run legitimately reports the other three parts' issues as
missing.

**`--match` patterns must use the served filename, not the listing text.** The
Avengers v3 parts were requested as `Avengers Vol. 3 000 - 015`, spaces copied
from how the post renders the range. The real filename is
`Avengers Vol. 3 000-015 (1999).zip`, so every match failed. Resolve the actual
filename first rather than transcribing what the page shows.

**And make batch wrappers fail loudly.** The script that hid this printed
`BATCH4 DOWNLOADS COMPLETE` regardless of outcome, having downloaded nothing:
the per-item failures went to a message the log filter dropped. Check exit status
per step and count failures:

```bash
if <command> | grep ...; [ "${PIPESTATUS[0]}" -ne 0 ]; then
    echo "*** FAILED: $item"; fails=$((fails+1))
fi
...
[ "$fails" -eq 0 ] && echo "OK" || echo "FAILED - $fails item(s)"
```

This is the same class of bug as the downloader that reported `DONE` on a 21%
file: a wrapper that reports success without checking anything.

### 6.18 Building a Kavita collection with real metadata

`scripts/build-comic-collection.py` repacks loose comic files into one Kavita
series with consistent `ComicInfo.xml`. Used for the Marvel Swimsuit Specials,
where the books ship under three different titles - "Marvel Illustrated: The
Swimsuit Issue" (1991), "Marvel Swimsuit Special #1-4" (1992-95), and subtitled
revivals (2025-26) - and would otherwise land as several unrelated Kavita series
in arbitrary order.

```bash
sudo scripts/build-comic-collection.py --manifest m.json \
    --series "Marvel Swimsuit Special" \
    --collection "Marvel Swimsuit Specials" \
    --dest "/mnt/jellyfin/Magazines/Marvel Swimsuit Special"
```

`Series` groups them, `Number` orders them, `Title` keeps each book's real name,
and `SeriesGroup` names the collection. Always `--dry-run` first.

**Everything is converted to .cbz.** `ComicInfo.xml` cannot be added to a `.cbr`,
because that is a RAR and nothing here writes RAR. Zip is the better container
anyway - it can be verified with the stdlib.

**The host's 7z cannot read modern RAR.** It is p7zip without RAR5 support and
fails with `Unsupported Method` on every page, while still exiting in a way that
looks like a generic error. The mylar3 container has a real `unrar`, so RAR
extraction is delegated there - which means both source and scratch directory
must sit under `/mnt/jellyfin`, the one tree both sides can see.

Pages are stored with `ZIP_STORED`, not deflate: they are already JPEG, so
recompressing costs CPU and saves nothing.

#### Collections are not reading lists

Different Kavita concept, different API. `/api/Collection/create` does not exist;
the working call is:

```bash
POST /api/Collection/update-for-series
{"collectionTagId": 0, "collectionTagTitle": "Marvel Swimsuit Specials",
 "seriesIds": [40]}
```

`collectionTagId: 0` creates a new collection. Passing only a title returns a 500
with a null-reference message.

#### The Magazines library finally has content

The swimsuit specials went into `/mnt/jellyfin/Magazines` rather than Comics:
they are magazine-format, they have no continuity relevance, and the library was
sitting empty and generating the "root folders for the library, magazines, are
empty" warning on every Kavita scan. That warning should now stop on its own.

`scannable_libraries()` in the sync script counts files on disk, so Magazines
started being scanned automatically the moment it had content - no config change
needed.

#### What cannot be verified from a scan

Asked which issues contain a given character, the honest answer is usually "not
determinable here". Page filenames in these scans are sequential
(`Swimsuit Special 01.jpg`, `MarSwim00.jpg`), none of the files carry a
`Characters` field, and the one `.NFO` present is scene-release ASCII art.
Identifying a pin-up needs someone to look at the pages. Say so rather than
guessing.

---

## 7. Verified working at install time

- Shelfmark container starts clean, web UI returns HTTP 200.
- Shelfmark applies PUID 112 / PGID 122, and `/config` plus `/data/Books` are
  writable by that user.
- Shelfmark outbound traffic exits through the ProtonVPN tunnel.
- Shelfmark reaches Byparr.
- Mylar3 authenticates to qBittorrent successfully (login 204, qBittorrent
  v5.2.3) using its stored credentials against `http://gluetun:8080`.
- The qBittorrent `comics` category exists and points to
  `/data/downloads/completed/comics`, matching Mylar3's configuration.
- Mylar3's ComicVine API key is valid (live API call returned data).
- Mylar3 reaches Prowlarr.
- Mylar3's scheduler is running: Auto-Search and Weekly Pullist both show
  recent successful completions.
- The weekly release calendar genuinely populates: the pull list loaded 95
  issues for week 31 and 99 issues for week 32 of 2026.
- RSS was enabled, resumed, and force-run successfully. Log confirmed
  `[RSS-FEEDS] Successfully ran a forced RSS Check`.
- Test series `Hero Solo (2024)` v4 (ComicVine ID <cvid>) was added via the
  API. Status Active, 10 issues loaded, directory
  `/mnt/jellyfin/Comics/Hero Solo (2024)` created with correct `112:122`
  ownership inside Kavita's comics library.
- Mylar3 egress verified on the VPN across all three HTTP paths: shell curl,
  Python `requests` (the DDL downloaders), and `urllib.request`. All returned
  the ProtonVPN IP, not the WAN IP.

### Full comic pipeline verified end to end on 2026-08-17

A complete download was proven, not simulated:

1. Search found `Hero Solo (2024) #1` via DDL(GetComics).
2. GetComics offered mega, pixeldrain and mediafire mirrors.
3. The mega mirror failed with `ETOOMANY` (too many concurrent IPs).
4. Mylar3 automatically fell back to **mediafire** and downloaded 72 MB.
5. Post-processing moved it to
   `/data/Comics/Hero Solo (2024)/Hero Solo 001 (2024) (Digital) (Shan-Empire).cbz`,
   owned `112:feedbackd`.
6. The download staging folder was left clean.
7. Mylar3 marked issue 1 `Downloaded`, series now `Have 1 / Total 10`.
8. **Kavita auto-detected it within about one minute** (folder scanned at
   21:51:30, post-processing finished 21:50:41) and indexed it as series
   `Hero Solo (2024)`, volume 2024, chapter 1, **34 pages**.

Step 4 is the important one: the successful download came through
**mediafire**, which is one of the modules that ignores Mylar3's own proxy
setting. Had the proxy been set only in Mylar3's config instead of at the
container level, that transfer would have gone out on the real WAN IP.

### RESOLVED: Shelfmark torrent import fixed by upgrading to 1.3.9

The qBittorrent bug described below was caused by running a **six month old
release**. Version `1.0.4` was pinned in error: the GHCR tag listing is
paginated and 1.0.4 was simply the newest version on the first page. The
repository actually has 418 tags and the current release is **1.3.9**.

Upstream refactored the client to `shelfmark/download/clients/qbittorrent.py`
and replaced the brittle `result == "Ok."` check with
`_normalize_add_result()` plus `_is_explicit_add_failure()`, which only treats
`fail`/`error` responses as failures. qBittorrent 5.x's `TorrentsAddedMetadata`
object no longer trips it.

After upgrading, the same release imported cleanly end to end:

```
Added torrent: 7766b5678a...
Added to qbittorrent: ... for 'Frankenstein by Mary Shelley EPUB'
download finished; starting post-processing
transferred 1 file(s) to /data/Books (ops: copy=1)
post-processing complete
```

**Lesson: always check the full paginated tag list before pinning.**

```bash
token=$(curl -s "https://ghcr.io/token?scope=repository:calibrain/shelfmark-lite:pull" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $token" \
  "https://ghcr.io/v2/calibrain/shelfmark-lite/tags/list?n=1000"
```

Settings survived the 1.0.4 to 1.3.9 upgrade intact (Hardcover, destinations,
Prowlarr, qBittorrent client). Config backup:
`backups/shelfmark-config.20260817-062150.tar.gz`.

### Still open: Anna's Archive (direct download) remains blocked

### Historical detail: the two original faults (2026-08-17)

Shelfmark installs, runs, searches and finds releases correctly, but **cannot
currently complete an import**. Two independent faults:

**1. Direct download is blocked (Anna's Archive unreachable).**
Anna's Archive is Shelfmark's search index for direct downloads. From this
host's VPN exit:

| Mirror | Result |
| --- | --- |
| annas-archive.org | no connection (HTTP 000) |
| annas-archive.se | no connection (HTTP 000) |
| annas-archive.gl | 302 redirect |
| annas-archive.li | JS FingerprintJS interstitial, 1.1 KB, no results table |

Log signature: `No results table found for query: ...` then
`Found 0 releases via title+author`.

Byparr can solve the first interstitial (returns 32 KB, follows the `fp=-7`
redirect) but lands on a second JS/adblock-detection layer, still with no
results. So direct download finds nothing at all, for any title.
`libgen.li` IS reachable and returns real results, but libgen is a download
mirror, not the search index, so it cannot compensate.

**2. Torrent import fails against qBittorrent 5.x (upstream bug).**
`release_sources/prowlarr/clients/qbittorrent.py` does:

```python
if result == "Ok.":
    ...
raise Exception(f"Failed to add torrent: {result}")
```

qBittorrent 5.2.3 returns a `TorrentsAddedMetadata` object rather than the
literal string `"Ok."`, so Shelfmark raises even on success. Observed log:

```
qBittorrent add failed: Failed to add torrent: TorrentsAddedMetadata(
  {'added_torrent_ids': ['7766b...'], 'failure_count': 0,
   'pending_count': 0, 'success_count': 1})
```

Note `success_count: 1`. **The torrent IS added and DOES download.** A test
release ("Frankenstein by Mary Shelley EPUB") completed to
`/mnt/jellyfin/downloads/completed/Frankenstein by Mary Shelley EPUB/` with
correct `112:feedbackd` ownership. But because Shelfmark believes the add
failed, it never tracks or imports the file, so `/mnt/jellyfin/Books` stays
empty.

**This is fixed** by the 1.3.9 upgrade described above. Retained here because
the symptom (a logged "failure" that actually succeeded) is confusing, and the
same class of version-skew bug can recur.

### File organisation: use folders, not flat files

`FILE_ORGANIZATION` was changed from `rename` to `organize` with
`TEMPLATE_ORGANIZE = {Author}/{Title}` on 2026-08-17. Books now land as
`/data/Books/<Author>/<Title>/...` instead of loose at the library root.

Reason: Kavita indexed the existing `Suzanne Collins/` folder fine and picked up
Mylar3's comic (also in a folder) within a minute, but did not index a loose
`.epub` sitting directly in `/books`. Kavita's Books library accepts EPUB and
PDF (verified via `LibraryFileTypeGroup`: groups 2 and 3 on library 1), so the
file type was never the problem. Matching the folder-per-author convention is
the reliable path.
- Kavita library `Books` maps to `/mnt/jellyfin/Books` and `Comics` maps to
  `/mnt/jellyfin/Comics`, so both download destinations are already scanned.

---

## 8. Common failure modes

### Shelfmark returns no search results

Check the bypass path first. Byparr must be up:

```bash
docker ps --filter name=flaresolverr
docker exec shelfmark curl -s -o /dev/null -w '%{http_code}\n' http://flaresolverr:8191
```

Note the container is named `flaresolverr` but the image is Byparr.

### Shelfmark cannot reach the internet

Its egress depends on Gluetun's HTTP proxy. If Gluetun is down or unhealthy,
Shelfmark has no outbound path.

```bash
docker ps --filter name=gluetun          # must show (healthy)
docker exec shelfmark curl -s -x http://gluetun:8888 https://api.ipify.org
```

If that IP matches your home IP rather than a ProtonVPN IP, the proxy is being
bypassed and downloads are not protected.

### Files land but Kavita does not show them

Kavita scans on a schedule. Force it: Kavita UI, Libraries, then Scan. If files
are present but unreadable, check ownership is `112:122`:

```bash
ls -la /mnt/jellyfin/Books /mnt/jellyfin/Comics
```

### Permission denied writing to the library

Every writing container needs `PUID=112` and `PGID=122`. A container running as
root or as uid 1000 will create files Jellyfin and Kavita cannot read, because
Jellyfin runs natively as uid 112.

### Mylar3 grabs nothing even with monitored series

Most likely the 24 hour Auto-Search has not fired yet. Trigger Auto-Search
manually (section 6) rather than waiting.

### Mylar3 reports "Connection Refused" and blocks providers for 60 minutes

**This bit us on 2026-08-17 and will happen again.** Mylar3 stores Prowlarr
torznab URLs by **numeric indexer ID** (`http://prowlarr:9696/13/api`). If an
indexer is deleted and re-added in Prowlarr, it gets a **new ID**, and Mylar3's
stored URL silently points at a dead one. Prowlarr answers 404, Mylar3
misreports it as "Connection Refused", then blocks the provider for 60 minutes.

1337x moved from ID 6 to ID 13 this way. Symptom in the Mylar3 log:

```
General Error fetching data from <name>: 429 Client Error / 404
Temporarily blocking provider <name> (torznab) for 60 minutes...
Aborting search due to Provider unavailability
```

Check the real IDs, then compare against `extra_torznabs` in `config.ini`:

```bash
KEY=$(sudo grep -oP '(?<=<ApiKey>)[^<]+' config/prowlarr/config.xml)
curl -s -H "X-Api-Key: $KEY" http://localhost:9696/api/v1/indexer \
  | python3 -c "import sys,json;[print(i['id'], i['name'], i['enable']) for i in json.load(sys.stdin)]"
grep '^extra_torznabs' config/mylar3/mylar/config.ini
```

Fix by stopping mylar3, correcting the ID in `config.ini`, and starting it
again. Restarting also clears the in-memory 60 minute provider blocklist.

### A 429 from Prowlarr is not always a rate limit

Prowlarr returns HTTP 429 with
`Indexer is disabled till <time> due to recent failures` when it has put an
indexer into a failure backoff. Check which indexers are in that state:

```bash
KEY=$(sudo grep -oP '(?<=<ApiKey>)[^<]+' config/prowlarr/config.xml)
curl -s -H "X-Api-Key: $KEY" http://localhost:9696/api/v1/indexerstatus
```

### Mylar3 searches successfully but finds nothing

Verify the content actually exists on your indexers before assuming a config
problem. Query the torznab endpoint directly (`cat=7030` is comics):

```bash
KEY=$(sudo grep -oP '(?<=<ApiKey>)[^<]+' config/prowlarr/config.xml)
curl -s "http://localhost:9696/13/api?t=search&q=SERIES+NAME&cat=7030&apikey=$KEY" \
  | grep -oE '<title>[^<]*</title>'
```

Mylar3 correctly refuses to grab a different volume year than the one you are
monitoring, so a 2023 volume will not satisfy a 2024 series. That is correct
behaviour, not a bug.

---

## 9. What to check if downloads stop working

### Comics specifically: check these three first

Because DDL(GetComics) is the only provider that actually delivers comics, most
"it stopped working" cases for Mylar3 are one of these rather than anything in
the torrent stack below.

1. **Are issues stuck `Snatched` with nothing happening?** That is usually a
   pack whose filenames Mylar3 could not parse. There is **no error line**.
   Confirm with:
   ```bash
   ls -1 /mnt/jellyfin/downloads/completed/comics/*/*/ 2>/dev/null
   docker logs mylar3 --since 30m 2>&1 | grep 'should have an issue number'
   ```
   Fix by renaming to a parseable pattern and re-running `forceProcess` (see
   "Packs whose filenames Mylar3 cannot parse").

2. **Is it searching, or is it just the delay?** Slow is normal. Confirm forward
   motion rather than assuming a hang:
   ```bash
   docker logs mylar3 --since 10m 2>&1 | grep -oE "looking for [^(]*\(" | sort -u
   ```
   If the series name is advancing, it is working. Budget ~3 minutes per issue
   that GetComics does not have.

3. **Does GetComics actually have it?** Plenty of this library does not exist
   there. Check before debugging a non-problem:
   ```bash
   docker exec mylar3 sh -c "curl -s -x http://gluetun:8888 \
     'https://getcomics.org/?s=<title+words>'" \
     | grep -oiE '<title words>[^<"]{0,45}' | sort -u
   ```
   Beware false negatives: GetComics writes titles with hyphens and drops
   apostrophes, so search without punctuation. A probe for
   `Avengers The Children` found nothing while `childrens crusade` found the
   complete pack.

Then work down the general list.

1. **Is Gluetun healthy?**
   ```bash
   docker ps --filter name=gluetun --format '{{.Status}}'
   ```
   Everything torrent related depends on it. Gluetun is pinned to `v3.41.1`
   deliberately, because later versions regressed the default route.

2. **Is the VPN actually tunnelling?**
   ```bash
   docker exec gluetun wget -qO- https://api.ipify.org
   ```
   Should not be your home IP.

3. **Did the forwarded port change?** ProtonVPN NAT-PMP rotates it. The
   `media-stack-vpn-sync.service` systemd unit pushes the new port and tunnel
   IP into qBittorrent.
   ```bash
   systemctl status media-stack-vpn-sync
   ```
   If qBittorrent shows connected but never downloads, this is a prime suspect.

4. **Can qBittorrent be reached by the apps?** They connect to
   `http://gluetun:8080`, not `http://qbittorrent:8080`, because qBittorrent
   shares Gluetun's network namespace and has no IP of its own.

5. **Is Prowlarr healthy and are its indexers passing tests?**
   Prowlarr UI, Indexers, then Test All. Prowlarr also routes outbound through
   the Gluetun proxy, so a Gluetun problem shows up here as every indexer
   failing at once.

6. **Is the disk full?**
   ```bash
   df -h /mnt/jellyfin
   ```

7. **Check the logs of the specific app:**
   ```bash
   docker logs --tail 100 shelfmark
   docker logs --tail 100 mylar3
   docker logs --tail 100 prowlarr
   ```

---

## 10. Known gaps and things to watch

### Found during the 2026-08-17 review

Everything below was found in the same review. Items marked **[FIXED]** were
approved and applied on 2026-08-17; they are kept here because the reasoning is
worth having if the setting is ever questioned again. Items marked **[OPEN]**
still need a decision, a restart, or a source that does not exist yet.

- **[OPEN] Timezone mismatch between host and containers.** The host is
  `Europe/London` (BST) while `TZ=America/Los_Angeles` in `.env` puts every
  container on PDT, an 8 hour offset. Nothing is broken, but two things suffer:
  correlating container logs against host logs during an incident is needlessly
  confusing (this genuinely misled the 2026-08-17 debugging for a moment), and
  every in-container scheduled job runs on Pacific time, so a task set for 3am
  fires at 11am local. Decide which is authoritative. Changing `TZ` restarts
  every service, so it should be done deliberately, not mid-queue.

  Still open. The recommendation is to move the *host* to Pacific, matching
  `.env`, since that needs no container restarts at all:
  ```bash
  sudo timedatectl set-timezone America/Los_Angeles   # revert: Europe/London
  ```
  This has to be run by hand; the agent permission layer blocks it. Native
  (non-container) Jellyfin picks up the new zone only on its next restart.
- **[FIXED] Gluetun was `restart: on-failure:3`.** Every other service is
  `unless-stopped`. After three consecutive failures Gluetun stays down, and
  because qBittorrent shares its network namespace while Prowlarr, Mylar3 and
  Shelfmark proxy through it, that is a full outage of the download stack until
  someone intervenes. A transient upstream problem at boot could trip it.
  Changed to `unless-stopped` in compose **and** live, without recreating the
  container:
  ```bash
  docker update --restart=unless-stopped gluetun
  ```
  `docker update` is the right tool here. Editing compose alone would not take
  effect until the next recreate, and recreating Gluetun tears down
  qBittorrent's network namespace with it. This way the running container kept
  its uptime and the active download queue was never interrupted.
- **[FIXED] Three images floated on `latest`:** `kavita`,
  `nginx-proxy-manager`, and `mylar3`. Mylar3 was the one that mattered,
  because its behaviour is tuned in detail (provider order, renaming, DDL, pack
  handling) and an unattended pull could change any of it. All three are now
  pinned to the versions that were already running, so nothing changes on the
  next recreate: `mylar3:v0.10.0-ls262`, `kavita:0.9.0.2`,
  `nginx-proxy-manager:2.15.1`. Verify a tag exists before pinning it.
- **[FIXED] Mylar3 had no `depends_on: gluetun`.** Shelfmark waits for
  `service_healthy`; Mylar3 does not, even though its entire egress path is
  Gluetun's proxy. On a cold boot Mylar3 can start first and its early outbound
  calls fail. It fails *closed*, not open (with `HTTP_PROXY` set and the proxy
  unreachable, `requests` and `urllib` raise rather than going direct, so there
  is no leak), which is why this was untidiness rather than a security issue.
  The `depends_on: gluetun / condition: service_healthy` block is now present;
  it takes effect on the next recreate.
- **[OPEN] Swap is nearly exhausted:** 458MB of 511MB used, though 4.2GB of RAM is
  available. That points at historical memory pressure rather than a current
  problem, and given this Pi's history of storage-related freezes it is worth
  keeping an eye on.
- **[FIXED] A quarantined 350MB duplicate** at
  `/mnt/jellyfin/downloads/quarantine/`, the wrongly-attributed copy of the
  1985 Hero Duo pack. Deleted on approval, after confirming
  the folder held nothing but that pack and that all 12 issues were already
  correctly filed under `The Hero Duo (1985)`. Confirm both
  of those before deleting any future quarantine.
- **[OPEN] DuckDNS: resolved, working, but the updater is not on this Pi.** Checked
  2026-08-17: both `<JELLYFIN_HOST>` and `<SEERR_HOST>`
  resolve to the current WAN IP (<WAN_IP>), so something *is* keeping them
  fresh. Since there is no cron job, systemd timer, or DuckDNS script anywhere
  on this Pi, that something is almost certainly the router. **Do not remove
  the DuckDNS entry from the router config** and remember it lives there, not
  here, the next time external access breaks.

  Test it correctly with `getent hosts <domain>`, **not** `dig ... @8.8.8.8`.
  Outbound DNS to external resolvers is blocked from this host, so `dig` against
  8.8.8.8 returns empty for *everything* including google.com. That looks
  exactly like a dead DNS record and briefly caused a false alarm here. Always
  run a known-good control domain before believing a negative DNS result.
- **[OPEN] 1337x is unreachable from behind the VPN.** Confirmed 2026-08-17:
  ```bash
  docker exec prowlarr curl -s -m 10 -o /dev/null -w "%{http_code}\n" \
      -x http://gluetun:8888 https://1337x.to/      # 403
  docker exec prowlarr curl -s -m 10 -o /dev/null -w "%{http_code}\n" \
      -x http://gluetun:8888 https://thepiratebay.org/   # 302, alive
  ```
  Cloudflare 403s the ProtonVPN exit IP, so every 1337x query fails. Mylar3
  reports this as `Connection Refused` and blocklists the provider for an hour
  at a time, which caps the waste but never fixes it. This is why 1337x
  produced **zero** grabs across an entire day of searching. The cost is now
  small because DDL runs first, so it is not urgent; the clean fix is to remove
  1337x from `provider_order` (Mylar3 must be **stopped** for that, see the
  config-editing note above) or to route it through Byparr. Re-test with the
  commands above before removing it, since exit IPs rotate.
- **[OPEN] Torrent indexer coverage for comics is thin.** Only 1337x (ID 13), The
  Pirate Bay (ID 8), and Bangumi Moe (ID 10) advertise category 7030
  (Books/Comics), and they are general-purpose public trackers with sparse,
  dated comic content. A torrent search for `Hero Solo` returned only the
  2023 volume. **DDL via GetComics was enabled on 2026-08-17 specifically to
  solve this** and it found the 2024 volume immediately. DDL is now the
  effective primary source for comics; the torrent indexers are a weak
  fallback.
- **[FIXED] Windows malware in the download folder.** qBittorrent's completed
  list held `Ted Lasso S04E02 1080p HEVC x265-MeGusta.exe`, a matching `.scr`,
  and an S04E03 `.exe` under category `tv` (3.5GB total). Video episodes are
  never `.exe` or `.scr`; this is the classic Windows malware masquerade. They
  could not execute on the Pi, but they were one copy away from a Windows
  machine. Removed on approval.

  **Confirm before deleting, and confirm before keeping.** Run `file` on the
  binary rather than trusting the extension:
  ```bash
  file "/mnt/jellyfin/downloads/<name>.exe"
  # PE32+ executable (GUI) x86-64, for MS Windows  -> malware, remove
  ```
  Delete through the qBittorrent API, not `rm`, when a torrent still references
  the files, or the torrent simply re-creates or re-seeds them:
  ```bash
  curl -s -X POST "http://localhost:8080/api/v2/torrents/delete" \
       -d "hashes=<hash>&deleteFiles=true"
  ```
  The same sweep found `mkvmerge.exe` and a `.bat` beside the Frieren releases.
  Those are **legitimate** neoDESU release tooling and were kept: the `.bat` is
  a readable mkvmerge audio-strip command, not an opaque binary. Extension
  alone would have condemned them, and deleting them would have broken
  `scripts/rename_frieren.py`. Read the accompanying script before judging.
- **[BY DESIGN] `autowant_all = False`.** Back issues are added as Skipped, not Wanted, so
  adding a series does not retroactively fetch its history. Future issues do
  get auto-wanted because `autowant_upcoming = True`, which is what makes
  ongoing-series automation work.
- **[OPEN] Uncommitted compose changes.** At the time of this change the working tree
  had unstaged edits migrating Gluetun to ProtonVPN plus WireGuard, alongside
  edits to `scripts/sync-qbit-vpn-ip.sh`. Those are unrelated to this work but
  are not committed, so a `git checkout` would destroy them.
- **[OPEN] Mylar3 stores its qBittorrent password in plaintext** in `config.ini`. That
  is inherent to Mylar3. Keep the file readable only by its owner.
- **[OPEN] Omnibus arm64** may appear later. Recheck with:
  ```bash
  docker buildx imagetools inspect ghcr.io/hankscafe/omnibus:latest
  docker buildx imagetools inspect ghcr.io/hankscafe/omnibus-engine:latest
  ```
