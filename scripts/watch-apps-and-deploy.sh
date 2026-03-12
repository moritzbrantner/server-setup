#!/usr/bin/env bash
set -euo pipefail

APPS_DIR="${APPS_DIR:-/srv/apps}"
DEBOUNCE_SECONDS="${DEBOUNCE_SECONDS:-5}"
RUNNER_SERVICE="${RUNNER_SERVICE:-site-discovery-deploy.service}"
LOG_DIR="${LOG_DIR:-/var/log/server-setup}"
LOG_RETENTION_DAYS="${LOG_RETENTION_DAYS:-14}"

if ! command -v inotifywait >/dev/null 2>&1; then
  echo "inotifywait is required (install inotify-tools)." >&2
  exit 1
fi

if [[ ! -d "$APPS_DIR" ]]; then
  echo "Apps directory not found: $APPS_DIR" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
find "$LOG_DIR" -type f -name '*.log' -mtime +"$LOG_RETENTION_DAYS" -delete 2>/dev/null || true

echo "Watching $APPS_DIR for change events..."
last_trigger_epoch=0
pending=0

inotifywait -m -r \
  -e close_write -e moved_to -e create -e delete \
  --format '%w%f %e' "$APPS_DIR" | while read -r changed_path changed_event; do
  now_epoch=$(date +%s)

  if (( now_epoch - last_trigger_epoch < DEBOUNCE_SECONDS )); then
    pending=1
    continue
  fi

  last_trigger_epoch=$now_epoch
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Change detected ($changed_event) at $changed_path" | tee -a "$LOG_DIR/watcher.log"
  systemctl start "$RUNNER_SERVICE"
  if (( pending == 1 )); then
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Collapsed burst of app changes into a single deploy trigger" | tee -a "$LOG_DIR/watcher.log"
    pending=0
  fi
done
