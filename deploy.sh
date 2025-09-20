#!/usr/bin/env bash
set -euo pipefail

# --- Config ---
REPO="/home/ryan/code/spotify-status"
SRC="${REPO}/channels/spotify_status/"
DST="/var/opt/mimir/mimir-api/channels/spotify_status"

# Files we want the option to preserve across deploys (authorization/token + settings)
AUTH_FILES=("data/.spotify_cache" "data/settings.json")

PRESERVE_AUTH=0

usage() {
	cat <<EOF
Usage: $(basename "$0") [--preserve-auth] [--reset-auth]

Deploy the spotify-status channel to mimir-api directory.

Options:
	--preserve-auth   Backup and restore Spotify auth/cache + settings.json so re-authorization not required.
	--reset-auth      Remove cached auth after deploy (forces fresh OAuth on next use).
	-h, --help        Show this help.

If neither option supplied, auth cache will be preserved by default unless --reset-auth provided.
EOF
}

# Parse arguments
RESET_AUTH=0
while [[ $# -gt 0 ]]; do
	case "$1" in
		--preserve-auth) PRESERVE_AUTH=1; shift ;;
		--reset-auth) RESET_AUTH=1; PRESERVE_AUTH=0; shift ;;
		-h|--help) usage; exit 0 ;;
		*) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
	esac
done

# Default preserve if neither explicitly set
if [[ $PRESERVE_AUTH -eq 0 && $RESET_AUTH -eq 0 ]]; then
	PRESERVE_AUTH=1
fi

echo "Preserve auth cache: $([[ $PRESERVE_AUTH -eq 1 ]] && echo yes || echo no)"



# --- Update & build first ---
echo "Updating repo: ${REPO} ..."
git -C "${REPO}" fetch --quiet
git -C "${REPO}" pull --ff-only


# --- Ensure destination exists with correct ownership/permissions ---
sudo install -d -m 2775 -o mimir -g mimir "${DST}"


TMP_BACKUP="$(mktemp -d)"
cleanup() { rm -rf "$TMP_BACKUP" || true; }
trap cleanup EXIT

if [[ $PRESERVE_AUTH -eq 1 ]]; then
	echo "Backing up auth-related files (if present): ${AUTH_FILES[*]}"
	for f in "${AUTH_FILES[@]}"; do
		if [[ -f "${DST}/${f}" ]]; then
			mkdir -p "${TMP_BACKUP}/$(dirname "$f")"
			cp -p "${DST}/${f}" "${TMP_BACKUP}/${f}" || true
		fi
	done
fi

echo "Syncing ALL content (rsync --delete)"
sudo rsync -a --delete --chown=mimir:mimir "${SRC}" "${DST}"

if [[ $PRESERVE_AUTH -eq 1 ]]; then
	echo "Restoring preserved auth files"
	for f in "${AUTH_FILES[@]}"; do
		if [[ -f "${TMP_BACKUP}/${f}" ]]; then
			mkdir -p "${DST}/$(dirname "$f")"
			cp -p "${TMP_BACKUP}/${f}" "${DST}/${f}" || true
			sudo chown mimir:mimir "${DST}/${f}" || true
		fi
	done
elif [[ $RESET_AUTH -eq 1 ]]; then
	echo "Resetting auth cache files (${AUTH_FILES[*]})"
	for f in "${AUTH_FILES[@]}"; do
		rm -f "${DST}/${f}" || true
	done
fi


# --- Final ownership pass (covers preserved folders) ---
sudo chown -R mimir:mimir "${DST}"

echo "✅ Deployed: ${SRC} → ${DST}"
