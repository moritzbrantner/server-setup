#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/test-helpers.sh
source "$SCRIPT_DIR/lib/test-helpers.sh"

# Initialized by test-helpers.sh; repeated here so ShellCheck sees it.
declare -i pass_count="${pass_count:-0}"

test_render_status_webapp_env_uses_port_4000() {
  local env_body
  env_body="$(python3 "$ROOT_DIR/scripts/setup_status_webapp.py" --root "$ROOT_DIR" --render-env)"

  grep -Fq "SERVER_SETUP_ROOT=$ROOT_DIR" <<<"$env_body"
  grep -Fq 'BUN_INSTALL=/root/.bun' <<<"$env_body"
  grep -Fq 'STATUS_WEBAPP_HOST=0.0.0.0' <<<"$env_body"
  grep -Fq 'STATUS_WEBAPP_PORT=4000' <<<"$env_body"
  grep -Fq 'STATUS_WEBAPP_ADMIN_TOKEN=' <<<"$env_body"
}

test_render_status_webapp_service_restarts_on_failure() {
  local unit
  unit="$(python3 "$ROOT_DIR/scripts/setup_status_webapp.py" --root "$ROOT_DIR" --render-service)"

  grep -Fq 'EnvironmentFile=-/etc/default/server-setup-status-webapp' <<<"$unit"
  grep -Fq 'Environment=BUN_INSTALL=/root/.bun' <<<"$unit"
  grep -Fq "WorkingDirectory=$ROOT_DIR/monitor/webapp" <<<"$unit"
  grep -Fq "ExecStart=/usr/bin/env python3 $ROOT_DIR/scripts/start_status_webapp.py" <<<"$unit"
  grep -Fq 'Environment=STATUS_WEBAPP_PORT=4000' <<<"$unit"
  grep -Fq 'Restart=always' <<<"$unit"
}

run_test "setup-status-webapp renders environment with port 4000" test_render_status_webapp_env_uses_port_4000
run_test "setup-status-webapp renders restartable systemd service" test_render_status_webapp_service_restarts_on_failure

test_status_webapp_runner_uses_bun() {
  grep -Fq 'bun", "install' "$ROOT_DIR/scripts/start_status_webapp.py"
  grep -Fq 'bun", "run", "build' "$ROOT_DIR/scripts/start_status_webapp.py"
  grep -Fq 'bun", "run", "start' "$ROOT_DIR/scripts/start_status_webapp.py"
  grep -Fq 'def ensure_bun()' "$ROOT_DIR/scripts/setup_status_webapp.py"
  grep -Fq 'bun", "install' "$ROOT_DIR/scripts/setup_status_webapp.py"
  grep -Fq 'bun", "run", "build' "$ROOT_DIR/scripts/setup_status_webapp.py"
}

run_test "status webapp scripts use bun" test_status_webapp_runner_uses_bun

echo "All tests passed: $pass_count"
