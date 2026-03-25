#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/test-helpers.sh
source "$SCRIPT_DIR/lib/test-helpers.sh"

# Initialized by test-helpers.sh; repeated here so ShellCheck sees it.
declare -i pass_count="${pass_count:-0}"

SCRIPT="$ROOT_DIR/scripts/sync-github-sites.sh"

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
  if [[ -n "${NGINX_SITE_AVAILABLE_DIR:-}" ]] && grep -R "broken.example" "$NGINX_SITE_AVAILABLE_DIR" >/dev/null 2>&1; then
    exit 1
  fi
  exit 0
fi
exit 0
SH

  cat >"$bin_dir/curl" <<'SH'
#!/usr/bin/env bash
if [[ "${FAIL_HEALTHCHECK:-0}" == "1" && "$*" == *"127.0.0.1:"* ]]; then
  exit 22
fi
printf 'ok'
SH

  cat >"$bin_dir/bun" <<'SH'
#!/usr/bin/env bash
exit 0
SH

  chmod +x "$bin_dir/systemctl" "$bin_dir/nginx" "$bin_dir/curl" "$bin_dir/bun"
}

create_repo() {
  local repo_dir="$1"
  local mode="$2"
  mkdir -p "$repo_dir"
  (
    cd "$repo_dir"
    git init -q
    git config user.email test@example.com
    git config user.name test
    mkdir -p dist
    printf '<html>ok</html>\n' > dist/index.html
    if [[ "$mode" == "service" ]]; then
      printf '#!/usr/bin/env bash\nexit 0\n' > run.sh
      chmod +x run.sh
    fi
    git add .
    git commit -qm "init"
    git branch -M main
  )
}

test_dry_run_fails_on_missing_env_placeholder() {
  local tmp
  tmp="$(make_temp_dir)"
  make_stub_commands "$tmp/bin"
  create_repo "$tmp/repos/app" static

  jq -n \
    --arg repo "$tmp/repos/app" \
    --arg releases "$tmp/workdir/releases" \
    --arg current "$tmp/workdir/current" \
    '[
      {
        name: "dry-run-app",
        repo: $repo,
        branch: "main",
        domain: "dry-run.test",
        workdir: "${MISSING_WORKDIR}",
        releases_dir: $releases,
        current_symlink: $current,
        build_output: "dist",
        runtime: {mode: "static", health_retries: 1, health_interval_seconds: 1},
        service: {name: "dry-run-app.service"},
        nginx: {www_redirect: false, tls_hostnames: []}
      }
    ]' >"$tmp/sites.json"

  if PATH="$tmp/bin:$PATH" \
    STATE_DIR="$tmp/state" \
    LOCK_DIR="$tmp/locks" \
    LOG_DIR="$tmp/logs" \
    NGINX_SITE_AVAILABLE_DIR="$tmp/nginx-available" \
    NGINX_SITE_ENABLED_DIR="$tmp/nginx-enabled" \
    NGINX_DEFAULT_SITE_LINK="$tmp/nginx-enabled/default" \
    SYSTEMD_UNIT_DIR="$tmp/systemd" \
    "$SCRIPT" --config "$tmp/sites.json" --dry-run 2>"$tmp/error.log"; then
    echo "Expected dry-run to fail for unresolved environment placeholders" >&2
    exit 1
  fi

  grep -q "Missing required environment variable 'MISSING_WORKDIR'" "$tmp/error.log"
  rm -rf "$tmp"
}

test_failed_health_check_keeps_previous_release_active() {
  local tmp
  tmp="$(make_temp_dir)"
  make_stub_commands "$tmp/bin"
  create_repo "$tmp/repos/service-app" service
  mkdir -p "$tmp/workdir/releases/old-release"
  ln -sfn "$tmp/workdir/releases/old-release" "$tmp/workdir/current"

  cat >"$tmp/sites.json" <<JSON
[
  {
    "name": "service-app",
    "repo": "$tmp/repos/service-app",
    "branch": "main",
    "domain": "service.test",
    "workdir": "$tmp/workdir",
    "releases_dir": "$tmp/workdir/releases",
    "current_symlink": "$tmp/workdir/current",
    "runtime": {
      "mode": "service",
      "command": "bash ./run.sh",
      "port": 3000,
      "health_endpoint": "/health",
      "health_retries": 1,
      "health_interval_seconds": 1
    },
    "service": { "name": "service-app.service" },
    "build_output": "dist",
    "nginx": { "www_redirect": false, "tls_hostnames": [] }
  }
]
JSON

  if PATH="$tmp/bin:$PATH" \
    FAIL_HEALTHCHECK=1 \
    STATE_DIR="$tmp/state" \
    LOCK_DIR="$tmp/locks" \
    LOG_DIR="$tmp/logs" \
    NGINX_SITE_AVAILABLE_DIR="$tmp/nginx-available" \
    NGINX_SITE_ENABLED_DIR="$tmp/nginx-enabled" \
    NGINX_DEFAULT_SITE_LINK="$tmp/nginx-enabled/default" \
    SYSTEMD_UNIT_DIR="$tmp/systemd" \
    "$SCRIPT" --config "$tmp/sites.json" >"$tmp/out.log" 2>"$tmp/error.log"; then
    echo "Expected deploy to fail on health check" >&2
    exit 1
  fi

  assert_eq "$tmp/workdir/releases/old-release" "$(readlink -f "$tmp/workdir/current")"
  rm -rf "$tmp"
}

