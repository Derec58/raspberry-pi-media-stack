#!/bin/bash
# Weekly cold backup of the Pi to /mnt/backup (WD10JDRW 1TB, label pi-backup).
# Layout mirrors /mnt/jellyfin/pi-backup, the drive that saved us on 2026-07-07:
#   system/etc/, system/*.txt, jellyfin/etc/, jellyfin/varlib/, home-korn/
# Jellyfin and the media-stack containers are stopped during the rsync so
# every SQLite DB (jellyfin, *arr apps, qbittorrent) is captured consistent.
set -euo pipefail

DEST=/mnt/backup
COMPOSE=/home/korn/media-stack/docker-compose.yml

mountpoint -q "$DEST" || { echo "ERROR: $DEST is not mounted, aborting"; exit 1; }
mkdir -p "$DEST"/system "$DEST"/jellyfin

echo "Stopping services for consistent backup..."
systemctl stop jellyfin
docker compose -f "$COMPOSE" stop

# Bring the stack back, retrying on failure.
#
# A plain `up -d --wait || true` is not enough. Compose treats a dependency
# that reports *unhealthy* as a hard abort, not as something to keep waiting
# on, so a Gluetun that is slow to finish its VPN handshake makes Compose give
# up immediately on every service gated behind it (qbittorrent, mylar3,
# shelfmark) -- long before --wait-timeout is relevant. Gluetun then goes
# healthy seconds later, but nothing re-attempts the skipped containers, and
# because they never started, `restart: unless-stopped` does not apply either.
# They just stay down. That is what happened on 2026-09-06 and 2026-09-13:
# the backup "succeeded" and the download stack was silently dead for days.
#
# Gluetun's start_period in docker-compose.yml is the real fix; this retry is
# the backstop, and it also makes the failure loud instead of swallowed.
restart_stack() {
    local attempt
    for attempt in 1 2 3; do
        if docker compose -f "$COMPOSE" up -d --wait --wait-timeout 300; then
            echo "Stack restarted successfully (attempt $attempt)"
            return 0
        fi
        echo "WARN: stack restart attempt $attempt failed; retrying in 30s"
        sleep 30
    done

    echo "ERROR: stack failed to restart after 3 attempts. Not running:"
    docker compose -f "$COMPOSE" ps -a --format '{{.Name}}\t{{.State}}' \
        | grep -v 'running' || true
    return 1
}

trap 'restart_stack || true; systemctl start jellyfin' EXIT

echo "Backing up /etc + system state..."
rsync -a --delete /etc/ "$DEST/system/etc/"
dpkg-query -f '${binary:Package}\n' -W > "$DEST/system/dpkg-list.txt"
systemctl list-unit-files --state=enabled --no-legend | awk '{print $1}' > "$DEST/system/enabled-units.txt"
blkid > "$DEST/system/blkid.txt"
cp /etc/fstab "$DEST/system/fstab"

echo "Backing up jellyfin..."
rsync -a --delete /etc/jellyfin/ "$DEST/jellyfin/etc/"
rsync -a --delete /var/lib/jellyfin/ "$DEST/jellyfin/varlib/"

echo "Backing up /home/korn (incl. media-stack repo + container configs)..."
rsync -a --delete \
  --exclude '.cache/' \
  --exclude '.npm/' \
  --exclude 'media-stack/logs/' \
  --exclude 'media-stack/config/qbittorrent/.cache/' \
  /home/korn/ "$DEST/home-korn/"

date -Is > "$DEST/last-backup.txt"
echo "Backup complete: $(df -h --output=used,avail "$DEST" | tail -1)"
