# Raspberry Pi Media Stack (Docker + Jellyfin)

This repo contains my Raspberry Pi media automation stack.

## What runs where
- **Jellyfin**: native system service (currently) on port **8096**
- **Docker stack**:
  - Gluetun (VPN gateway)
  - qBittorrent (routed through Gluetun)
  - Prowlarr
  - Sonarr
  - Radarr
  - Bazarr
  - Jellyseerr

## Ports (LAN)
- Jellyfin: `8096`
- Jellyseerr: `5055`
- Sonarr: `8989`
- Radarr: `7878`
- Prowlarr: `9696`
- Bazarr: `6767`
- qBittorrent WebUI (via Gluetun): `8080`
- Torrent port (via Gluetun): `6881` TCP/UDP

## Storage layout (host)
Mounted media drive:
- `/mnt/jellyfin`

Folders:
- Downloads: `/mnt/jellyfin/downloads`
- Movies: `/mnt/jellyfin/Movies`
- TV: `/mnt/jellyfin/TV Shows`
- Anime: `/mnt/jellyfin/Anime`

## Quick start
1. Copy `.env.example` to `.env` and fill in values
2. Put your OpenVPN config at `vpn/custom.ovpn`
3. Start the stack:

```bash
docker compose up -d
