#!/usr/bin/env bash
# sync-qbit-vpn-ip.sh
# Keeps qBittorrent in sync with gluetun's VPN state:
#   1. Session\Interface = current tun0 IP (config edit + restart on change)
#   2. listen_port = gluetun's forwarded port (live API update, no restart)
#
# Background: qBittorrent must bind to the tun0 IP (not eth0) to route through
# gluetun's VPN. If the VPN reconnects and assigns a new tunnel IP, qBit's
# sockets silently stop working. ProtonVPN's NAT-PMP forwarded port also
# changes across sessions, and qBittorrent reverts to its stored port on
# restart, so the port must be re-pushed whenever the two disagree.
#
# Run via systemd: media-stack-vpn-sync.service (polls every 30s)

set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="$COMPOSE_DIR/config/qbittorrent/qBittorrent/qBittorrent.conf"
LOG="$COMPOSE_DIR/logs/vpn-ip-sync.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

qbit_api() {
    docker exec qbittorrent curl -s --max-time 10 "$@"
}

# ─── 1. tun0 IP → Session\Interface ───────────────────────────────────────

TUN_IP=$(docker exec gluetun ip addr show tun0 2>/dev/null \
    | grep 'inet ' \
    | awk '{print $2}' \
    | cut -d/ -f1 \
    || true)

if [[ -n "$TUN_IP" ]]; then
    CONF_IP=$(grep "^Session\\\\Interface=" "$CONF" 2>/dev/null \
        | cut -d= -f2 \
        || true)

    if [[ "$TUN_IP" != "$CONF_IP" ]]; then
        log "VPN tunnel IP changed: '${CONF_IP:-<unset>}' -> '$TUN_IP'"

        # Stop qBit so it doesn't overwrite the config on shutdown
        cd "$COMPOSE_DIR"
        docker compose stop qbittorrent >> "$LOG" 2>&1

        sed -i "s|^Session\\\\Interface=.*|Session\\\\Interface=$TUN_IP|" "$CONF"

        docker compose start qbittorrent >> "$LOG" 2>&1
        log "qBittorrent restarted with interface $TUN_IP"
    fi
fi

# ─── 2. forwarded port → listen_port ──────────────────────────────────────
# (after a restart above, the qBit API may not be up yet — the next 30s
# iteration will catch it, so every failure here is a silent skip)

PF_PORT=$(docker exec gluetun wget -qO- --timeout=10 \
    http://127.0.0.1:8000/v1/openvpn/portforwarded 2>/dev/null \
    | grep -o '[0-9]\+' \
    || true)

if [[ -n "$PF_PORT" && "$PF_PORT" != "0" ]]; then
    QBIT_PORT=$(qbit_api http://localhost:8080/api/v2/app/preferences 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["listen_port"])' 2>/dev/null \
        || true)

    if [[ -n "$QBIT_PORT" && "$QBIT_PORT" != "$PF_PORT" ]]; then
        if qbit_api --data "json={\"listen_port\":$PF_PORT}" \
            http://localhost:8080/api/v2/app/setPreferences >/dev/null 2>&1; then
            log "Forwarded port changed: $QBIT_PORT -> $PF_PORT (pushed to qBittorrent)"
        fi
    fi
fi
