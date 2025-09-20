#!/usr/bin/env bash
set -euo pipefail

# --- Config (can be overridden via environment) ---
REPO="${REPO:-/home/ryan/code/spotify-status}"
DST="${DST:-/var/opt/mimir/mimir-api/channels/spotify_status}"
SRC="${SRC:-${REPO}/channels/spotify_status/}"

# Sudo wrapper; allow disabling with --no-sudo or SUDO="" env
SUDO=${SUDO:-sudo}

# Files we want the option to preserve across deploys (authorization/token + settings)
AUTH_FILES=("data/.spotify_cache" "data/settings.json")

PRESERVE_AUTH=0

usage() {
	cat <<EOF
Usage: $(basename "$0") [options]

Deploy the spotify-status channel to mimir-api directory.

Options:
	--preserve-auth   Backup and restore Spotify auth/cache + settings.json so re-authorization not required.
	--reset-auth      Remove cached auth after deploy (forces fresh OAuth on next use).
	--dry-run         Show what would change (rsync --dry-run) and exit; always preserves auth.
	--no-sudo         Run without sudo (assumes current user has proper permissions).
	-h, --help        Show this help.

If neither option supplied, auth cache will be preserved by default unless --reset-auth provided.
EOF
}

# Parse arguments
RESET_AUTH=0
DRY_RUN=0
NO_SUDO=0
while [[ $# -gt 0 ]]; do
	case "$1" in
		--preserve-auth) PRESERVE_AUTH=1; shift ;;
		--reset-auth) RESET_AUTH=1; PRESERVE_AUTH=0; shift ;;
		--dry-run) DRY_RUN=1; shift ;;
		--no-sudo) NO_SUDO=1; SUDO=""; shift ;;
		-h|--help) usage; exit 0 ;;
		*) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
	esac
done

# Default preserve if neither explicitly set
if [[ $PRESERVE_AUTH -eq 0 && $RESET_AUTH -eq 0 ]]; then
	PRESERVE_AUTH=1
fi

echo "Preserve auth cache: $([[ $PRESERVE_AUTH -eq 1 ]] && echo yes || echo no)"
echo "Dry run: $([[ $DRY_RUN -eq 1 ]] && echo yes || echo no)"
echo "Using sudo: $([[ -n $SUDO ]] && echo yes || echo no)"
echo "SRC=$SRC"
echo "DST=$DST"



# --- Update & build first ---
if [[ $DRY_RUN -eq 1 ]]; then
	echo "[dry-run] Skipping git fetch/pull"
else
	echo "Updating repo: ${REPO} ..."
	git -C "${REPO}" fetch --quiet
	git -C "${REPO}" pull --ff-only
fi


# --- Ensure destination exists with correct ownership/permissions ---
${SUDO} install -d -m 2775 -o mimir -g mimir "${DST}"


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

echo "Syncing ALL content (rsync --delete${DRY_RUN:+ --dry-run})"
${SUDO} rsync -a ${DRY_RUN:+--dry-run} --delete ${SUDO:+--chown=mimir:mimir} "${SRC}" "${DST}" || {
	echo "rsync failed" >&2; exit 1;
}

if [[ $DRY_RUN -eq 1 ]]; then
	echo "[dry-run] Stopping before restore/reset phase"
	exit 0
fi

if [[ $PRESERVE_AUTH -eq 1 ]]; then
	echo "Restoring preserved auth files"
	for f in "${AUTH_FILES[@]}"; do
		if [[ -f "${TMP_BACKUP}/${f}" ]]; then
			mkdir -p "${DST}/$(dirname "$f")"
			cp -p "${TMP_BACKUP}/${f}" "${DST}/${f}" || true
			${SUDO} chown mimir:mimir "${DST}/${f}" || true
		fi
	done
elif [[ $RESET_AUTH -eq 1 ]]; then
	echo "Resetting auth cache files (${AUTH_FILES[*]})"
	for f in "${AUTH_FILES[@]}"; do
		rm -f "${DST}/${f}" || true
	done
fi


# --- Final ownership pass (covers preserved folders) ---
${SUDO} chown -R mimir:mimir "${DST}"

echo "✅ Deployed: ${SRC} → ${DST}"
