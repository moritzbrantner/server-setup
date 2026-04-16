#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/lib/test-helpers.sh"

test_status_webapp_frontend_suite() {
  local node_bin
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required to run monitor/webapp tests." >&2
    exit 1
  fi
  node_bin="$(dirname "$(command -v npm)")/node"
  if [[ ! -x "$node_bin" ]]; then
    echo "Unable to locate the Node.js binary next to npm." >&2
    exit 1
  fi

  (
    cd "$ROOT_DIR/monitor/webapp"
    npm ci --no-audit --no-fund
    "$node_bin" ./node_modules/tsx/dist/cli.mjs --test \
      lib/status.test.ts \
      lib/control.test.ts \
      components/dashboard.test.tsx
  )
}

run_test "status webapp frontend tests pass" test_status_webapp_frontend_suite

echo "All tests passed: $pass_count"
