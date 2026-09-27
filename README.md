# Raspberry Pi Media Stack

> A VPN-gated, self-hosted media system on a single Raspberry Pi 5. Movies, TV, anime, books, comics and audiobooks: requested, found, downloaded, organised and served automatically.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-arm64%20%7C%20Raspberry%20Pi%205-red)](#overview)
[![Last Commit](https://img.shields.io/github/last-commit/Derec58/raspberry-pi-media-stack)](https://github.com/Derec58/raspberry-pi-media-stack/commits/main)
[![Services](https://img.shields.io/badge/services-13%20containers%20%2B%201%20native-blue)](#stack)

---

## Table of Contents

- [Overview](#overview)
- [Design Constraints](#design-constraints)
- [Stack](#stack)
- [Architecture](#architecture)
  - [Diagram](#architecture-diagram)
  - [Networking](#networking)
  - [Storage](#storage)
  - [Public Access](#public-access)
- [Operations](#operations)
- [Release Filtering](#release-filtering)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Deployment](#deployment)
- [Configuration](#configuration)
- [Literature Stack](#literature-stack)
- [Notable Bugs and Fixes](#notable-bugs-and-fixes)
- [Known Limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Background and Lessons](#background-and-lessons)
- [Acknowledgments](#acknowledgments)
- [License](#license)

---

## Overview

A request goes into a web UI. Software searches a set of indexers, applies a filter
policy, hands the chosen release to a torrent client locked inside a VPN container,
renames and files the result, fetches subtitles, and the media server picks it up.
No manual step after the request.

The interesting part of this project is not the service list. It is the filter
policy and the operational tooling that keep the library playable on hardware that
cannot transcode video, for viewers who watch in web browsers.

**Hardware**

| | |
|---|---|
| Board | Raspberry Pi 5 Model B Rev 1.1, 8 GB |
| Kernel | 6.12.93+rpt-rpi-2712, Raspberry Pi OS (Debian) |
| OS drive | 229 GB SSD |
| Media drive | 5.5 TB USB HDD at `/mnt/jellyfin` |
| Video encoder | **none** (see [Design Constraints](#design-constraints)) |

**Scale at time of writing:** 334 films, 25 anime series, 14 TV series, 66 comic
series, 2.7 TB used of 5.5 TB.

---

## Design Constraints

Two hardware and audience facts drive every filtering decision in this repo. They
are worth stating first because the rest of the configuration looks arbitrary
without them.

### The Pi 5 has no video encoder

`/dev/video19` is `rpi-hevc-dec`, a **decoder**. There is no encode block. Any
transcode Jellyfin performs is software `libx264`. Measured on this hardware:

| Source | Transcode speed | Verdict |
|---|---|---|
| 2160p HEVC | **0.19 to 0.34x** real time | unplayable |
| 1080p HEVC | **1.43 to 1.51x** | keeps up comfortably |
| 960p HEVC | 1.24 to 1.71x | works, marginal |
| 1080p with subtitle burn-in | **0.111x** | unplayable |

Resolution is the variable, not codec. 1080p transcodes fine. 4K does not. Burning
subtitles into the picture does not, at any resolution, because it forces a full
re-encode.

### Viewers use web browsers

Browsers cannot decode **HEVC, AV1 or VC-1**. Firefox is the strictest target. A
browser playing one of those files forces a transcode, and on this hardware a 4K
transcode is effectively a failure.

Everything follows from those two facts:

- **H.264 8-bit preferred** over HEVC, AV1 and 10-bit
- **VC-1 rejected outright**, no browser decodes it
- **Text subtitles** (SRT, ASS) over bitmap (PGS, VobSub), because bitmap must be
  burned in
- **1080p only**, no 2160p
- **Bitrate capped** so files direct stream rather than buffer
- Lossless audio deprioritised: audio-only transcoding is cheap, but it is bulk
  with no benefit on a phone

---

## Stack

13 Docker containers plus Jellyfin, which runs natively so it can reach the
VideoCore decoder without device passthrough into a container.

| Service | Purpose | Port | Image |
|---|---|---|---|
| **Jellyfin** | Media server | 8096 | native systemd service |
| **Seerr** | Request portal | 5055 | `ghcr.io/seerr-team/seerr:v3.4.1` |
| **Radarr** | Film automation | 7878 | `lscr.io/linuxserver/radarr:latest` |
| **Sonarr** | TV automation | 8989 | `lscr.io/linuxserver/sonarr:latest` |
| **Prowlarr** | Indexer aggregator | 9696 | `lscr.io/linuxserver/prowlarr:latest` |
| **Bazarr** | Subtitle automation | 6767 | `lscr.io/linuxserver/bazarr:latest` |
| **qBittorrent** | Torrent client | via gluetun | `lscr.io/linuxserver/qbittorrent:latest` |
| **Gluetun** | VPN gateway and kill switch | 8080, 8888 | `qmcgaw/gluetun:v3.41.1` |
| **Byparr** | Cloudflare challenge solver | 8191 | `ghcr.io/thephaseless/byparr:latest` |
| **Nginx Proxy Manager** | Reverse proxy, TLS | 80, 443 | `jc21/nginx-proxy-manager:2.15.1` |
| **Shelfmark** | Ebook automation | 8084 | `ghcr.io/calibrain/shelfmark-lite:1.3.9` |
| **Mylar3** | Comic automation | 8090 | `lscr.io/linuxserver/mylar3:v0.10.0-ls262` |
| **Kavita** | Ebook and comic reader | 5000 | `jvmilazz0/kavita:0.9.0.2` |
| **Audiobookshelf** | Audiobook and podcast server | 13378 | `ghcr.io/advplyr/audiobookshelf:latest` |

**Pinned versus floating.** Gluetun, Seerr, Mylar3, Kavita, Shelfmark and Nginx
Proxy Manager are pinned to exact versions. Radarr, Sonarr, Prowlarr, Bazarr,
qBittorrent, Byparr and Audiobookshelf float on `:latest`, so a rebuild is not
reproducible to the version for those seven.

> **Two naming notes.** Byparr runs under the container name `flaresolverr` so
> Prowlarr's proxy configuration needs no change; it is the maintained replacement
> for FlareSolverr. The Seerr container is still named `jellyseerr` for the same
> reason, following the fork.

> **Gluetun is pinned to v3.41.1 deliberately.** A later image introduced a default
> route regression that broke VPN egress. The pin is the fix, and it means the stack
> is intentionally behind upstream.

---

## Architecture

### Architecture Diagram

```
                          ┌─────────────────────────┐
     internet ───TLS───▶  │  Nginx Proxy Manager    │  network_mode: host
                          │  ports 80 / 443         │  binds privileged ports
                          └───────────┬─────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
        ┌──────────┐            ┌──────────┐           ┌────────────┐
        │ Jellyfin │            │  Seerr   │           │   Kavita   │
        │  :8096   │            │  :5055   │           │   :5000    │
        │  native  │            └────┬─────┘           └────────────┘
        └────┬─────┘                 │
             │                        │ writes requests
             │ reads library          ▼
             │                  ┌───────────┐   ┌──────────┐
             │                  │  Radarr   │   │  Sonarr  │
             │                  │  :7878    │   │  :8989   │
             │                  └─────┬─────┘   └────┬─────┘
             │                        │              │
             │                        └──────┬───────┘
             │                               │ queries
             │                          ┌────▼─────┐      ┌──────────┐
             │                          │ Prowlarr │─────▶│  Byparr  │
             │                          │  :9696   │      │  :8191   │
             │                          └────┬─────┘      └──────────┘
             │                               │ 10 indexers in Prowlarr
             │                               │ (via gluetun HTTP proxy)
             │                               ▼
             │              ┌──────────────────────────────────┐
             │              │  Gluetun  (ProtonVPN WireGuard)  │
             │              │  ┌────────────────────────────┐  │
             │              │  │  qBittorrent               │  │
             │              │  │  network_mode:             │  │
             │              │  │    container:gluetun       │  │
             │              │  │  no network stack of its   │  │
             │              │  │  own: VPN down = no net    │  │
             │              │  └────────────────────────────┘  │
             │              └──────────────┬───────────────────┘
             │                             │ writes
             ▼                             ▼
        ┌──────────────────────────────────────────────────┐
        │   /mnt/jellyfin  ──bind──▶  /data  (all apps)    │
        │   downloads/ and library/ under ONE root so       │
        │   imports hardlink instead of copying             │
        └──────────────────────────────────────────────────┘
```

### Networking

**The VPN kill switch is structural, not a firewall rule.** qBittorrent runs with
`network_mode: container:gluetun`, meaning it has no network namespace of its own
and shares gluetun's. If gluetun stops, qBittorrent has no route anywhere. This
fails closed by construction, where a firewall rule or an interface binding can
fail open.

Verified live: gluetun container id `3ceff092fb9d`, qBittorrent
`NetworkMode=container:3ceff092fb9d`.

Consequences worth knowing:

- qBittorrent's WebUI is published through **gluetun's** port mapping, which is why
  gluetun exposes 8080
- restarting gluetun forces a qBittorrent restart
- container-to-container calls get awkward. Prowlarr was routing **internal**
  service calls through the VPN proxy and had to be explicitly configured to bypass
  it

Prowlarr's outbound indexer traffic goes through gluetun's HTTP proxy on 8888, so
indexer queries share the VPN egress without putting Prowlarr inside the namespace.

### Storage

Every container binds `/mnt/jellyfin` to `/data`. One root, one filesystem.

```
/mnt/jellyfin
├── Movies/          film library
├── Anime/           anime series
├── TV Shows/        TV series
├── Comics/          comic library
├── Books/           ebooks
├── Audiobooks/
├── Magazines/
├── downloads/
│   ├── completed/
│   └── incomplete/
├── .recyclebin/     7 day retention, the undo for every replacement
└── pi-backup/       config backups, never touched by automation
```

**Why one root matters.** Hardlinks only work within a filesystem, and the *arr
apps only create them when source and destination are on the same mount *as the
container sees it*. Separate mounts silently degrade to full copies, doubling disk
use and I/O. With this layout an import is a hardlink, so a completed download and
its library entry are the same bytes on disk.

That is easy to misread. During a cleanup, `du` reported 441 GB in the download
folder, but **260 GB of it was hardlinked to the library**: the same data counted
twice, and deleting it would have freed nothing. Only 181 GB was genuinely
orphaned.

**Permissions.** Media files must be owned `112:122` (the Jellyfin user and the
media group). Hand-placing files as another user causes silent
`UnauthorizedAccessException` import failures in the *arr apps. `PUID` and `PGID`
in `.env` control this for the LinuxServer containers.

### Public Access

Nginx Proxy Manager runs in `network_mode: host` so it can bind 80 and 443
directly, terminates TLS with Let's Encrypt, and reverse proxies to Jellyfin and
Seerr. A dynamic DNS hostname provides a stable address; the updater runs on the
router, not on the Pi.

Only Jellyfin, Seerr and Kavita are reachable externally. Every automation service
is LAN-only, enforced on a timer rather than trusted (see
[Operations](#operations)).

---

## Operations

Compose brings the services up. These keep them correct. Everything in `ops/` and
`scripts/` is version controlled, and every systemd unit is installed from
`ops/systemd/`.

### Scheduled units

| Unit | Interval | What breaks without it |
|---|---|---|
| `media-stack-vpn-sync` | on VPN change | qBittorrent binds a stale tunnel IP and every announce fails with EPERM |
| `media-stack-mount-guard` | 5 min | containers start before the USB drive mounts and pin an empty stub directory, so the stack looks healthy while `/data` is empty |
| `media-stack-lan-only-ports` | 5 min | automation UIs drift to public exposure; internet scanners find them within hours |
| `media-stack-extract-subs` | hourly | files with only bitmap subtitles force burn-in, which is the one thing this hardware cannot do |
| `media-stack-reap-stalled` | 30 min | dead torrents occupy the download slots and starve everything behind them |
| `media-stack-audit-library` | weekly | wrong default tracks and codec problems accumulate invisibly |
| `pi-backup` | weekly | no config backup |

### Scripts

| Script | Purpose |
|---|---|
| `audit-filters.py` | **139 assertions** that live config matches documented policy. Exits non-zero on drift |
| `audit-library.py` | File-level health: wrong default tracks, bitmap-only subtitles, interlacing, over-ceiling bitrate, orphan sidecars |
| `fix-track-flags.py` | Repairs `default`/`forced` flags via `mkvpropedit`, header-only, no re-encode |
| `extract-subs.py` | Extracts embedded English subtitle tracks to `.srt` sidecars across all libraries |
| `reap-stalled.py` | Removes downloads with no seeds or peers, blocklists them, triggers a fresh search |
| `check-media-mount.sh` | Detects containers pinned to a stale bind mount by comparing device IDs, not paths |
| `lan-only-ports.sh` | Enforces that only Jellyfin, Seerr and Kavita are externally reachable |
| `sync-qbit-vpn-ip.sh` | Updates qBittorrent's bound interface address when the tunnel IP changes |
| `ops/pi-backup.sh` | Config and metadata backup to the media drive |

Comic library tooling (`ingest-comic-pack.py`, `verify-comics-library.py`,
`comics-status.py`, `find-comic-source.py`, `build-comic-collection.py`,
`fetch-getcomics-pack.py`) handles issue-level matching, mis-ranged pack detection
and stray-file verification. These read any personal collection data from
`scripts/collection-config.json`, which is gitignored; see
`scripts/collection-config.example.json` for the shape.

For the books and comics side specifically, `RUNBOOK-books-comics.md` documents
the matching failures, filename normalisation and alternate-search behaviour in
detail.

### Why the audit exists

Configuration drifts, and some of it drifts silently from outside. Real regressions
`audit-filters.py` has caught:

- **Prowlarr overwriting downstream settings.** A minimum seeder floor set in
  Radarr and Sonarr reverted to 1 on all 16 indexers, because Prowlarr re-syncs its
  app profile to the applications and overwrites what is set there. The floor has
  to live in **Prowlarr's app sync profile** to hold.
- A policy value changed in one place and not the other, flagged as drift on the
  next run.

Run it any time:

```bash
python3 scripts/audit-filters.py   # exit 0 = config matches policy
python3 scripts/audit-library.py   # report on the media files themselves
```

---

## Release Filtering

This is where most of the engineering went. The goal is a file that **direct plays**
in a browser, because the alternative is a transcode this hardware cannot perform.

### Quality profiles

One profile per app: `Mobile 1080p` (Radarr), `Anime 1080p` and `TV 1080p`
(Sonarr). WEB-DL ranks **above** BluRay, which is deliberate: disc rips carry
bitmap subtitles, while WEB-DL carries text. On hardware that cannot burn in
subtitles, a text track is worth more than the marginal bitrate.

### Size ceilings

Limits are expressed in MB per minute, which the apps multiply by runtime, so the
effective cap is a constant bitrate:

| Quality | Cap | Effective |
|---|---|---|
| WEBDL / WEBRip / HDTV 1080p | 100 to 110 MB/min | 13.3 to 14.7 Mbps |
| Bluray-1080p | 110 MB/min | 14.7 Mbps |
| Remux-1080p | 110 MB/min | 14.7 Mbps |

`Bluray-1080p` originally had **no ceiling at all**, which is why 35 Mbps files kept
arriving even after Remux was disallowed. A floor of 20 MB/min also rejects
low-bitrate rips.

### Custom formats

22 formats, scored. `minFormatScore` is -1000, so anything at -10000 is refused
outright rather than merely deprioritised.

| Format | Score | Reason |
|---|---|---|
| `HDR-or-DV` | -10000 | tone mapping to SDR is the most expensive transcode |
| `VC1` | -10000 | no browser decodes VC-1 |
| `Hardcoded-Subs` | -10000 | subtitles burned into the picture, cannot be disabled |
| `Multi-File-Bundle` | -10000 | one torrent, many videos, wrong file imported |
| `Foreign-Dub-Only` | -10000 | no English audio |
| `Non-Latin-Title` | -10000 | foreign-language edition |
| `Cam-Laundered` | -10000 | cam rip relabelled as WEB |
| `Upscaled` | -10000 | fake 1080p from a 720p source |
| `Sample` | -10000 | a 90 second clip imported as the feature |
| `Audio-Description` | -10000 | narrated for accessibility, a different product |
| `3D-or-SBS` | -10000 | side-by-side 3D plays as two squished images |
| `AV1`, `Hi10P` | -500 | almost nothing decodes these |
| `HEVC-x265` | -300 | browsers cannot decode it, but allowed as a fallback |
| `HEVC-10bit` | -150 | stacks with the above to -450 |
| `Lossless-Audio` | -100 | bulk with no benefit on a phone |
| `Dual Audio` | +500 | original language plus English dub |
| `Chinese Audio` | +400 | |
| `English Audio` | +300 | |
| `Text-Subs-Likely` | +150 | biases away from bitmap-only releases |
| `Multi-Subs` | +100 | |
| `Opus Audio` | -50 | |

**HEVC is penalised, not banned.** At 1080p it transcodes at 1.4x, which is
watchable, and dual-audio anime is predominantly HEVC. Banning it would cost the
English dub on most anime. 4K HEVC is a different matter and 2160p is disallowed
entirely.

### Seeders

A minimum of **10 seeders** per indexer, set in **Prowlarr's app sync profile** so
it is not overwritten downstream. Prowlarr manages 10 indexers; 8 sync through to
Radarr, of which 7 are enabled (one is disabled for repeated failures).

Worth knowing how the apps actually rank releases, because it explains why a
seeder minimum is necessary rather than a preference:

1. **Rejection first.** Quality not in profile, size outside limits, format score
   below minimum, ignored terms, too few seeders. Rejected releases are never
   ranked.
2. **Then ordering:** quality position in the profile, then custom format score,
   then protocol, and **seeders last**, only as a near-tiebreak.

So seeders barely influence the choice unless everything above is equal. Without an
explicit floor, a 6-seeder release beats a 73-seeder one on a marginally better
format score, and then sits at 0 bytes indefinitely.

### Subtitles

Bazarr holds an English language profile on every film and series and fetches what
releases do not carry. `extract-subs.py` additionally pulls embedded English tracks
out to `.srt` sidecars, so a text track exists on disk and Jellyfin never needs to
burn one in.

A subtlety: a language profile must be **assigned per item**, not merely defined.
Defining the profile and enabling a default only affects newly added items.

---

## Getting Started

### Prerequisites

- Raspberry Pi 5 (4 GB minimum, 8 GB recommended)
- 64-bit Raspberry Pi OS (Bookworm or later)
- External storage with sufficient capacity
- A VPN provider supporting **WireGuard** with a private key you can extract
- A dynamic DNS hostname for remote access

### Deployment

#### 1. Update the system

```bash
sudo apt update && sudo apt upgrade -y
```

#### 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Log out and back in, then verify:

```bash
docker --version
docker compose version
```

#### 3. Install Jellyfin natively

Jellyfin runs as a systemd service rather than a container so it can reach the Pi's
VideoCore decoder without device passthrough.

```bash
curl https://repo.jellyfin.org/install-debuntu.sh | sudo bash
sudo systemctl enable --now jellyfin
```

Available at `http://<pi-ip>:8096`.

**Set `EnableHardwareEncoding` to `false`** in `/etc/jellyfin/encoding.xml`. The
Pi 5 has a decoder but no encoder, and leaving this enabled causes failed transcode
attempts rather than falling back cleanly.

#### 4. Clone the repository

```bash
git clone https://github.com/Derec58/raspberry-pi-media-stack.git
cd raspberry-pi-media-stack
```

#### 5. Configure environment variables

```bash
cp .env.example .env
nano .env
```

See [Configuration](#configuration) for every variable.

#### 6. Add the WireGuard key

Gluetun connects using WireGuard. Put your provider's private key in `.env` as
`WIREGUARD_PRIVATE_KEY`. There is no `.ovpn` file to place; the tunnel is
configured entirely through environment variables in `docker-compose.yml`
(provider, server location, and NAT-PMP port forwarding).

Without a valid key gluetun will not start, and because qBittorrent shares its
network namespace, qBittorrent will have no network access at all. That is the
kill switch working.

#### 7. Mount storage

```bash
lsblk                               # identify your drive
sudo mkdir -p /mnt/jellyfin
sudo mount /dev/sdb1 /mnt/jellyfin  # replace sdb1 with your device
sudo chown -R 1000:1000 /mnt/jellyfin
```

Add to `/etc/fstab` so it remounts on boot:

```
/dev/sdb1  /mnt/jellyfin  ext4  defaults,nofail  0  2
```

`nofail` matters. Without it a missing drive blocks boot. With it, containers can
start **before** the mount is ready and pin an empty directory, which is what
`media-stack-mount-guard` exists to detect.

#### 8. Create literature directories *(optional)*

```bash
bash setup-literature.sh
```

Creates the media and download directories for Shelfmark, Mylar3, Audiobookshelf
and Kavita with correct ownership.

#### 9. Start the stack

```bash
docker compose up -d
docker ps
```

Containers reach `Up` within 30 to 60 seconds. Gluetun, Kavita and Shelfmark have
healthchecks and may briefly show `(health: starting)`.

> Compose aborts if gluetun reports unhealthy, which can strand the containers that
> depend on it. `ops/pi-backup.sh` accounts for this with a longer `start_period`
> and a retry, after a weekly backup once left three services down for days.

#### 10. Install the operational units *(recommended)*

```bash
sudo cp ops/systemd/*.service ops/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now media-stack-mount-guard.timer \
                            media-stack-lan-only-ports.timer \
                            media-stack-extract-subs.timer \
                            media-stack-reap-stalled.timer \
                            media-stack-audit-library.timer \
                            pi-backup.timer
systemctl list-timers 'media-stack-*'
```

---

## Configuration

Copy `.env.example` to `.env`. The `.env` file is gitignored.

| Variable | Description | Example |
|---|---|---|
| `PUID` | User ID for LinuxServer containers. Run `id`. | `1000` |
| `PGID` | Group ID for LinuxServer containers. | `1000` |
| `TZ` | Timezone for all containers. | `America/Los_Angeles` |
| `WIREGUARD_PRIVATE_KEY` | WireGuard private key from your VPN provider. | *(your key)* |
| `KAVITA_API_KEY` | Kavita API key, used by the comic tooling. | *(from Kavita's settings)* |

> `PUID` and `PGID` control file ownership inside LinuxServer containers. If
> downloaded media is inaccessible to Jellyfin, a mismatch here is the most likely
> cause. Media must end up owned `112:122` for Jellyfin to read it.

---

## Literature Stack

A second automation layer for written and audio content, sharing the same Prowlarr
indexers and qBittorrent client.

**Shelfmark** *(port 8084)* handles automated ebook acquisition. It replaced
Readarr, which was retired upstream in 2025. Omnibus was evaluated and rejected
because no arm64 build exists.

**Mylar3** *(port 8090)* is the comics equivalent of Sonarr and Radarr. It tracks
series by issue and downloads through Prowlarr and qBittorrent. Comic matching is
substantially harder than film or TV matching: multiple series share a title across
different years, and packs are frequently mislabelled with the wrong issue range.
`RUNBOOK-books-comics.md` documents the specific failures and the alternate-search
configuration that works around them.

**Kavita** *(port 5000)* serves ebooks, comics, manga and magazines, and supports
OPDS so e-reader apps can browse and sync. Manages its own file permissions.

**Audiobookshelf** *(port 13378)* serves audiobooks and podcasts with first-party
mobile apps. Manages its own file permissions and does not use `PUID`/`PGID`.

---

## Notable Bugs and Fixes

Four findings that were only visible in output, not in the configuration. Each is
recorded with the measurement that revealed it.

### A two-character rule that rejected 80% of all releases

A release profile ignored the term `TS` to block cam rips. The application matches
plain ignored terms as **case-insensitive substrings**, not words, and one series
had those two letters sitting mid-word in its title. Every release for it was
rejected by the cam filter.

```
before:  189 releases returned,  0 acceptable
         151 rejected: "Contains these ignored terms: TS"
after:   210 releases returned, 66 acceptable
```

`HDTS` and `TELESYNC` already covered real cam rips, so the bare term contributed
nothing but collateral damage. Fixed by converting to a word-bounded regex,
`/\bTS\b/`. `CAM` had the same exposure against "camera" and "camp".

**Lesson:** short blocklist terms in a substring matcher fail silently. Nothing logs
"this rule is too broad"; searches simply return nothing and look like
unavailability.

### A regex too narrow to see the problem

The codec filters matched `H.265` and `H265` but **not `H 265`**, and `VC-1` but not
`VC 1`. Space-separated codec names are a common release convention, so those files
bypassed every codec filter in both applications. That is how a VC-1 file ended up
in a library that bans VC-1: a 39 GB file that transcoded on every browser play.

Fixed with a separator class, `h[\s._-]?265`, verified to still pass `H 264` and not
false-match the film "Vice". Twelve previously invisible releases were rejected
immediately afterwards.

**Lesson:** the exact inverse of the bug above. One rule was too broad and blocked
everything; this one was too narrow and blocked nothing. Both failed silently.
`audit-filters.py` now asserts these patterns against real release titles.

### A service installed, connected, and completely inert

Bazarr was running, linked to both automation apps, with a provider configured and
an English language profile already defined. Its own database:

```
table_movies   326 rows,  1 with a language profile
table_shows     39 rows,  0 with a language profile
```

A language profile must be **assigned per item**. Enabling a default only affects
newly added items, so nothing was ever eligible for a subtitle search. The component
was healthy and idle.

Worse: a filter rule rejecting releases for "no English subtitles" had been quietly
compensating for it, and had blocked at least six otherwise good films. The rule
looked sensible in isolation and was masking a broken dependency.

**Lesson:** a rule that compensates for a broken component hides the breakage.

### A measurement generalised into a rule

A transcode speed of **0.249x** real time, measured once on a 4K file with subtitle
burn-in, became the working rule "the Pi cannot transcode". That premise justified
replacing a batch of 1080p HEVC files at a cost of 30.8 GB.

It was wrong. Transcode speed scales with **source resolution**:

```
2160p HEVC   0.19 to 0.34x    unplayable
1080p HEVC   1.43 to 1.51x    comfortable
```

The 0.249x figure was specific to 4K plus burn-in and never generalised. The
replacements were not harmful, but they were not needed.

**Lesson:** a number measured once, in one configuration, is not a rule. This only
surfaced because playback test results contradicted the model; it would otherwise
have stood indefinitely, because it produced plausible-looking work.

---

## Known Limitations

**Infrastructure**

- **Single node.** No redundancy. The Pi is the whole system.
- **No RAID.** One drive failure loses the library.
- **No off-site backup.** Config is backed up to the same physical drive as the media.
- **No alerting.** Timers write logs; nothing notifies on failure.
- **Jellyfin is not in compose.** A rebuild needs the separate native install path.
- **Seven images float on `:latest`**, so a rebuild is not version-reproducible.
- **Gluetun is pinned behind upstream** to avoid a routing regression.

**Library**

- **67 HEVC files** transcode for browser viewers. 51 are 4K, which is the case
  this hardware cannot handle. They direct play in native apps, so this only
  affects browser users.
- **11 remuxes above the bitrate ceiling**, four kept deliberately for quality.
  They direct play but may buffer for remote viewers.
- **Three films the indexers cannot match by title**, returning unrelated results.
  Their searches fail through no fault of the filters.
- **Anime arrives as HEVC** because dual-audio releases predominantly are. Accepted
  deliberately: it transcodes at 1.4x and the alternative is losing the English dub.
- **Some anime season numbering does not match the metadata source**, which needs
  external scene mappings that do not exist for every series.

**Tooling**

- **No Recyclarr or TRaSH Guides integration.** All 22 custom formats are
  hand-built. `audit-filters.py` makes the policy reproducible, but the scoring is
  not derived from a reference set.

---

## Roadmap

- [ ] Alerting on timer failure, so a silent breakage surfaces (`OnFailure=`)
- [ ] Generation-based backups with `--link-dest` rather than a single snapshot
- [ ] Off-site backup target
- [ ] Pin the remaining seven `:latest` images
- [ ] Evaluate Recyclarr to make the filter policy portable
- [ ] Security pass: file permissions on config, credential rotation, SSH hardening, unattended-upgrades
- [ ] Migrate Jellyfin into Docker if hardware decode passthrough becomes reliable on arm64
- [ ] RAID or ZFS for redundancy
- [ ] Add Immich for photos

---

## Background and Lessons

My background is in **Product Design**, with experience in HTML, CSS, Python, C++
and Java. Before this project I had no hands-on experience with systems
administration, Docker networking or infrastructure. Port conflicts, VPN routing,
bind mount mismatches and container dependency chains were all worked through by
research and trial and error.

**Infrastructure is about interactions, not components.** Individual services are
easy to run. Making them agree on paths, permissions and network namespaces
requires holding the whole system in mind at once.

**Follow the file across every boundary.** The most frustrating early failure was
downloads completing but imports failing silently. qBittorrent wrote to
`/data/downloads/file.mkv` while Sonarr looked for `/downloads/file.mkv`.
Standardising every container on `/mnt/jellyfin → /data` fixed it. Trace the path
through each container before assuming the code is wrong.

**Security has to be chosen.** The kill switch via a shared network namespace was a
deliberate decision, not a default. Docker's defaults are permissive; restriction is
opt-in.

**Rules belong in the system, not in a script.** Selection rules first lived in an
ad hoc script, which meant they only applied when that script ran. The applications'
own automation used none of them, and kept grabbing files the script would have
rejected. Moving every rule into the *arr configuration, then writing an audit to
assert it, is what made the policy real.

**Measure, then re-measure.** The single most expensive mistake here was trusting
one measurement as a general rule. It produced work that looked correct and was
unnecessary. The fix was not a better tool; it was testing a claim that had gone
unquestioned.

---

## Acknowledgments

Built on the open source selfhosted ecosystem. Thanks to the LinuxServer.io team,
the \*arr maintainers, the Gluetun project, Bazarr, Kavita, Mylar3, Shelfmark,
Audiobookshelf, and the communities at [r/selfhosted](https://reddit.com/r/selfhosted)
and [r/homelab](https://reddit.com/r/homelab).

---

## License

MIT License. Free to use, adapt and share for personal and educational purposes.
