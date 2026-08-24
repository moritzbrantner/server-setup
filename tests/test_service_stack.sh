#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ENV_EXAMPLE="$ROOT_DIR/services/.env.example"
BASE="$ROOT_DIR/services/compose.yml"
PUBLIC="$ROOT_DIR/services/compose.public.yml"

bash -n "$ROOT_DIR/setup.sh"
bash "$ROOT_DIR/setup.sh" --help >/dev/null

docker compose --env-file "$ENV_EXAMPLE" -f "$BASE" config --quiet
UPTIME_KUMA_HOST=status.example.com \
BESZEL_HOST=metrics.example.com \
BESZEL_APP_URL=https://metrics.example.com \
  docker compose --env-file "$ENV_EXAMPLE" -f "$BASE" -f "$PUBLIC" config --quiet

grep -Fq '127.0.0.1:${UPTIME_KUMA_PORT:-3001}:3001' "$BASE"
grep -Fq '127.0.0.1:${BESZEL_PORT:-8090}:8090' "$BASE"
grep -Fq 'dokploy-network' "$PUBLIC"
grep -Fq 'services/dnscontrol/creds.json' "$ROOT_DIR/.gitignore"
