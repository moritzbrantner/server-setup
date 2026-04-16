#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/test-helpers.sh
source "$SCRIPT_DIR/lib/test-helpers.sh"

# Initialized by test-helpers.sh; repeated here so ShellCheck sees it.
declare -i pass_count="${pass_count:-0}"

IMAGE_NAME="${IMAGE_NAME:-server-setup-test}"
CONTAINER_NAME="server-setup-test-$$"

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}

dump_diagnostics() {
  docker logs "$CONTAINER_NAME" >/dev/null 2>&1 || return 0

  echo "Docker sandbox diagnostics:" >&2
  docker logs "$CONTAINER_NAME" >&2 || true
  docker exec "$CONTAINER_NAME" bash -lc '
    systemctl status nginx --no-pager || true
    systemctl status tlm-deutschland.service --no-pager || true
    journalctl -u tlm-deutschland.service --no-pager -n 200 || true
    find /var/log/server-setup -maxdepth 1 -type f -name "*.log" -print -exec tail -n 200 {} \; || true
  ' >&2 || true
}

on_exit() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    dump_diagnostics
  fi
  cleanup
  exit $status
}

trap on_exit EXIT

wait_for_container_command() {
  local cmd="$1"
  local attempts="${2:-60}"
  local delay_seconds="${3:-2}"
  local attempt

  for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    if docker exec "$CONTAINER_NAME" bash -lc "$cmd" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay_seconds"
  done

  return 1
}

verify_tlm_repo_access() {
  if [[ -z "${TLM_DEUTSCHLAND_GITHUB_TOKEN:-}" ]]; then
    echo "TLM_DEUTSCHLAND_GITHUB_TOKEN must be set for tlm-deutschland deploy tests." >&2
    return 1
  fi

  if docker exec "$CONTAINER_NAME" bash -lc 'git ls-remote "https://x-access-token:${TLM_DEUTSCHLAND_GITHUB_TOKEN}@github.com/moritzbrantner/tlm-deutschland.git" HEAD >/dev/null 2>&1'; then
    return 0
  fi

  echo "Unable to access tlm-deutschland on GitHub from the sandbox using TLM_DEUTSCHLAND_GITHUB_TOKEN." >&2
  return 1
}

test_docker_sandbox_deploys_tlm_deutschland() {
  if [[ -z "${TLM_DEUTSCHLAND_GITHUB_TOKEN:-}" ]]; then
    echo "Skipping tlm-deutschland Docker deploy test because TLM_DEUTSCHLAND_GITHUB_TOKEN is not set."
    return 0
  fi

  docker run -d \
    --privileged \
    --cgroupns=host \
    --name "$CONTAINER_NAME" \
    -e TLM_DEUTSCHLAND_GITHUB_TOKEN="${TLM_DEUTSCHLAND_GITHUB_TOKEN:-}" \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    "$IMAGE_NAME" >/dev/null

  wait_for_container_command 'systemctl list-units >/dev/null'
  docker exec "$CONTAINER_NAME" bash -lc 'shellcheck --version >/dev/null'
  docker exec "$CONTAINER_NAME" bash -lc 'bun --version >/dev/null'
  verify_tlm_repo_access
  docker exec "$CONTAINER_NAME" bash -lc 'systemctl start nginx'
  docker exec "$CONTAINER_NAME" bash -lc '
    cd /opt/server-setup
    python3 ./scripts/prepare_server.py --email admin@example.com --skip-docker
    python3 ./scripts/deploy_repo.py --repo-url https://github.com/moritzbrantner/tlm-deutschland.git --dest /root/apps/tlm-deutschland --email admin@example.com --skip-github-hook
  '

  docker exec "$CONTAINER_NAME" bash -lc 'test -f /root/apps/tlm-deutschland/.next/BUILD_ID'
  docker exec "$CONTAINER_NAME" bash -lc 'systemctl is-active --quiet tlm-deutschland.service'
  docker exec "$CONTAINER_NAME" bash -lc "curl -fsS -H 'Host: tlm-deutschland.de' http://127.0.0.1/ >/dev/null"
}

run_test "docker sandbox deploys tlm-deutschland from GitHub" test_docker_sandbox_deploys_tlm_deutschland

echo "All tests passed: $pass_count"
