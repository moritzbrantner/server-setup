#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPT="$ROOT_DIR/scripts/discover-sites.sh"

pass_count=0

run_test() {
  local name="$1"
  shift
  echo "==> $name"
  "$@"
  pass_count=$((pass_count + 1))
}

test_discover_from_local_clone_with_autodetect_repo_branch() {
  local tmp
  tmp="$(mktemp -d)"
  mkdir -p "$tmp/apps/server-setup-copy"
  cp -R "$ROOT_DIR/." "$tmp/apps/server-setup-copy/"

  (
    cd "$tmp/apps/server-setup-copy"
    git init -q
    git config user.email test@example.com
    git config user.name test
    git add .
    git commit -qm "init"
    git branch -M main
    git remote add origin git@github.com:example/server-setup.git

    cat > server.conf <<'JSON'
{
  "name": "server-setup",
  "domain": "server-setup.local",
  "build_output": ".",
  "deploy_hooks": {
    "build": "./scripts/run-self-checks.sh"
  },
  "runtime": {
    "mode": "static"
  },
  "service": {
    "name": "server-setup.service"
  }
}
JSON

    "$SCRIPT" --base-glob "$tmp/apps/*" --output "$tmp/sites.json"

    local discovered_repo discovered_branch
    discovered_repo="$(jq -r '.[0].repo' "$tmp/sites.json")"
    discovered_branch="$(jq -r '.[0].branch' "$tmp/sites.json")"

    [[ "$discovered_repo" == "git@github.com:example/server-setup.git" ]]
    [[ "$discovered_branch" == "main" ]]
  )

  rm -rf "$tmp"
}

test_discover_falls_back_to_absolute_path_without_origin() {
  local tmp
  tmp="$(mktemp -d)"
  mkdir -p "$tmp/apps/no-origin"

  (
    cd "$tmp/apps/no-origin"
    git init -q
    git config user.email test@example.com
    git config user.name test
    touch README.md
    git add README.md
    git commit -qm "init"
    git branch -M trunk

    cat > server.conf <<'JSON'
{
  "name": "no-origin",
  "domain": "no-origin.local",
  "build_output": ".",
  "deploy_hooks": {},
  "runtime": {
    "mode": "static"
  },
  "service": {
    "name": "no-origin.service"
  }
}
JSON

    "$SCRIPT" --base-glob "$tmp/apps/*" --output "$tmp/sites.json"

    local discovered_repo discovered_branch
    discovered_repo="$(jq -r '.[0].repo' "$tmp/sites.json")"
    discovered_branch="$(jq -r '.[0].branch' "$tmp/sites.json")"

    [[ "$discovered_repo" == "$(pwd -P)" ]]
    [[ "$discovered_branch" == "trunk" ]]
  )

  rm -rf "$tmp"
}

run_test "discover auto-detects repo/branch from local clone" test_discover_from_local_clone_with_autodetect_repo_branch
run_test "discover falls back to absolute repo path when origin is missing" test_discover_falls_back_to_absolute_path_without_origin

echo "All tests passed: $pass_count"
