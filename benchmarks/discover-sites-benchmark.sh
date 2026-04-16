#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if ! command -v jq >/dev/null 2>&1; then
  echo "Missing required command: jq" >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/apps/server-setup-bench"
(
  cd "$ROOT_DIR"
  tar --exclude=.git -cf - .
) | (
  cd "$tmp/apps/server-setup-bench"
  tar -xf -
)

(
  cd "$tmp/apps/server-setup-bench"
  git init -q
  git config user.email bench@example.com
  git config user.name bench
  git add .
  git commit -qm "init"
  git branch -M main
  cat > server.conf <<'JSON'
{
  "name": "server-setup-bench",
  "domain": "bench.local",
  "build_output": ".",
  "deploy_hooks": {
    "build": "python3 ./scripts/run_self_checks.py"
  },
  "runtime": {
    "mode": "static"
  },
  "service": {
    "name": "server-setup-bench.service"
  }
}
JSON
)

if command -v hyperfine >/dev/null 2>&1; then
  hyperfine --warmup 2 \
    "python3 -c \"import sys; sys.path.insert(0, '$ROOT_DIR/scripts'); from server_conf_contract import normalize_server_conf; normalize_server_conf('$tmp/apps/server-setup-bench')\""
else
  echo "hyperfine not found; falling back to 5 timed runs."
  for i in 1 2 3 4 5; do
    if [[ -x /usr/bin/time ]]; then
      /usr/bin/time -f "run $i: %E real %M KB" \
        python3 -c "import sys; sys.path.insert(0, '$ROOT_DIR/scripts'); from server_conf_contract import normalize_server_conf; normalize_server_conf('$tmp/apps/server-setup-bench')" >/dev/null
    else
      start="$(date +%s%3N)"
      python3 -c "import sys; sys.path.insert(0, '$ROOT_DIR/scripts'); from server_conf_contract import normalize_server_conf; normalize_server_conf('$tmp/apps/server-setup-bench')" >/dev/null
      end="$(date +%s%3N)"
      echo "run $i: $((end - start))ms real"
    fi
  done
fi
