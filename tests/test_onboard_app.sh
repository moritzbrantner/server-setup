#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/lib/test-helpers.sh"

declare -i pass_count="${pass_count:-0}"

SCRIPT="$ROOT_DIR/scripts/onboard_app.py"

make_stub_commands() {
  local bin_dir="$1"
  mkdir -p "$bin_dir"

  cat >"$bin_dir/systemctl" <<'SH'
#!/usr/bin/env bash
exit 0
SH

  cat >"$bin_dir/nginx" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "-t" ]]; then
  exit 0
fi
exit 0
SH

  chmod +x "$bin_dir/systemctl" "$bin_dir/nginx"
}

create_repo() {
  local repo_dir="$1"
  local workdir="$2"
  mkdir -p "$repo_dir/dist"

  cat >"$repo_dir/server.conf" <<JSON
{
  "name": "onboard-app",
  "domain": "onboard-app.test",
  "workdir": "$workdir",
  "releases_dir": "$workdir/releases",
  "current_symlink": "$workdir/current",
  "build_output": "dist",
  "runtime": {
    "mode": "static",
    "health_retries": 1,
    "health_interval_seconds": 1
  },
  "service": {
    "name": "onboard-app.service"
  },
  "nginx": {
    "www_redirect": false,
    "tls_hostnames": []
  }
}
JSON

  printf '<html>ok</html>\n' >"$repo_dir/dist/index.html"

  (
    cd "$repo_dir"
    git init -q
    git config user.email test@example.com
    git config user.name test
    git add .
    git commit -qm "init"
    git branch -M main
  )
}

create_repo_without_server_conf() {
  local repo_dir="$1"
  mkdir -p "$repo_dir/public"
  printf '<html>ok</html>\n' >"$repo_dir/public/index.html"

  (
    cd "$repo_dir"
    git init -q
    git config user.email test@example.com
    git config user.name test
    git add .
    git commit -qm "init"
    git branch -M main
  )
}

test_noninteractive_dry_run_does_not_prompt_for_optional_values() {
  local tmp
  tmp="$(make_temp_dir)"
  make_stub_commands "$tmp/bin"
  create_repo "$tmp/repo" "$tmp/workdir"

  PATH="$tmp/bin:$PATH" \
    STATE_DIR="$tmp/state" \
    LOCK_DIR="$tmp/locks" \
    LOG_DIR="$tmp/logs" \
    NGINX_SITE_AVAILABLE_DIR="$tmp/nginx-available" \
    NGINX_SITE_ENABLED_DIR="$tmp/nginx-enabled" \
    NGINX_DEFAULT_SITE_LINK="$tmp/nginx-enabled/default" \
    SYSTEMD_UNIT_DIR="$tmp/systemd" \
    python3 "$SCRIPT" \
      --repo-url "$tmp/repo" \
      --dest "$tmp/dest" \
      --skip-tls \
      --dry-run </dev/null >"$tmp/out.log" 2>"$tmp/error.log"

  grep -q "Onboarding complete." "$tmp/out.log"
  rm -rf "$tmp"
}

test_interactive_dry_run_without_server_conf_builds_single_config_entry() {
  local tmp
  tmp="$(make_temp_dir)"
  make_stub_commands "$tmp/bin"
  create_repo_without_server_conf "$tmp/repo"

  printf '\nmanual-site\nmanual.test\nn\npublic\n\n\n%s\n5\nn\n' "$tmp/workdir" | \
    PATH="$tmp/bin:$PATH" \
    STATE_DIR="$tmp/state" \
    LOCK_DIR="$tmp/locks" \
    LOG_DIR="$tmp/logs" \
    NGINX_SITE_AVAILABLE_DIR="$tmp/nginx-available" \
    NGINX_SITE_ENABLED_DIR="$tmp/nginx-enabled" \
    NGINX_DEFAULT_SITE_LINK="$tmp/nginx-enabled/default" \
    SYSTEMD_UNIT_DIR="$tmp/systemd" \
    python3 "$SCRIPT" \
      --repo-url "$tmp/repo" \
      --dest "$tmp/dest" \
      --skip-tls \
      --dry-run \
      --interactive \
      --summary-output "$tmp/summary.json" >"$tmp/out.log" 2>"$tmp/error.log"

  grep -q "Collecting deploy config interactively" "$tmp/out.log"
  grep -q '"name": "manual-site"' "$tmp/summary.json"
  grep -q '"managed_via": "onboard"' "$tmp/summary.json"
  grep -q '"repo": "'"$tmp/repo"'"' "$tmp/summary.json"
  rm -rf "$tmp"
}

run_test "onboard_app dry-run stays non-interactive when optional args are omitted" test_noninteractive_dry_run_does_not_prompt_for_optional_values
run_test "onboard_app interactive dry-run works without server.conf" test_interactive_dry_run_without_server_conf_builds_single_config_entry

echo "All tests passed: $pass_count"
