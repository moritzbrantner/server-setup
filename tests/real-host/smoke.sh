#!/usr/bin/env bash
set -euo pipefail

if [[ "${SERVER_SETUP_DISPOSABLE_VM:-}" != "1" ]]; then
  echo "Refusing destructive real-host smoke test." >&2
  echo "Run only in a disposable VM with SERVER_SETUP_DISPOSABLE_VM=1." >&2
  exit 2
fi
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then echo "Real-host smoke test must run as root." >&2; exit 2; fi
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cp "$ROOT_DIR/tests/real-host/config.toml" /tmp/server-setup-real-host.toml
bash "$ROOT_DIR/setup.sh" --non-interactive --config /tmp/server-setup-real-host.toml --yes
server-setup validate --config /tmp/server-setup-real-host.toml
SECOND_PLAN="$(server-setup plan --config /tmp/server-setup-real-host.toml)"
printf '%s\n' "$SECOND_PLAN"
[[ "$SECOND_PLAN" == *"No changes."* ]] || { echo "Real host is not idempotent after first apply." >&2; exit 1; }
curl -fsS --max-time 10 http://127.0.0.1:3000/ >/dev/null || { echo "Dokploy admin endpoint did not respond on the fresh-install port." >&2; exit 1; }
