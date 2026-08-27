#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
BASE_IMAGE="${1:-debian:12}"
SAFE_NAME="$(printf '%s' "$BASE_IMAGE" | tr ':/' '--')"
IMAGE="server-setup-host-sandbox:$SAFE_NAME"
CONTAINER="server-setup-host-sandbox-$SAFE_NAME-$$"
cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker build --build-arg "BASE_IMAGE=$BASE_IMAGE" -t "$IMAGE" "$ROOT_DIR/tests/host-sandbox"
docker run --privileged --detach --name "$CONTAINER" --tmpfs /run --tmpfs /run/lock --volume /sys/fs/cgroup:/sys/fs/cgroup:rw "$IMAGE" >/dev/null
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" systemctl is-system-running >/dev/null 2>&1; then break; fi
  sleep 1
done

docker exec "$CONTAINER" mkdir -p /workspace/server-setup
docker cp "$ROOT_DIR/." "$CONTAINER:/workspace/server-setup"
docker cp "$ROOT_DIR/tests/host-sandbox/config.toml" "$CONTAINER:/tmp/server-setup-config.toml"
docker exec -w /workspace/server-setup "$CONTAINER" bash ./setup.sh --non-interactive --config /tmp/server-setup-config.toml --yes
docker exec "$CONTAINER" server-setup validate --config /tmp/server-setup-config.toml
SECOND_PLAN="$(docker exec "$CONTAINER" server-setup plan --config /tmp/server-setup-config.toml)"
printf '%s\n' "$SECOND_PLAN"
if [[ "$SECOND_PLAN" != *"No changes."* ]]; then echo "Second plan was not empty; apply is not idempotent." >&2; exit 1; fi
docker exec -w /workspace/server-setup "$CONTAINER" bash ./setup.sh --non-interactive --config /tmp/server-setup-config.toml --yes
