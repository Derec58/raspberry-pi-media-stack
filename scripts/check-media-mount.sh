#!/usr/bin/env bash
# check-media-mount.sh
# Detects (and heals) containers whose media bind mount points at the wrong
# filesystem, and re-creates them so they see the real 6TB drive.
#
# Background: Docker resolves a bind mount's source at container start and
# pins it for the container's lifetime. /mnt/jellyfin is a USB drive with
# 'nofail' in fstab, so if Docker starts its containers before
# mnt-jellyfin.mount has activated, the bind captures the bare mountpoint
# directory on the root SSD instead. Nothing errors: the containers come up
# healthy, pointed at an empty stub, and stay that way until re-created.
# That is exactly what happened on 2026-09-07, and it stalled every torrent
# for three days ("Permission denied" on file_open, missing files, endless
# queues) because uid 112 cannot mkdir inside a root-owned stub.
#
# The check is a filesystem-identity comparison, not a path comparison:
# st_dev of the host mount vs st_dev of each bind destination inside the
# container. Bind sources are read from Docker itself rather than hardcoded,
# so this keeps working when docker-compose.yml gains or moves a mount.
#
# Run via systemd: media-stack-mount-guard.service (polls every 60s)

set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MEDIA_ROOT="/mnt/jellyfin"
LOG="$COMPOSE_DIR/logs/mount-guard.log"

mkdir -p "$(dirname "$LOG")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

# ─── 1. the drive itself must be mounted ──────────────────────────────────
# If it is not, there is nothing to heal onto — re-creating containers now
# would only re-pin them to the stub. Bail and let the next tick retry.

if ! mountpoint -q "$MEDIA_ROOT"; then
    log "WARN $MEDIA_ROOT is not a mountpoint — media drive absent, skipping"
    exit 0
fi

HOST_DEV=$(stat -c %d "$MEDIA_ROOT")

# ─── 2. find every container bind-mounting something under the drive ──────

STALE=()

for CID in $(docker ps --format '{{.Names}}'); do
    # "<host source>|<container destination>" for each bind under MEDIA_ROOT
    MOUNTS=$(docker inspect "$CID" \
        --format '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}{{end}}' \
        2>/dev/null | grep "^${MEDIA_ROOT}" || true)

    [[ -z "$MOUNTS" ]] && continue

    while IFS='|' read -r src dest; do
        [[ -z "$dest" ]] && continue

        CDEV=$(docker exec "$CID" stat -c %d "$dest" 2>/dev/null || echo "")

        if [[ -z "$CDEV" ]]; then
            log "WARN $CID: cannot stat $dest inside container"
            continue
        fi

        if [[ "$CDEV" != "$HOST_DEV" ]]; then
            log "STALE $CID: $dest is on fsid $CDEV, expected $HOST_DEV (src $src)"
            STALE+=("$CID")
            break
        fi
    done <<< "$MOUNTS"
done

# ─── 3. heal ──────────────────────────────────────────────────────────────

if [[ ${#STALE[@]} -eq 0 ]]; then
    exit 0
fi

# De-duplicate; a container may have several stale mounts.
mapfile -t TARGETS < <(printf '%s\n' "${STALE[@]}" | sort -u)

log "Re-creating ${#TARGETS[@]} container(s) with a stale media mount: ${TARGETS[*]}"

# --force-recreate is the point: a plain restart re-uses the pinned mount and
# would change nothing. Compose service names match container_name throughout
# this stack, so the container names double as service names.
if (cd "$COMPOSE_DIR" && docker compose up -d --force-recreate "${TARGETS[@]}" >>"$LOG" 2>&1); then
    log "Re-create finished; verifying"
else
    log "ERROR docker compose up -d --force-recreate failed for: ${TARGETS[*]}"
    exit 1
fi

# ─── 4. verify the heal actually took ─────────────────────────────────────

sleep 5
FAILED=()
for CID in "${TARGETS[@]}"; do
    DESTS=$(docker inspect "$CID" \
        --format '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}{{end}}' \
        2>/dev/null | grep "^${MEDIA_ROOT}" | cut -d'|' -f2 || true)

    while read -r dest; do
        [[ -z "$dest" ]] && continue
        CDEV=$(docker exec "$CID" stat -c %d "$dest" 2>/dev/null || echo "")
        [[ "$CDEV" != "$HOST_DEV" ]] && FAILED+=("$CID:$dest")
    done <<< "$DESTS"
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
    log "ERROR still stale after re-create: ${FAILED[*]} — dockerd itself may hold a stale namespace; 'systemctl restart docker' may be required"
    exit 1
fi

log "OK all media mounts now resolve to the real drive (fsid $HOST_DEV)"
