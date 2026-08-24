#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

"$ROOT_DIR/tests/run-lint.sh"
bash "$ROOT_DIR/tests/test_service_stack.sh"
"$ROOT_DIR/tests/test_install_nginx_site.sh"
"$ROOT_DIR/tests/test_setup_status_webapp.sh"
"$ROOT_DIR/tests/test_seed_example_repositories.sh"
"$ROOT_DIR/tests/test_shutdown_reset_scripts.sh"
bash "$ROOT_DIR/tests/test_manage_services.sh"
bash "$ROOT_DIR/tests/test_python_entrypoints.sh"
"$ROOT_DIR/tests/test_webhook_service_unit.sh"
"$ROOT_DIR/tests/test_status_webapp_frontend.sh"
"$ROOT_DIR/tests/run-python-tests.sh"
