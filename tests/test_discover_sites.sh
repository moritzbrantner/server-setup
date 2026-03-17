#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/lib/test-helpers.sh"

SCRIPT="$ROOT_DIR/scripts/discover-sites.sh"

test_discover_from_local_clone_with_autodetect_repo_branch() {
  local tmp
  tmp="$(make_temp_dir)"
  mkdir -p "$tmp/apps/server-setup-copy"
  copy_repo_without_git "$tmp/apps/server-setup-copy"

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

    assert_eq "git@github.com:example/server-setup.git" "$(jq -r '.[0].repo' "$tmp/sites.json")"
    assert_eq "main" "$(jq -r '.[0].branch' "$tmp/sites.json")"
  )

  rm -rf "$tmp"
}

test_discover_falls_back_to_absolute_path_without_origin() {
  local tmp
  tmp="$(make_temp_dir)"
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

    assert_eq "$(pwd -P)" "$(jq -r '.[0].repo' "$tmp/sites.json")"
    assert_eq "trunk" "$(jq -r '.[0].branch' "$tmp/sites.json")"
  )

  rm -rf "$tmp"
}

test_discover_normalizes_nginx_settings() {
  local tmp
  tmp="$(make_temp_dir)"
  mkdir -p "$tmp/apps/nginx-site"

  cat > "$tmp/apps/nginx-site/server.conf" <<'JSON'
{
  "name": "nginx-site",
  "repo": "git@github.com:example/nginx-site.git",
  "branch": "main",
  "domain": "example.test",
  "build_output": "dist",
  "deploy_hooks": {},
  "runtime": {
    "mode": "static"
  },
  "service": {
    "name": "nginx-site.service"
  },
  "nginx": {
    "www_redirect": true,
    "tls_hostnames": ["example.test", "www.example.test"]
  }
}
JSON

  "$SCRIPT" --base-glob "$tmp/apps/*" --output "$tmp/sites.json"

  assert_eq "true" "$(jq -r '.[0].nginx.www_redirect' "$tmp/sites.json")"
  assert_eq "example.test www.example.test" "$(jq -r '.[0].nginx.tls_hostnames | join(" ")' "$tmp/sites.json")"

  rm -rf "$tmp"
}

test_discover_rejects_duplicate_domains() {
  local tmp
  tmp="$(make_temp_dir)"
  mkdir -p "$tmp/apps/one" "$tmp/apps/two"

  cat > "$tmp/apps/one/server.conf" <<'JSON'
{
  "name": "one",
  "repo": "git@github.com:example/one.git",
  "branch": "main",
  "domain": "dup.test",
  "build_output": "dist",
  "deploy_hooks": {},
  "runtime": { "mode": "static" },
  "service": { "name": "one.service" }
}
JSON

  cat > "$tmp/apps/two/server.conf" <<'JSON'
{
  "name": "two",
  "repo": "git@github.com:example/two.git",
  "branch": "main",
  "domain": "dup.test",
  "build_output": "dist",
  "deploy_hooks": {},
  "runtime": { "mode": "static" },
  "service": { "name": "two.service" }
}
JSON

  if "$SCRIPT" --base-glob "$tmp/apps/*" --output "$tmp/sites.json" 2>"$tmp/error.log"; then
    echo "Expected duplicate domain validation to fail" >&2
    exit 1
  fi

  grep -q "duplicate site domain" "$tmp/error.log"
  rm -rf "$tmp"
}

test_discover_rejects_invalid_runtime_port() {
  local tmp
  tmp="$(make_temp_dir)"
  mkdir -p "$tmp/apps/bad-service"

  cat > "$tmp/apps/bad-service/server.conf" <<'JSON'
{
  "name": "bad-service",
  "repo": "git@github.com:example/bad-service.git",
  "branch": "main",
  "domain": "bad-service.test",
  "build_output": "dist",
  "deploy_hooks": {},
  "runtime": {
    "mode": "service",
    "command": "npm run start",
    "port": "abc"
  },
  "service": { "name": "bad-service.service" }
}
JSON

  if "$SCRIPT" --base-glob "$tmp/apps/*" --output "$tmp/sites.json" 2>"$tmp/error.log"; then
    echo "Expected runtime.port validation to fail" >&2
    exit 1
  fi

  grep -q "runtime.port must be numeric" "$tmp/error.log"
  rm -rf "$tmp"
}

test_discover_rejects_absolute_runtime_working_dir() {
  local tmp
  tmp="$(make_temp_dir)"
  mkdir -p "$tmp/apps/bad-working-dir"

  cat > "$tmp/apps/bad-working-dir/server.conf" <<'JSON'
{
  "name": "bad-working-dir",
  "repo": "git@github.com:example/bad-working-dir.git",
  "branch": "main",
  "domain": "bad-working-dir.test",
  "build_output": "dist",
  "deploy_hooks": {},
  "runtime": {
    "mode": "service",
    "command": "bun run start",
    "working_dir": "/root/apps/bad-working-dir",
    "port": 3000
  },
  "service": { "name": "bad-working-dir.service" }
}
JSON

  if "$SCRIPT" --base-glob "$tmp/apps/*" --output "$tmp/sites.json" 2>"$tmp/error.log"; then
    echo "Expected absolute runtime.working_dir validation to fail" >&2
    exit 1
  fi

  grep -q "runtime.working_dir must be relative to the deployed release" "$tmp/error.log"
  rm -rf "$tmp"
}

test_discover_fails_when_no_server_conf_files_are_found() {
  local tmp
  tmp="$(make_temp_dir)"
  mkdir -p "$tmp/apps/no-config"

  if "$SCRIPT" --base-glob "$tmp/apps/*" --output "$tmp/sites.json" 2>"$tmp/error.log"; then
    echo "Expected discovery to fail when no server.conf files are found" >&2
    exit 1
  fi

  grep -q "No valid server.conf files found under base glob" "$tmp/error.log"
  rm -rf "$tmp"
}

run_test "discover auto-detects repo/branch from local clone" test_discover_from_local_clone_with_autodetect_repo_branch
run_test "discover falls back to absolute repo path when origin is missing" test_discover_falls_back_to_absolute_path_without_origin
run_test "discover normalizes nginx settings" test_discover_normalizes_nginx_settings
run_test "discover rejects duplicate domains" test_discover_rejects_duplicate_domains
run_test "discover rejects invalid runtime port" test_discover_rejects_invalid_runtime_port
run_test "discover rejects absolute runtime working_dir" test_discover_rejects_absolute_runtime_working_dir
run_test "discover fails when no server.conf files are found" test_discover_fails_when_no_server_conf_files_are_found

echo "All tests passed: $pass_count"
