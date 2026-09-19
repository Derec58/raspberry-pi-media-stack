#!/usr/bin/env bash
# lan-only-ports.sh
# Restricts this stack's Docker-published web UIs to LAN sources only.
#
# Background: every service in docker-compose.yml publishes its port on
# 0.0.0.0, which is fine on its own -- the Pi sits on a private LAN behind a
# gateway. But on 2026-09-16 qBittorrent's log showed internet-wide scanners
# (Censys 167.94.146.50, 147.185.133.141, 67.205.164.100, 159.89.30.37 and
# others) reaching its WebUI HTTP parser on 8080 with HTTP/2 prefaces, RTSP,
# SIP and MSMQ probe payloads. Gluetun's own firewall was NOT the hole: it
# scopes 8080/8888 to eth0 and opens only the NAT-PMP forwarded port on tun0.
# The traffic therefore arrived via a port-forward on the upstream gateway.
#
# Nothing here is meant to be reachable from the internet. Remote access is
# Nginx Proxy Manager's job -- it terminates TLS on 80/443 and proxies the
# services deliberately published over duckdns (Jellyfin, Jellyseerr/Seerr,
# Kavita, and -- since 2026-09-16 -- qBittorrent behind an NPM access list).
#
# NOTE: an earlier version of this comment claimed qBittorrent "has no proxy
# host and never did". That was wrong: the proxy host was created the same day
# this script was written. Corrected 2026-09-18.
#
# DOCKER-USER is the correct hook: Docker's published ports are DNAT'd and
# traverse FORWARD, bypassing INPUT entirely, so an INPUT rule would do
# nothing. Only NEW inbound connections are matched, so container egress and
# established flows are untouched.
#
# Idempotent by design -- safe to re-run. Run via systemd:
# media-stack-lan-only-ports.{service,timer}

set -euo pipefail

CHAIN="MEDIA_LAN_ONLY"

# Web UIs published by docker-compose.yml. NPM (80/443/81) and Jellyfin (8096)
# run in the host netns, not behind DOCKER-USER, and are handled separately.
PORTS="8080,6767,8191,9696,7878,8989,5055,5000,13378,8084,8090"

PRIVATE_NETS=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 127.0.0.0/8)

# ─── 1. (re)build the decision chain ──────────────────────────────────────
if iptables -n -L "$CHAIN" >/dev/null 2>&1; then
    iptables -F "$CHAIN"
else
    iptables -N "$CHAIN"
fi

for net in "${PRIVATE_NETS[@]}"; do
    iptables -A "$CHAIN" -s "$net" -j RETURN
done
iptables -A "$CHAIN" -j DROP

# ─── 2. point DOCKER-USER at it exactly once ──────────────────────────────
# Delete any existing jump first so repeated runs cannot stack duplicates.
while iptables -C DOCKER-USER -p tcp -m conntrack --ctstate NEW \
        -m multiport --dports "$PORTS" -j "$CHAIN" 2>/dev/null; do
    iptables -D DOCKER-USER -p tcp -m conntrack --ctstate NEW \
        -m multiport --dports "$PORTS" -j "$CHAIN"
done

iptables -I DOCKER-USER 1 -p tcp -m conntrack --ctstate NEW \
    -m multiport --dports "$PORTS" -j "$CHAIN"
