#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/lib/test-helpers.sh"

test_init_server_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/init_server.py" --help >/dev/null
}

test_onboard_app_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/onboard_app.py" --help >/dev/null
}

test_serve_domain_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/serve_domain.py" --help >/dev/null
}

run_test "init_server.py help works" test_init_server_python_entrypoint_shows_help
run_test "onboard_app.py help works" test_onboard_app_python_entrypoint_shows_help
run_test "serve_domain.py help works" test_serve_domain_python_entrypoint_shows_help

echo "All tests passed: $pass_count"
