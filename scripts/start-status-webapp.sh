#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_SERVER_SETUP_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
SERVER_SETUP_ROOT="${SERVER_SETUP_ROOT:-$DEFAULT_SERVER_SETUP_ROOT}"
STATUS_WEBAPP_HOST="${STATUS_WEBAPP_HOST:-0.0.0.0}"
STATUS_WEBAPP_PORT="${STATUS_WEBAPP_PORT:-4000}"
WEBAPP_DIR="$SERVER_SETUP_ROOT/monitor/webapp"

cd "$WEBAPP_DIR"

if [[ ! -d node_modules/next ]]; then
  npm ci --no-fund --no-audit
fi

if [[ ! -f .next/BUILD_ID ]]; then
  npm run build
fi

exec npm run start -- --hostname "$STATUS_WEBAPP_HOST" --port "$STATUS_WEBAPP_PORT"
