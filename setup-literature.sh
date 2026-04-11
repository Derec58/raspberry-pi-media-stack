#!/bin/bash
# Run this on the Pi BEFORE docker compose up -d
# From: /home/korn/media-stack

set -e

echo "==> Creating media library folders..."
sudo mkdir -p \
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

echo "==> Setting ownership on media folders (adjust 1000:1000 if your PUID/PGID differs)..."
sudo chown -R 1000:1000 \
  "/mnt/jellyfin/Books" \
  "/mnt/jellyfin/Audiobooks" \
  "/mnt/jellyfin/Comics" \
  "/mnt/jellyfin/Magazines" \
  "/mnt/jellyfin/downloads/books" \
  "/mnt/jellyfin/downloads/comics"

echo "==> Setting ownership on config folders..."
chown -R 1000:1000 \
  ./config/bookshelf \
  ./config/mylar3 \
  ./config/audiobookshelf \
  ./config/kavita

echo "==> Done. You can now run: docker compose up -d"
