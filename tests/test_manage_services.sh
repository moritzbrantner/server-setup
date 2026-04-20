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

make_stub_systemctl() {
  local bin_dir="$1"
  mkdir -p "$bin_dir"

  cat >"$bin_dir/systemctl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

command="${1:-}"
shift || true

case "$command" in
  show)
    unit="${1:-}"
    case "$unit" in
      sample-service.service)
        cat <<'EOF'
LoadState=loaded
ActiveState=active
SubState=running
UnitFileState=enabled
EOF
        ;;
      site-webhook-receiver.service)
        cat <<'EOF'
LoadState=loaded
ActiveState=active
SubState=running
UnitFileState=enabled
EOF
        ;;
      server-setup-status-webapp.service)
        cat <<'EOF'
LoadState=not-found
ActiveState=inactive
SubState=dead
UnitFileState=disabled
EOF
        ;;
      *)
        cat <<'EOF'
LoadState=loaded
ActiveState=inactive
SubState=dead
UnitFileState=disabled
EOF
        ;;
    esac
    ;;
  restart)
    unit="${1:-}"
    printf 'restart %s\n' "$unit"
    ;;
  *)
    echo "unexpected systemctl command: $command $*" >&2
    exit 1
    ;;
esac
SH

  chmod +x "$bin_dir/systemctl"
}

test_manage_services_lists_unit_status_and_app_mapping() {
  local tmp
  local registry_path
  tmp="$(make_temp_dir)"
  registry_path="$tmp/registry.json"
  make_stub_systemctl "$tmp/bin"
  write_registry_fixture "$registry_path"

  local output
  output="$(
    PATH="$tmp/bin:$PATH" \
      python3 "$ROOT_DIR/scripts/manage_services.py" --config "$registry_path"
  )"

  grep -Fq 'SERVICE' <<<"$output"
  grep -Fq 'site-webhook-receiver.service' <<<"$output"
  grep -Fq 'sample-service.service' <<<"$output"
  grep -Fq 'automation' <<<"$output"
  grep -Fq 'sample-service' <<<"$output"
  grep -Fq 'yes' <<<"$output"
  grep -Fq 'no' <<<"$output"
}

test_manage_services_filters_and_runs_dry_run_action() {
  local tmp
  local registry_path
  tmp="$(make_temp_dir)"
  registry_path="$tmp/registry.json"
  make_stub_systemctl "$tmp/bin"
  write_registry_fixture "$registry_path"

  local output
  output="$(
    PATH="$tmp/bin:$PATH" \
      python3 "$ROOT_DIR/scripts/manage_services.py" restart --app sample-service --dry-run --config "$registry_path"
  )"

  grep -Fq '+ systemctl restart sample-service.service' <<<"$output"
  if grep -Fq 'site-webhook-receiver.service' <<<"$output"; then
    echo "Unexpected extra service action in output:" >&2
    echo "$output" >&2
    exit 1
  fi
}

run_test "manage_services lists managed units with status and app mapping" test_manage_services_lists_unit_status_and_app_mapping
run_test "manage_services dry-run action respects app filters" test_manage_services_filters_and_runs_dry_run_action

echo "All tests passed: $pass_count"
