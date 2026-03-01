#!/usr/bin/env bash
set -euo pipefail

APPS_DIR="${APPS_DIR:-/srv/apps}"
DEBOUNCE_SECONDS="${DEBOUNCE_SECONDS:-5}"
RUNNER_SERVICE="${RUNNER_SERVICE:-site-discovery-deploy.service}"

if ! command -v inotifywait >/dev/null 2>&1; then
  echo "inotifywait is required (install inotify-tools)." >&2
  exit 1
fi

if [[ ! -d "$APPS_DIR" ]]; then
  echo "Apps directory not found: $APPS_DIR" >&2
  exit 1
fi

echo "Watching $APPS_DIR for change events..."
last_trigger_epoch=0

inotifywait -m -r \
  -e close_write -e moved_to -e create -e delete \
  --format '%w%f %e' "$APPS_DIR" | while read -r changed_path changed_event; do
  now_epoch=$(date +%s)

  if (( now_epoch - last_trigger_epoch < DEBOUNCE_SECONDS )); then
    continue
  fi

  last_trigger_epoch=$now_epoch
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Change detected ($changed_event) at $changed_path"
  systemctl start "$RUNNER_SERVICE"
done
