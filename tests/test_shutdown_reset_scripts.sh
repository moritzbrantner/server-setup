#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/lib/test-helpers.sh"

test_shutdown_websites_dry_run_lists_managed_units() {
  local output
  output="$(python3 "$ROOT_DIR/scripts/shutdown_websites.py" --config "$ROOT_DIR/deploy/sites.json" --dry-run)"

  grep -Fq 'systemctl stop site-apps-watcher.service' <<<"$output"
  grep -Fq 'systemctl stop site-webhook-receiver.service' <<<"$output"
  grep -Fq 'systemctl stop server-setup-status-webapp.service' <<<"$output"
  grep -Fq 'systemctl stop tlm-deutschland.service' <<<"$output"
  grep -Fq 'systemctl stop nginx.service' <<<"$output"
}

test_reset_server_setup_dry_run_lists_cleanup_targets() {
  local output
  output="$(python3 "$ROOT_DIR/scripts/reset_server_setup.py" --config "$ROOT_DIR/deploy/sites.json" --dry-run)"

  grep -Fq 'systemctl disable tlm-deutschland.service' <<<"$output"
  grep -Fq 'remove /etc/systemd/system/tlm-deutschland.service' <<<"$output"
  grep -Fq 'remove /etc/default/site-automation' <<<"$output"
  grep -Fq 'remove /etc/default/server-setup-status-webapp' <<<"$output"
  grep -Fq 'remove /etc/nginx/sites-available/tlm-deutschland.conf' <<<"$output"
  grep -Fq 'remove /var/lib/server-setup/state/tlm-deutschland.json' <<<"$output"
  grep -Fq 'systemctl daemon-reload' <<<"$output"
}

run_test "shutdown-websites dry-run lists managed units" test_shutdown_websites_dry_run_lists_managed_units
run_test "reset-server-setup dry-run lists cleanup targets" test_reset_server_setup_dry_run_lists_cleanup_targets

echo "All tests passed: $pass_count"
