#!/bin/bash
# Run this on the Pi BEFORE docker compose up -d
# From: /home/korn/media-stack

set -e

# Load PUID/PGID from .env so ownership matches the running stack
if [ -f .env ]; then
  export $(grep -E '^(PUID|PGID)=' .env | xargs)
fi
PUID=${PUID:-1000}
PGID=${PGID:-1000}

echo "==> Using PUID=$PUID PGID=$PGID (from .env)"

echo "==> Creating media library folders..."
sudo mkdir -p \
  "/mnt/jellyfin/Books" \
  "/mnt/jellyfin/Audiobooks" \
  "/mnt/jellyfin/Comics" \
  "/mnt/jellyfin/Magazines" \
  "/mnt/jellyfin/downloads/books" \
  "/mnt/jellyfin/downloads/comics"

echo "==> Setting ownership on media folders..."
sudo chown -R "$PUID:$PGID" \
  "/mnt/jellyfin/Books" \
  "/mnt/jellyfin/Audiobooks" \
  "/mnt/jellyfin/Comics" \
  "/mnt/jellyfin/Magazines" \
  "/mnt/jellyfin/downloads/books" \
  "/mnt/jellyfin/downloads/comics"

echo "==> Creating container config folders..."
mkdir -p \
  ./config/bookshelf \
  ./config/mylar3 \
  ./config/audiobookshelf/metadata \
  ./config/kavita

# Bookshelf and Mylar3 use PUID/PGID — set ownership so they can write configs
echo "==> Setting ownership on Bookshelf and Mylar3 config folders..."
sudo chown -R "$PUID:$PGID" \
  ./config/bookshelf \
  ./config/mylar3

# Audiobookshelf and Kavita manage their own permissions internally —
# do not chown their config dirs or the containers may fail to start

echo "==> Done. You can now run: docker compose up -d"
