#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/lib/test-helpers.sh"

SCRIPT_PATH="$ROOT_DIR/scripts/init_server_bootstrap.py"

test_init_server_invokes_status_webapp_setup() {
  grep -Fq 'log("[3/6] Installing/updating status webapp service")' "$SCRIPT_PATH"
  grep -Fq 'scripts/setup-status-webapp.sh' "$SCRIPT_PATH"
}

run_test "init-server installs status webapp service" test_init_server_invokes_status_webapp_setup

echo "All tests passed: $pass_count"
