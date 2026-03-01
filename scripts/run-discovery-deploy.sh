#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

APPS_GLOB="${APPS_GLOB:-/srv/apps/*}"
CONFIG_PATH="${CONFIG_PATH:-$ROOT_DIR/deploy/sites.json}"
LOCK_FILE="${LOCK_FILE:-/var/lock/site-discovery-deploy.lock}"

mkdir -p "$(dirname "$LOCK_FILE")"

exec 200>"$LOCK_FILE"
flock 200

cd "$ROOT_DIR"

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Starting discovery + deploy run"
"$SCRIPT_DIR/discover-sites.sh" --base-glob "$APPS_GLOB" --output "$CONFIG_PATH"
"$SCRIPT_DIR/sync-github-sites.sh" --config "$CONFIG_PATH"
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Discovery + deploy run complete"