test_invalid_nginx_config_restores_last_good_config() {
  local tmp
  tmp="$(make_temp_dir)"
  make_stub_commands "$tmp/bin"
  create_repo "$tmp/repos/static-app" static
  mkdir -p "$tmp/nginx-available" "$tmp/nginx-enabled"
  printf 'good config\n' >"$tmp/nginx-available/static-app.conf"
  mkdir -p "$tmp/workdir/releases/old-release"
  ln -sfn "$tmp/workdir/releases/old-release" "$tmp/workdir/current"

  cat >"$tmp/sites.json" <<JSON
[
  {
    "name": "static-app",
    "repo": "$tmp/repos/static-app",
    "branch": "main",
    "domain": "broken.example",
    "workdir": "$tmp/workdir",
    "releases_dir": "$tmp/workdir/releases",
    "current_symlink": "$tmp/workdir/current",
    "build_output": "dist",
    "runtime": { "mode": "static", "health_retries": 1, "health_interval_seconds": 1 },
    "service": { "name": "static-app.service" },
    "nginx": { "www_redirect": false, "tls_hostnames": [] }
  }
]
JSON

  if PATH="$tmp/bin:$PATH" \
    STATE_DIR="$tmp/state" \
    LOCK_DIR="$tmp/locks" \
    LOG_DIR="$tmp/logs" \
    NGINX_SITE_AVAILABLE_DIR="$tmp/nginx-available" \
    NGINX_SITE_ENABLED_DIR="$tmp/nginx-enabled" \
    NGINX_DEFAULT_SITE_LINK="$tmp/nginx-enabled/default" \
    SYSTEMD_UNIT_DIR="$tmp/systemd" \
    "$SCRIPT" --config "$tmp/sites.json" >"$tmp/out.log" 2>"$tmp/error.log"; then
    echo "Expected deploy to fail when nginx validation fails" >&2
    exit 1
  fi

  assert_eq "good config" "$(tr -d '\n' < "$tmp/nginx-available/static-app.conf")"
  rm -rf "$tmp"
}

test_service_unit_adds_bun_path_and_runtime_command() {
  local tmp
  tmp="$(make_temp_dir)"
  make_stub_commands "$tmp/bin"
  create_repo "$tmp/repos/service-app" service

  cat >"$tmp/sites.json" <<JSON
[
  {
    "name": "service-app",
    "repo": "$tmp/repos/service-app",
    "branch": "main",
    "domain": "service.test",
    "workdir": "$tmp/workdir",
    "releases_dir": "$tmp/workdir/releases",
    "current_symlink": "$tmp/workdir/current",
    "runtime": {
      "mode": "service",
      "command": "PORT=3000 bun run start",
      "port": 3000,
      "health_endpoint": "/health",
      "health_retries": 1,
      "health_interval_seconds": 1
    },
    "service": { "name": "service-app.service" },
    "build_output": "dist",
    "nginx": { "www_redirect": false, "tls_hostnames": [] }
  }
]
JSON

  PATH="$tmp/bin:$PATH" \
    STATE_DIR="$tmp/state" \
    LOCK_DIR="$tmp/locks" \
    LOG_DIR="$tmp/logs" \
    NGINX_SITE_AVAILABLE_DIR="$tmp/nginx-available" \
    NGINX_SITE_ENABLED_DIR="$tmp/nginx-enabled" \
    NGINX_DEFAULT_SITE_LINK="$tmp/nginx-enabled/default" \
    SYSTEMD_UNIT_DIR="$tmp/systemd" \
    "$SCRIPT" --config "$tmp/sites.json" >"$tmp/out.log" 2>"$tmp/error.log"

  grep -q 'export BUN_INSTALL=' "$tmp/systemd/app-service-app.service"
  grep -q 'export PATH=' "$tmp/systemd/app-service-app.service"
  grep -q 'PORT=3000 bun run start' "$tmp/systemd/app-service-app.service"
  rm -rf "$tmp"
}

