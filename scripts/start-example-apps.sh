#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/opt/server-setup"
CONFIG_PATH="${EXAMPLE_APPS_CONFIG_PATH:-$ROOT_DIR/deploy/sites.json}"
POSTGRES_HOST="${POSTGRES_HOST:-test-db}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-server_setup}"
POSTGRES_USER="${POSTGRES_USER:-server_setup}"
SKIP_EXAMPLE_DEPLOY="${SKIP_EXAMPLE_DEPLOY:-0}"

if [[ "$SKIP_EXAMPLE_DEPLOY" == "1" ]]; then
  echo "Skipping example app deployment because SKIP_EXAMPLE_DEPLOY=1"
  exit 0
fi

for _ in $(seq 1 30); do
  if pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    break
  fi

  sleep 2
done

cd "$ROOT_DIR"
exec /usr/bin/env bash "$ROOT_DIR/scripts/sync-github-sites.sh" --config "$CONFIG_PATH"
