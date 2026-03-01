#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
DISCOVER_SCRIPT="$ROOT_DIR/scripts/discover-sites.sh"

if ! command -v jq >/dev/null 2>&1; then
  echo "Missing required command: jq" >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/apps/server-setup-bench"
cp -R "$ROOT_DIR/." "$tmp/apps/server-setup-bench/"

(
  cd "$tmp/apps/server-setup-bench"
  git init -q
  git config user.email bench@example.com
  git config user.name bench
  git add .
  git commit -qm "init"
  git branch -M main
  cat > server.conf <<'JSON'
{
  "name": "server-setup-bench",
  "domain": "bench.local",
  "build_output": ".",
  "deploy_hooks": {
    "build": "./scripts/run-self-checks.sh"
  },
  "runtime": {
    "mode": "static"
  },
  "service": {
    "name": "server-setup-bench.service"
  }
}
JSON
)

if command -v hyperfine >/dev/null 2>&1; then
  hyperfine --warmup 2 \
    "$DISCOVER_SCRIPT --base-glob '$tmp/apps/*' --output '$tmp/sites.json'"
else
  echo "hyperfine not found; falling back to 5 timed runs."
  for i in 1 2 3 4 5; do
    if [[ -x /usr/bin/time ]]; then
      /usr/bin/time -f "run $i: %E real %M KB" \
        "$DISCOVER_SCRIPT" --base-glob "$tmp/apps/*" --output "$tmp/sites.json" >/dev/null
    else
      start="$(date +%s%3N)"
      "$DISCOVER_SCRIPT" --base-glob "$tmp/apps/*" --output "$tmp/sites.json" >/dev/null
      end="$(date +%s%3N)"
      echo "run $i: $((end - start))ms real"
    fi
  done
fi
