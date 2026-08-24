#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if ! command -v shellcheck >/dev/null 2>&1; then
  echo "shellcheck is required to run lint checks." >&2
  exit 1
fi

shellcheck -x -P "$ROOT_DIR/tests" \
  "$ROOT_DIR/setup.sh" \
  "$ROOT_DIR"/tests/*.sh \
  "$ROOT_DIR"/tests/lib/*.sh \
  "$ROOT_DIR"/benchmarks/*.sh

if python3 -m ruff --version >/dev/null 2>&1; then
  python3 -m ruff check "$ROOT_DIR/scripts" "$ROOT_DIR/tests"
elif command -v ruff >/dev/null 2>&1; then
  ruff check "$ROOT_DIR/scripts" "$ROOT_DIR/tests"
else
  echo "ruff is not installed; skipping Python lint. Install it with: python3 -m pip install ruff" >&2
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to typecheck monitor/webapp." >&2
  exit 1
fi

(
  cd "$ROOT_DIR/monitor/webapp"
  npm ci --no-audit --no-fund
  npm run typecheck
)
