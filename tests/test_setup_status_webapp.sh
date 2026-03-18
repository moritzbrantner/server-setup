#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/test-helpers.sh
source "$SCRIPT_DIR/lib/test-helpers.sh"

# shellcheck source=../scripts/setup-status-webapp.sh
source "$ROOT_DIR/scripts/setup-status-webapp.sh"

test_render_status_webapp_env_uses_port_4000() {
  local env_body
  env_body="$(render_status_webapp_env "$ROOT_DIR" "0.0.0.0" "4000")"

  grep -Fq "SERVER_SETUP_ROOT=$ROOT_DIR" <<<"$env_body"
  grep -Fq 'STATUS_WEBAPP_HOST=0.0.0.0' <<<"$env_body"
  grep -Fq 'STATUS_WEBAPP_PORT=4000' <<<"$env_body"
}

test_render_status_webapp_service_restarts_on_failure() {
  local unit
  unit="$(render_status_webapp_service "$ROOT_DIR" "/etc/default/server-setup-status-webapp")"

  grep -Fq 'EnvironmentFile=-/etc/default/server-setup-status-webapp' <<<"$unit"
  grep -Fq "WorkingDirectory=$ROOT_DIR/monitor/webapp" <<<"$unit"
  grep -Fq "ExecStart=/usr/bin/env bash $ROOT_DIR/scripts/start-status-webapp.sh" <<<"$unit"
  grep -Fq 'Environment=STATUS_WEBAPP_PORT=4000' <<<"$unit"
  grep -Fq 'Restart=always' <<<"$unit"
}

run_test "setup-status-webapp renders environment with port 4000" test_render_status_webapp_env_uses_port_4000
run_test "setup-status-webapp renders restartable systemd service" test_render_status_webapp_service_restarts_on_failure

echo "All tests passed: $pass_count"
