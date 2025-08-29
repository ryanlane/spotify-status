#!/usr/bin/env bash
set -euo pipefail

# --- Config ---
REPO="/home/ryan/code/spotify-status"
SRC="${REPO}/channels/spotify_status/"
DST="/var/opt/mimir/mimir-api/channels/spotify_status"



# --- Update & build first ---
echo "Updating repo: ${REPO} ..."
git -C "${REPO}" fetch --quiet
git -C "${REPO}" pull --ff-only


# --- Ensure destination exists with correct ownership/permissions ---
sudo install -d -m 2775 -o mimir -g mimir "${DST}"


echo "Syncing ALL content"
# Everything fresh; rsync sets ownership as it copies
sudo rsync -a --delete --chown=mimir:mimir "${SRC}" "${DST}"


# --- Final ownership pass (covers preserved folders) ---
sudo chown -R mimir:mimir "${DST}"

echo "✅ Deployed: ${SRC} → ${DST}"