test_service_nginx_config_uses_https_when_letsencrypt_files_exist() {
  local tmp
  tmp="$(make_temp_dir)"
  make_stub_commands "$tmp/bin"
  create_repo "$tmp/repos/service-app" service
  mkdir -p "$tmp/letsencrypt/live/service.test"
  printf 'fullchain\n' >"$tmp/letsencrypt/live/service.test/fullchain.pem"
  printf 'privkey\n' >"$tmp/letsencrypt/live/service.test/privkey.pem"
  printf 'options\n' >"$tmp/letsencrypt/options-ssl-nginx.conf"
  printf 'dhparams\n' >"$tmp/letsencrypt/ssl-dhparams.pem"

  cat >"$tmp/sites.json" <<JSON
[
  {
    "name": "service-app",
    "repo": "$tmp/repos/service-app",
    "branch": "main",
    "domain": "service.test",
    "workdir": "$tmp/workdir",
    "releases_dir": "$tmp/workdir/releases",
    "current_symlink": "$tmp/workdir/current",
    "runtime": {
      "mode": "service",
      "command": "bash ./run.sh",
      "port": 3000,
      "health_endpoint": "/health",
      "health_retries": 1,
      "health_interval_seconds": 1
    },
    "service": { "name": "service-app.service" },
    "build_output": "dist",
    "nginx": {
      "www_redirect": true,
      "tls_hostnames": ["service.test", "www.service.test"]
    }
  }
]
JSON

  PATH="$tmp/bin:$PATH" \
    STATE_DIR="$tmp/state" \
    LOCK_DIR="$tmp/locks" \
    LOG_DIR="$tmp/logs" \
    NGINX_SITE_AVAILABLE_DIR="$tmp/nginx-available" \
    NGINX_SITE_ENABLED_DIR="$tmp/nginx-enabled" \
    NGINX_DEFAULT_SITE_LINK="$tmp/nginx-enabled/default" \
    SYSTEMD_UNIT_DIR="$tmp/systemd" \
    LETSENCRYPT_LIVE_DIR="$tmp/letsencrypt/live" \
    LETSENCRYPT_OPTIONS_PATH="$tmp/letsencrypt/options-ssl-nginx.conf" \
    LETSENCRYPT_DHPARAM_PATH="$tmp/letsencrypt/ssl-dhparams.pem" \
    "$SCRIPT" --config "$tmp/sites.json" >"$tmp/out.log" 2>"$tmp/error.log"

  grep -q 'listen 443 ssl;' "$tmp/nginx-available/service-app.conf"
  grep -q 'server_name service.test;' "$tmp/nginx-available/service-app.conf"
  grep -q 'server_name www.service.test;' "$tmp/nginx-available/service-app.conf"
  grep -q "return 301 https://service.test\$request_uri;" "$tmp/nginx-available/service-app.conf"
  grep -q "proxy_set_header X-Forwarded-Host \$host;" "$tmp/nginx-available/service-app.conf"
  grep -q "proxy_set_header X-Forwarded-Port \$server_port;" "$tmp/nginx-available/service-app.conf"
  grep -q "proxy_set_header Upgrade \$http_upgrade;" "$tmp/nginx-available/service-app.conf"
  grep -q 'proxy_set_header Connection "upgrade";' "$tmp/nginx-available/service-app.conf"
  rm -rf "$tmp"
}

test_preflight_rejects_absolute_runtime_working_dir() {
  local tmp
  tmp="$(make_temp_dir)"
  make_stub_commands "$tmp/bin"
  create_repo "$tmp/repos/service-app" service

  cat >"$tmp/sites.json" <<JSON
[
  {
    "name": "service-app",
    "repo": "$tmp/repos/service-app",
    "branch": "main",
    "domain": "service.test",
    "workdir": "$tmp/workdir",
    "releases_dir": "$tmp/workdir/releases",
    "current_symlink": "$tmp/workdir/current",
    "runtime": {
      "mode": "service",
      "command": "PORT=3000 bun run start",
      "working_dir": "/root/apps/service-app",
      "port": 3000,
      "health_endpoint": "/health",
      "health_retries": 1,
      "health_interval_seconds": 1
    },
    "service": { "name": "service-app.service" },
    "build_output": "dist",
    "nginx": { "www_redirect": false, "tls_hostnames": [] }
  }
]
JSON

  if PATH="$tmp/bin:$PATH" \
    STATE_DIR="$tmp/state" \
    LOCK_DIR="$tmp/locks" \
    LOG_DIR="$tmp/logs" \
    NGINX_SITE_AVAILABLE_DIR="$tmp/nginx-available" \
    NGINX_SITE_ENABLED_DIR="$tmp/nginx-enabled" \
    NGINX_DEFAULT_SITE_LINK="$tmp/nginx-enabled/default" \
    SYSTEMD_UNIT_DIR="$tmp/systemd" \
    "$SCRIPT" --config "$tmp/sites.json" --preflight-only >"$tmp/out.log" 2>"$tmp/error.log"; then
    echo "Expected preflight to fail for absolute runtime.working_dir" >&2
    exit 1
  fi

  grep -q "runtime.working_dir must be relative to the deployed release" "$tmp/error.log"
  rm -rf "$tmp"
}

run_test "sync dry-run rejects unresolved env placeholders" test_dry_run_fails_on_missing_env_placeholder
run_test "sync keeps previous release when health check fails" test_failed_health_check_keeps_previous_release_active
run_test "sync restores last good nginx config when validation fails" test_invalid_nginx_config_restores_last_good_config
run_test "sync service unit exports bun path for runtime commands" test_service_unit_adds_bun_path_and_runtime_command
run_test "sync nginx config renders https blocks when letsencrypt material exists" test_service_nginx_config_uses_https_when_letsencrypt_files_exist
run_test "sync preflight rejects absolute runtime working_dir" test_preflight_rejects_absolute_runtime_working_dir

echo "All tests passed: $pass_count"
