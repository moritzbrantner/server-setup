#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

pass_count=0

run_test() {
  local name="$1"
  shift
  echo "==> $name"
  "$@"
  pass_count=$((pass_count + 1))
}

assert_eq() {
  local expected="$1"
  local actual="$2"

  if [[ "$expected" != "$actual" ]]; then
    echo "Assertion failed:" >&2
    echo "  expected: $expected" >&2
    echo "  actual:   $actual" >&2
    exit 1
  fi
}

copy_repo_without_git() {
  local dest="$1"
  mkdir -p "$dest"
  (
    cd "$ROOT_DIR"
    tar --exclude=.git -cf - .
  ) | (
    cd "$dest"
    tar -xf -
  )
}

make_temp_dir() {
  mktemp -d
}
