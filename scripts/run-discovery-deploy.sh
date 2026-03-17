#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

default_apps_glob() {
  if [[ -d "/root/apps" ]]; then
    printf '/root/apps/*'
  else
    printf '/srv/apps/*'
  fi
}

APPS_GLOB="${APPS_GLOB:-$(default_apps_glob)}"
CONFIG_PATH="${CONFIG_PATH:-$ROOT_DIR/deploy/sites.json}"
LOCK_FILE="${LOCK_FILE:-/var/lock/site-discovery-deploy.lock}"
LOG_DIR="${LOG_DIR:-/var/log/server-setup}"
LOG_RETENTION_DAYS="${LOG_RETENTION_DAYS:-14}"

mkdir -p "$(dirname "$LOCK_FILE")"
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -type f -name '*.log' -mtime +"$LOG_RETENTION_DAYS" -delete 2>/dev/null || true

exec 200>"$LOCK_FILE"
flock 200

cd "$ROOT_DIR"

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Starting discovery + deploy run" | tee -a "$LOG_DIR/discovery-deploy.log"
"$SCRIPT_DIR/discover-sites.sh" --base-glob "$APPS_GLOB" --output "$CONFIG_PATH"
"$SCRIPT_DIR/sync-github-sites.sh" --config "$CONFIG_PATH"
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Discovery + deploy run complete" | tee -a "$LOG_DIR/discovery-deploy.log"
