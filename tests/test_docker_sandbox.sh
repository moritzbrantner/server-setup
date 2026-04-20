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
    find /etc/nginx/sites-available -maxdepth 1 -type f \( -name "simple-site*.conf" -o -name "rest-api*.conf" -o -name "complex-site*.conf" -o -name "server-setup-status-webapp.conf" \) -print -exec sed -n "1,160p" {} \; || true
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

test_docker_sandbox_deploys_seeded_example_site() {
  docker run -d \
    --privileged \
    --cgroupns=host \
    --name "$CONTAINER_NAME" \
    -e SKIP_EXAMPLE_DEPLOY=1 \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    "$IMAGE_NAME" >/dev/null

  wait_for_container_command 'systemctl list-units >/dev/null'
  docker exec "$CONTAINER_NAME" bash -lc 'shellcheck --version >/dev/null'
  docker exec "$CONTAINER_NAME" bash -lc 'bun --version >/dev/null'
  docker exec "$CONTAINER_NAME" bash -lc '
    cd /opt/server-setup
    python3 ./scripts/prepare_server.py --email admin@example.com --skip-docker
    python3 ./scripts/deploy_repo.py --repo-url /srv/apps/simple-site --dest /srv/apps/simple-site --email admin@example.com --skip-github-hook --skip-tls
    if ! getent hosts test-db >/dev/null 2>&1; then
      printf "\n127.0.0.1 test-db\n" >>/etc/hosts
      systemctl enable --now postgresql
      runuser -u postgres -- createuser server_setup >/dev/null 2>&1 || true
      runuser -u postgres -- createdb -O server_setup server_setup >/dev/null 2>&1 || true
      runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "alter user server_setup with password \$\$server_setup\$\$;"
    fi
    python3 ./scripts/deploy_repo.py --repo-url /srv/apps/rest-api --dest /srv/apps/rest-api --email admin@example.com --skip-github-hook --skip-tls
    python3 ./scripts/deploy_repo.py --repo-url /srv/apps/complex-site --dest /srv/apps/complex-site --email admin@example.com --skip-github-hook --skip-tls
  '

  docker exec "$CONTAINER_NAME" bash -lc 'test -f /srv/apps/simple-site/public/index.html'
  docker exec "$CONTAINER_NAME" bash -lc 'test -f /etc/nginx/sites-available/simple-site.conf'
  docker exec "$CONTAINER_NAME" bash -lc "curl -fsS -H 'Host: simple.localhost' http://127.0.0.1/ >/dev/null"
  docker exec "$CONTAINER_NAME" bash -lc 'systemctl is-active --quiet complex-site.service'
  docker exec "$CONTAINER_NAME" bash -lc "curl -fsS -H 'Host: app.localhost' http://127.0.0.1/ >/dev/null"
  docker exec "$CONTAINER_NAME" bash -lc 'systemctl is-active --quiet rest-api.service'
  docker exec "$CONTAINER_NAME" bash -lc "curl -fsS -H 'Host: api.localhost' http://127.0.0.1/healthz >/dev/null"
  docker exec "$CONTAINER_NAME" bash -lc '
    if systemctl is-active --quiet server-setup-status-webapp.service; then
      curl -fsS -H "Host: monitor.localhost" http://127.0.0.1/ >/dev/null
    else
      echo "Skipping status webapp curl because the service is not active in this sandbox."
    fi
  '
}

run_test "docker sandbox deploys seeded example site" test_docker_sandbox_deploys_seeded_example_site

echo "All tests passed: $pass_count"
