#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/lib/test-helpers.sh"

test_webhook_service_declares_install_target() {
  local unit
  unit="$(<"$ROOT_DIR/ops/systemd/site-webhook-receiver.service")"

  grep -Fq '[Install]' <<<"$unit"
  grep -Fq 'WantedBy=multi-user.target' <<<"$unit"
}

run_test "webhook service unit supports systemctl enable" test_webhook_service_declares_install_target

echo "All tests passed: $pass_count"
