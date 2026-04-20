#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/lib/test-helpers.sh"

write_registry_fixture() {
  local registry_path="$1"
  cat >"$registry_path" <<'JSON'
[
  {
    "name": "sample-service",
    "repo_url": "https://github.com/example/sample-service.git",
    "branch": "main",
    "checkout_path": "/srv/apps/sample-service",
    "server_conf_path": "/srv/apps/sample-service/server.conf",
    "service_name": "sample-service.service",
    "domain": "sample.example.com",
    "webhook_repo": "example/sample-service",
    "managed_by": "deploy-repo",
    "deploy_config": {
      "name": "sample-service",
      "domain": "sample.example.com",
      "runtime": {
        "mode": "service"
      },
      "service": {
        "name": "sample-service.service"
      }
    }
  }
]
JSON
}

test_shutdown_websites_dry_run_lists_managed_units() {
  local tmp
  local registry_path
  local output
  tmp="$(make_temp_dir)"
  registry_path="$tmp/registry.json"
  write_registry_fixture "$registry_path"
  output="$(python3 "$ROOT_DIR/scripts/shutdown_websites.py" --config "$registry_path" --dry-run)"

  grep -Fq 'systemctl stop site-webhook-receiver.service' <<<"$output"
  grep -Fq 'systemctl stop server-setup-status-webapp.service' <<<"$output"
  grep -Fq 'systemctl stop sample-service.service' <<<"$output"
  grep -Fq 'systemctl stop nginx.service' <<<"$output"
}

test_reset_server_setup_dry_run_lists_cleanup_targets() {
  local tmp
  local registry_path
  local output
  tmp="$(make_temp_dir)"
  registry_path="$tmp/registry.json"
  write_registry_fixture "$registry_path"
  output="$(python3 "$ROOT_DIR/scripts/reset_server_setup.py" --config "$registry_path" --dry-run)"

  grep -Fq 'systemctl disable sample-service.service' <<<"$output"
  grep -Fq 'remove /etc/systemd/system/sample-service.service' <<<"$output"
  grep -Fq "remove $registry_path" <<<"$output"
  grep -Fq 'remove /etc/default/site-automation' <<<"$output"
  grep -Fq 'remove /etc/default/server-setup-status-webapp' <<<"$output"
  grep -Fq 'remove /etc/nginx/sites-available/sample-service.conf' <<<"$output"
  grep -Fq 'remove /var/lib/server-setup/state/sample-service.json' <<<"$output"
  grep -Fq 'systemctl daemon-reload' <<<"$output"
}

run_test "shutdown-websites dry-run lists managed units" test_shutdown_websites_dry_run_lists_managed_units
run_test "reset-server-setup dry-run lists cleanup targets" test_reset_server_setup_dry_run_lists_cleanup_targets

echo "All tests passed: $pass_count"
