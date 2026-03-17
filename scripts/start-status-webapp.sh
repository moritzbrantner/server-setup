#!/usr/bin/env bash
set -euo pipefail

STATUS_WEBAPP_HOST="${STATUS_WEBAPP_HOST:-0.0.0.0}"
STATUS_WEBAPP_PORT="${STATUS_WEBAPP_PORT:-4000}"
WEBAPP_DIR="/opt/server-setup/monitor/webapp"

cd "$WEBAPP_DIR"

if [[ ! -d node_modules/next ]]; then
  npm ci --no-fund --no-audit
fi

if [[ -f .next/BUILD_ID ]]; then
  exec npm run start -- --hostname "$STATUS_WEBAPP_HOST" --port "$STATUS_WEBAPP_PORT"
fi

exec npm run dev -- --hostname "$STATUS_WEBAPP_HOST" --port "$STATUS_WEBAPP_PORT"
