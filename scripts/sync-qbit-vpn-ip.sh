#!/usr/bin/env bash
# sync-qbit-vpn-ip.sh
# Keeps qBittorrent's Session\Interface in sync with the current tun0 IP.
#
# Background: qBittorrent must bind to the tun0 IP (not eth0) to route through
# gluetun's VPN. If the VPN reconnects and assigns a new tunnel IP, qBit's
# sockets silently stop working. This script detects the change and restarts
# qBit with the correct IP.
#
# Run via systemd: media-stack-vpn-sync.service (polls every 30s)

set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="$COMPOSE_DIR/config/qbittorrent/qBittorrent/qBittorrent.conf"
LOG="$COMPOSE_DIR/logs/vpn-ip-sync.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

# --- Get current tun0 IP from gluetun container ---
TUN_IP=$(docker exec gluetun ip addr show tun0 2>/dev/null \
    | grep 'inet ' \
    | awk '{print $2}' \
    | cut -d/ -f1 \
    || true)

if [[ -z "$TUN_IP" ]]; then
    # tun0 not up yet (VPN connecting) — nothing to do
    exit 0
fi

# --- Get IP currently set in qBittorrent config ---
CONF_IP=$(grep "^Session\\\\Interface=" "$CONF" 2>/dev/null \
    | cut -d= -f2 \
    || true)

# --- No change — fast exit (common case) ---
if [[ "$TUN_IP" == "$CONF_IP" ]]; then
    exit 0
fi

# --- IP changed: update config and restart qBittorrent ---
log "VPN tunnel IP changed: '${CONF_IP:-<unset>}' -> '$TUN_IP'"

# Stop qBit so it doesn't overwrite the config on shutdown
cd "$COMPOSE_DIR"
docker compose stop qbittorrent >> "$LOG" 2>&1

# Update Session\Interface with the new tun0 IP
sed -i "s|^Session\\\\Interface=.*|Session\\\\Interface=$TUN_IP|" "$CONF"

# Start qBit back up (depends_on: service_healthy ensures VPN is ready)
docker compose start qbittorrent >> "$LOG" 2>&1

log "qBittorrent restarted with interface $TUN_IP"
