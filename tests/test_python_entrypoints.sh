#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/lib/test-helpers.sh"

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

test_shutdown_server_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/shutdown_server.py" --help >/dev/null
}

test_manage_services_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/manage_services.py" --help >/dev/null
}

test_migrate_registry_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/migrate_registry.py" --help >/dev/null
}

test_setup_letsencrypt_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/setup_letsencrypt.py" --help >/dev/null
}

test_setup_status_webapp_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/setup_status_webapp.py" --help >/dev/null
}

test_harden_server_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/harden_server.py" --help >/dev/null
}

test_manage_github_secrets_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/manage_github_secrets.py" --help >/dev/null
}

test_manage_dns_records_python_entrypoint_shows_help() {
  python3 "$ROOT_DIR/scripts/manage_dns_records.py" --help >/dev/null
}

run_test "shutdown_websites.py help works" test_shutdown_websites_python_entrypoint_shows_help
run_test "reset_server_setup.py help works" test_reset_server_setup_python_entrypoint_shows_help
run_test "prepare_server.py help works" test_prepare_server_python_entrypoint_shows_help
run_test "deploy_repo.py help works" test_deploy_repo_python_entrypoint_shows_help
run_test "shutdown_server.py help works" test_shutdown_server_python_entrypoint_shows_help
run_test "manage_services.py help works" test_manage_services_python_entrypoint_shows_help
run_test "migrate_registry.py help works" test_migrate_registry_python_entrypoint_shows_help
run_test "setup_letsencrypt.py help works" test_setup_letsencrypt_python_entrypoint_shows_help
run_test "setup_status_webapp.py help works" test_setup_status_webapp_python_entrypoint_shows_help
run_test "harden_server.py help works" test_harden_server_python_entrypoint_shows_help
run_test "manage_github_secrets.py help works" test_manage_github_secrets_python_entrypoint_shows_help
run_test "manage_dns_records.py help works" test_manage_dns_records_python_entrypoint_shows_help

echo "All tests passed: $pass_count"
