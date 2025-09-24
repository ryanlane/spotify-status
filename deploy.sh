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
	--no-git          Do not git fetch/pull before syncing.
	--from-here       Use current working copy as SRC (./channels/spotify_status/).
	--verbose         Verbose rsync (prints itemized changes) and script tracing of key steps.
	--checksum        Use rsync --checksum (slower, ignores mtimes) to force detect all differences.
	--restart         Restart mimir-api systemd service after deploy.
	-h, --help        Show this help.

If neither option supplied, auth cache will be preserved by default unless --reset-auth provided.
EOF
}

# Parse arguments
RESET_AUTH=0
DRY_RUN=0
NO_SUDO=0
NO_GIT=0
FROM_HERE=0
VERBOSE=0
USE_CHECKSUM=0
RESTART=0
while [[ $# -gt 0 ]]; do
	case "$1" in
		--preserve-auth) PRESERVE_AUTH=1; shift ;;
		--reset-auth) RESET_AUTH=1; PRESERVE_AUTH=0; shift ;;
		--dry-run) DRY_RUN=1; shift ;;
		--no-sudo) NO_SUDO=1; SUDO=""; shift ;;
		--no-git) NO_GIT=1; shift ;;
		--from-here) FROM_HERE=1; shift ;;
		--verbose) VERBOSE=1; shift ;;
		--checksum) USE_CHECKSUM=1; shift ;;
		--restart) RESTART=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
	esac
done

# Default preserve if neither explicitly set
if [[ $PRESERVE_AUTH -eq 0 && $RESET_AUTH -eq 0 ]]; then
	PRESERVE_AUTH=1
fi

# Derive SRC from current directory if requested
if [[ $FROM_HERE -eq 1 ]]; then
	SRC="$(pwd)/channels/spotify_status/"
fi

# Prepare rsync flags
RSYNC_FLAGS=( -a --delete )
if [[ $VERBOSE -eq 1 ]]; then
	RSYNC_FLAGS+=( -v --progress --itemize-changes )
fi
if [[ $USE_CHECKSUM -eq 1 ]]; then
	RSYNC_FLAGS+=( --checksum )
fi
if [[ -n ${SUDO} ]]; then
	RSYNC_FLAGS+=( --chown=mimir:mimir )
fi
if [[ $DRY_RUN -eq 1 ]]; then
	RSYNC_FLAGS+=( --dry-run )
fi

# Summary
echo "Preserve auth cache: $([[ $PRESERVE_AUTH -eq 1 ]] && echo yes || echo no)"
echo "Dry run: $([[ $DRY_RUN -eq 1 ]] && echo yes || echo no)"
echo "Using sudo: $([[ -n $SUDO ]] && echo yes || echo no)"
echo "Use checksum: $([[ $USE_CHECKSUM -eq 1 ]] && echo yes || echo no)"
echo "Verbose: $([[ $VERBOSE -eq 1 ]] && echo yes || echo no)"
[[ $FROM_HERE -eq 1 ]] && echo "SRC derived from current working copy" || true
echo "SRC=$SRC"
echo "DST=$DST"

# --- Update & build first ---
if [[ $DRY_RUN -eq 1 || $NO_GIT -eq 1 ]]; then
	echo "[skip] git fetch/pull (dry-run or --no-git)"
else
	echo "Updating repo: ${REPO} ..."
	git -C "${REPO}" fetch --quiet
	git -C "${REPO}" pull --ff-only
fi

# --- Ensure destination exists with correct ownership/permissions ---
${SUDO} install -d -m 2775 -o mimir -g mimir "${DST}"

# Quick visibility of key deltas before rsync
key_files=( channel.py renderer.py routes/main.py ui/index.esm.js ui/manage.esm.js )
show_stat() {
	local label=$1; local base=$2
	for f in "${key_files[@]}"; do
		local p="${base%/}/$f"
		if ${SUDO} test -e "$p"; then
			local sz mtime
			sz=$(${SUDO} stat -c %s "$p" 2>/dev/null || echo "-")
			mtime=$(${SUDO} stat -c %y "$p" 2>/dev/null || echo "-")
			echo "  $label: $(printf '%-18s' "$f") size=$(printf '%-8s' "$sz") mtime=$mtime"
		else
			echo "  $label: $(printf '%-18s' "$f") (missing)"
		fi
	done
}

echo "Before sync:"
show_stat SRC "$SRC"
show_stat DST "$DST"

# --- Backup auth if needed ---
TMP_BACKUP="$(mktemp -d)"
cleanup() { rm -rf "$TMP_BACKUP" || true; }
trap cleanup EXIT

if [[ $PRESERVE_AUTH -eq 1 ]]; then
	echo "Backing up auth-related files (if present): ${AUTH_FILES[*]}"
	for f in "${AUTH_FILES[@]}"; do
		if ${SUDO} test -f "${DST}/${f}"; then
			mkdir -p "${TMP_BACKUP}/$(dirname "$f")"
			${SUDO} cp -p "${DST}/${f}" "${TMP_BACKUP}/${f}" || true
		fi
	done
fi

# --- Rsync deploy ---
echo "Syncing ALL content: rsync ${RSYNC_FLAGS[*]} \"${SRC}\" \"${DST}\""
${SUDO} rsync "${RSYNC_FLAGS[@]}" "${SRC}" "${DST}" || { echo "rsync failed" >&2; exit 1; }

if [[ $DRY_RUN -eq 1 ]]; then
	echo "[dry-run] Stopping before restore/reset phase"
	exit 0
fi

# --- Restore or reset auth ---
if [[ $PRESERVE_AUTH -eq 1 ]]; then
	echo "Restoring preserved auth files"
	for f in "${AUTH_FILES[@]}"; do
		if [[ -f "${TMP_BACKUP}/${f}" ]]; then
			mkdir -p "${DST}/$(dirname "$f")"
			${SUDO} cp -p "${TMP_BACKUP}/${f}" "${DST}/${f}" || true
			${SUDO} chown mimir:mimir "${DST}/${f}" || true
		fi
	done
elif [[ $RESET_AUTH -eq 1 ]]; then
	echo "Resetting auth cache files (${AUTH_FILES[*]})"
	for f in "${AUTH_FILES[@]}"; do
		${SUDO} rm -f "${DST}/${f}" || true
	done
fi

# --- Final ownership pass (covers preserved folders) ---
${SUDO} chown -R mimir:mimir "${DST}"

echo "After sync:"
show_stat SRC "$SRC"
show_stat DST "$DST"

if [[ $RESTART -eq 1 ]]; then
	echo "Restarting mimir-api.service ..."
	${SUDO} systemctl restart mimir-api.service || echo "[warn] failed to restart mimir-api.service"
fi

echo "✅ Deployed: ${SRC} → ${DST}"
