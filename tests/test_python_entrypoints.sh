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

test_shutdown_websites_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/shutdown_websites.py" --help >/dev/null
}

test_reset_server_setup_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/reset_server_setup.py" --help >/dev/null
}

test_prepare_server_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/prepare_server.py" --help >/dev/null
}

test_deploy_repo_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/deploy_repo.py" --help >/dev/null
}

test_setup_domain_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/setup_domain.py" --help >/dev/null
}

test_shutdown_server_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/shutdown_server.py" --help >/dev/null
}

test_prepare_server_shell_wrapper_shows_help() {
  bash "$ROOT_DIR/scripts/prepare-server.sh" --help >/dev/null
}

test_deploy_repo_shell_wrapper_shows_help() {
  bash "$ROOT_DIR/scripts/deploy-repo.sh" --help >/dev/null
}

test_setup_domain_shell_wrapper_shows_help() {
  bash "$ROOT_DIR/scripts/setup-domain.sh" --help >/dev/null
}

test_shutdown_server_shell_wrapper_shows_help() {
  bash "$ROOT_DIR/scripts/shutdown-server.sh" --help >/dev/null
}

run_test "init_server.py help works" test_init_server_python_entrypoint_shows_help
run_test "onboard_app.py help works" test_onboard_app_python_entrypoint_shows_help
run_test "serve_domain.py help works" test_serve_domain_python_entrypoint_shows_help
run_test "shutdown_websites.py help works" test_shutdown_websites_python_entrypoint_shows_help
run_test "reset_server_setup.py help works" test_reset_server_setup_python_entrypoint_shows_help
run_test "prepare_server.py help works" test_prepare_server_python_entrypoint_shows_help
run_test "deploy_repo.py help works" test_deploy_repo_python_entrypoint_shows_help
run_test "setup_domain.py help works" test_setup_domain_python_entrypoint_shows_help
run_test "shutdown_server.py help works" test_shutdown_server_python_entrypoint_shows_help
run_test "prepare-server.sh help works" test_prepare_server_shell_wrapper_shows_help
run_test "deploy-repo.sh help works" test_deploy_repo_shell_wrapper_shows_help
run_test "setup-domain.sh help works" test_setup_domain_shell_wrapper_shows_help
run_test "shutdown-server.sh help works" test_shutdown_server_shell_wrapper_shows_help

echo "All tests passed: $pass_count"
