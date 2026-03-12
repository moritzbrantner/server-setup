#!/usr/bin/env bash
set -euo pipefail

EXAMPLE_APPS_DIR="${EXAMPLE_APPS_DIR:-/srv/apps}"
SKIP_EXAMPLE_SEED="${SKIP_EXAMPLE_SEED:-0}"
SEED_SCRIPT="/opt/server-setup/scripts/seed-example-repositories.sh"

mkdir -p "$EXAMPLE_APPS_DIR"

if [[ "$SKIP_EXAMPLE_SEED" != "1" ]]; then
  "$SEED_SCRIPT" --target-dir "$EXAMPLE_APPS_DIR"
else
  echo "Skipping example repository seeding because SKIP_EXAMPLE_SEED=1"
fi

if [[ $# -gt 0 ]]; then
  exec "$@"
fi

exec /sbin/init
