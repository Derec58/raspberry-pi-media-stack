Raspberry Pi Media Stack (Docker + Jellyfin)

This repository documents my Raspberry Pi media automation stack built using Docker, a VPN gateway, and Jellyfin.

The goal of this project is to create a reproducible, secure, and modular self-hosted media system.

Architecture Overview
flowchart TB
  U[User Devices<br/>TV / Phone / PC] -->|LAN| R[Home Router]
  R --> PI[Raspberry Pi Server]

  subgraph STORAGE[External Media Drive]
    DRV[/dev/sdb1<br/>/mnt/jellyfin/]
    DL[/downloads/]
    MOV[/Movies/]
    TV[/TV Shows/]
    AN[/Anime/]
    DRV --> DL
    DRV --> MOV
    DRV --> TV
    DRV --> AN
  end

  subgraph NATIVE[Jellyfin (Native Service)]
    JF[Jellyfin<br/>Port 8096]
  end

  subgraph DOCKER[Docker Media Stack]
    JS[Jellyseerr<br/>5055]
    SO[Sonarr<br/>8989]
    RA[Radarr<br/>7878]
    PR[Prowlarr<br/>9696]
    BZ[Bazarr<br/>6767]

    subgraph VPN[Gluetun VPN Gateway]
      GL[Gluetun<br/>Firewall ON]
      QB[qBittorrent<br/>8080 / 6881]
      GL --> QB
    end

    JS --> SO
    JS --> RA
    SO --> PR
    RA --> PR
    PR --> QB
    QB --> DL
    SO --> TV
    RA --> MOV
    BZ --> MOV
    BZ --> TV
    BZ --> AN
  end

  U -->|http://pi:8096| JF
  JF --> MOV
  JF --> TV
  JF --> AN
What Runs Where
Native Service

Jellyfin

Runs via systemd

Port: 8096

Reads media directly from /mnt/jellyfin

Docker Stack

Compose project located at:

/home/korn/media-stack

Services:

Gluetun – VPN gateway with firewall

qBittorrent – Routed through Gluetun

Prowlarr – Indexer manager

Sonarr – TV automation

Radarr – Movie automation

Bazarr – Subtitle management

Jellyseerr – User request interface

Ports (LAN Access)
Service	Port
Jellyfin	8096
Jellyseerr	5055
Sonarr	8989
Radarr	7878
Prowlarr	9696
Bazarr	6767
qBittorrent	8080
Torrent Port	6881 TCP/UDP
Storage Layout (Host)

Mounted media drive:

/mnt/jellyfin

Structure:

/mnt/jellyfin/
├── downloads/
├── Movies/
├── TV Shows/
└── Anime/

Inside containers this path is mounted as:

/data

This ensures all services operate on the same file structure.

Why This Architecture?
1. Separation of Responsibilities

Each service has a single purpose:

Sonarr/Radarr → automation & organization

Prowlarr → indexer abstraction layer

qBittorrent → downloading only

Bazarr → subtitles only

Jellyfin → streaming only

This makes debugging easier and prevents cascading failures.

2. Consistent Volume Mapping

All media containers bind:

/mnt/jellyfin → /data

This prevents:

Path mismatches

Import failures

Hardlink issues

Media duplication

3. Infrastructure as Code

The entire stack is defined in:

docker-compose.yml

This allows:

Full rebuilds

Easy migration

Version control

Portable deployment

Security Considerations
VPN Enforcement

qBittorrent is forced through Gluetun using:

network_mode: "service:gluetun"

This means:

qBittorrent shares Gluetun’s network namespace

It cannot bypass the VPN

If Gluetun fails, torrent traffic stops

Firewall Protection

Gluetun is configured with:

FIREWALL=on

Explicit inbound ports (8080, 6881)

Restricted outbound LAN subnet (192.168.0.0/16)

Only necessary ports are exposed.

Secret Management

The repository excludes:

.env

vpn/

config/

Only .env.example is committed.

Quick Start

Copy .env.example → .env

Add your OpenVPN config to:

vpn/custom.ovpn

Start the stack:

docker compose up -d
Future Roadmap
Migrate Jellyfin to Docker

Replace native service with linuxserver/jellyfin

Unify entire stack under Docker

Simplify backup strategy

Move to Proxmox (Mini PC)

Run stack inside VM or LXC

Add ZFS or RAID storage

Improve performance & scalability

Backups & Disaster Recovery

Automated backup of:

docker-compose.yml

.env

config/

Jellyfin metadata

Scheduled rsync to secondary storage

Monitoring & Alerts

Add Uptime Kuma

Disk usage monitoring

Container health checks

License

Personal infrastructure project for educational and self-hosting purposes.
