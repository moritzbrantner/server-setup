#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ENV_EXAMPLE="$ROOT_DIR/services/.env.example"
BASE="$ROOT_DIR/services/compose.yml"
PUBLIC="$ROOT_DIR/services/compose.public.yml"

bash -n "$ROOT_DIR/setup.sh"
help_output="$(bash "$ROOT_DIR/setup.sh" --help)"
grep -Fq -- '--cutover-preflight' <<<"$help_output"
grep -Fq -- '--confirm-legacy-cutover-ready' <<<"$help_output"

preflight_output="$(bash "$ROOT_DIR/setup.sh" --cutover-preflight)"
grep -Fq 'Cut-over preflight (read-only)' <<<"$preflight_output"
grep -Fq 'Pinned Dokploy release: v0.30.2' <<<"$preflight_output"
grep -Fq 'No changes were made.' <<<"$preflight_output"

missing_ack_output="$(mktemp)"
if bash "$ROOT_DIR/setup.sh" --dry-run --replace-legacy >"$missing_ack_output" 2>&1; then
  echo "legacy cut-over unexpectedly succeeded without confirmation" >&2
  rm -f "$missing_ack_output"
  exit 1
fi
grep -Fq -- '--replace-legacy requires --confirm-legacy-cutover-ready' "$missing_ack_output"
rm -f "$missing_ack_output"

confirmed_output="$(bash "$ROOT_DIR/setup.sh" --dry-run --replace-legacy --confirm-legacy-cutover-ready)"
grep -Fq 'Installing pinned Dokploy release v0.30.2.' <<<"$confirmed_output"
grep -Fq 'Dry run complete; no mutating commands above were executed.' <<<"$confirmed_output"

grep -Fq 'releases/download/${DOKPLOY_VERSION}/install.sh' "$ROOT_DIR/setup.sh"
grep -Fq 'restart_active_legacy_edge' "$ROOT_DIR/setup.sh"
grep -Fq 'wait_for_dokploy_ready' "$ROOT_DIR/setup.sh"
grep -Fq 'disable_legacy_edge' "$ROOT_DIR/setup.sh"

docker compose --env-file "$ENV_EXAMPLE" -f "$BASE" config --quiet
UPTIME_KUMA_HOST=status.example.com \
BESZEL_HOST=metrics.example.com \
BESZEL_APP_URL=https://metrics.example.com \
  docker compose --env-file "$ENV_EXAMPLE" -f "$BASE" -f "$PUBLIC" config --quiet

grep -Fq '127.0.0.1:${UPTIME_KUMA_PORT:-3001}:3001' "$BASE"
grep -Fq '127.0.0.1:${BESZEL_PORT:-8090}:8090' "$BASE"
grep -Fq 'dokploy-network' "$PUBLIC"
grep -Fq 'services/dnscontrol/creds.json' "$ROOT_DIR/.gitignore"
