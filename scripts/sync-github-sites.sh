#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/sync-github-sites.sh [--config deploy/sites.json] [--site SITE_NAME]
  ./scripts/sync-github-sites.sh [--discover-base '/srv/apps/*'] [--config deploy/sites.json] [--site SITE_NAME]
  ./scripts/sync-github-sites.sh [--config deploy/sites.json] --rollback SITE_NAME

Description:
  - Pulls website repos from GitHub.
  - Checks out configured branch.
  - For release-based sites, deploys into a timestamped release directory and atomically switches the current symlink.
  - Runs optional build and deploy hooks.
  - Runs Unlighthouse metrics collection after each deployment.

Config format (JSON array):
[
  {
    "name": "marketing-site",
    "repo": "git@github.com:org/marketing-site.git",
    "branch": "main",
    "workdir": "/srv/github-sites/marketing-site",
    "releases_dir": "/srv/github-sites/marketing-site/releases",
    "current_symlink": "/srv/github-sites/marketing-site/current",
    "keep_releases": 5,
    "site_url": "https://example.com",
    "git_ssh_command": "ssh -i ${MARKETING_DEPLOY_KEY_PATH} -o IdentitiesOnly=yes",
    "deploy_script": "scripts/deploy.sh",
    "pre_deploy_cmd": "echo Preparing deploy",
    "build_cmd": "bun install --frozen-lockfile && bun run build",
    "post_deploy_cmd": "sudo systemctl reload nginx",
    "unlighthouse_server_url": "${UNLIGHTHOUSE_SERVER_URL}",
    "unlighthouse_server_token": "${UNLIGHTHOUSE_SERVER_TOKEN}",
    "unlighthouse_cmd": "npx --yes unlighthouse-ci@latest --site https://example.com"
  }
]
USAGE
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

CONFIG_PATH="deploy/sites.json"
DISCOVER_BASE=""
ONLY_SITE=""
ROLLBACK_SITE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --discover-base)
      DISCOVER_BASE="${2:-}"
      shift 2
      ;;
    --site)
      ONLY_SITE="${2:-}"
      shift 2
      ;;
    --rollback)
      ROLLBACK_SITE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -n "$ROLLBACK_SITE" && -n "$ONLY_SITE" ]]; then
  echo "--site and --rollback are mutually exclusive." >&2
  exit 1
fi

require_cmd git
require_cmd jq
require_cmd curl

if [[ -n "$DISCOVER_BASE" ]]; then
  ./scripts/discover-sites.sh --base-glob "$DISCOVER_BASE" --output "$CONFIG_PATH"
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Config file not found: $CONFIG_PATH" >&2
  exit 1
fi

SITE_COUNT=$(jq 'length' "$CONFIG_PATH")
if [[ "$SITE_COUNT" -eq 0 ]]; then
  echo "No sites defined in $CONFIG_PATH"
  exit 0
fi

run_optional() {
  local cmd="$1"
  local where="$2"

  if [[ -n "$cmd" && "$cmd" != "null" ]]; then
    echo "  - Running ${where}: $cmd"
    bash -lc "$cmd"
  fi
}

resolve_config_value() {
  local site_name="$1"
  local field_name="$2"
  local raw_value="$3"

  if [[ -z "$raw_value" || "$raw_value" == "null" ]]; then
    echo "$raw_value"
    return
  fi

  local resolved="$raw_value"
  while [[ "$resolved" =~ \$\{([A-Za-z_][A-Za-z0-9_]*)\} ]]; do
    local env_var="${BASH_REMATCH[1]}"
    local env_value="${!env_var-}"

    if [[ -z "$env_value" ]]; then
      echo "Missing required environment variable '$env_var' for site '$site_name' field '$field_name'." >&2
      exit 1
    fi

    local token="\${${env_var}}"
    resolved="${resolved//${token}/$env_value}"
  done

  echo "$resolved"
}

run_git() {
  local git_ssh_command="$1"
  shift

  if [[ -n "$git_ssh_command" && "$git_ssh_command" != "null" ]]; then
    GIT_SSH_COMMAND="$git_ssh_command" git "$@"
    return
  fi

  git "$@"
}

run_unlighthouse() {
  local site_name="$1"
  local site_url="$2"
  local unlighthouse_cmd="$3"
  local unlighthouse_server_url="$4"
  local unlighthouse_server_token="$5"

  if [[ -n "$unlighthouse_cmd" && "$unlighthouse_cmd" != "null" ]]; then
    echo "  - Running unlighthouse_cmd: $unlighthouse_cmd"
    bash -lc "$unlighthouse_cmd"
    return
  fi

  if [[ -z "$site_url" || "$site_url" == "null" ]]; then
    echo "  - Skipping Unlighthouse (site_url not configured)."
    return
  fi

  require_cmd npx

  local ts report_dir
  ts=$(date +%Y%m%d-%H%M%S)
  report_dir="/var/log/unlighthouse/${site_name}/${ts}"
  mkdir -p "$report_dir"

  echo "  - Running Unlighthouse against $site_url"
  echo "  - Report output: $report_dir"

  local cmd=(npx --yes unlighthouse-ci@latest --site "$site_url" --output-path "$report_dir")

  if [[ -n "$unlighthouse_server_url" && "$unlighthouse_server_url" != "null" ]]; then
    echo "  - Uploading report to Unlighthouse server: $unlighthouse_server_url"
    cmd+=(--server "$unlighthouse_server_url" --build-name "$site_name")

    if [[ -n "$unlighthouse_server_token" && "$unlighthouse_server_token" != "null" ]]; then
      cmd+=(--auth "$unlighthouse_server_token")
    fi
  fi

  "${cmd[@]}"
}

render_systemd_unit() {
  local name="$1"
  local command="$2"
  local working_dir="$3"
  local run_user="$4"
  local env_file="$5"

  cat <<UNIT
[Unit]
Description=Runtime service for ${name}
After=network.target

[Service]
Type=simple
WorkingDirectory=${working_dir}
ExecStart=/usr/bin/env bash -lc '${command}'
Restart=always
RestartSec=3
User=${run_user}
${env_file:+EnvironmentFile=${env_file}}

[Install]
WantedBy=multi-user.target
UNIT
}

write_if_changed() {
  local path="$1"
  local content="$2"
  local tmp
  tmp=$(mktemp)
  printf '%s\n' "$content" >"$tmp"

  if [[ -f "$path" ]] && cmp -s "$tmp" "$path"; then
    rm -f "$tmp"
    return 1
  fi

  mkdir -p "$(dirname "$path")"
  install -m 0644 "$tmp" "$path"
  rm -f "$tmp"
  return 0
}

ensure_runtime_service() {
  local site_name="$1"
  local runtime_mode="$2"
  local runtime_command="$3"
  local runtime_working_dir="$4"
  local runtime_user="$5"
  local runtime_env_file="$6"

  if [[ "$runtime_mode" != "service" ]]; then
    return
  fi

  local unit_path="/etc/systemd/system/app-${site_name}.service"
  local unit_content
  unit_content=$(render_systemd_unit "$site_name" "$runtime_command" "$runtime_working_dir" "$runtime_user" "$runtime_env_file")

  if write_if_changed "$unit_path" "$unit_content"; then
    echo "  - Updated systemd unit: $unit_path"
    systemctl daemon-reload
    systemctl enable "app-${site_name}.service"
    systemctl restart "app-${site_name}.service"
  else
    echo "  - Systemd unit unchanged: $unit_path"
  fi
}

render_nginx_site_config() {
  local site_name="$1"
  local domain="$2"
  local runtime_mode="$3"
  local static_root="$4"
  local runtime_port="$5"
  local www_redirect="$6"
  local tls_hostnames_csv="$7"

  local server_names="$domain"
  if [[ -n "$tls_hostnames_csv" ]]; then
    server_names="$tls_hostnames_csv"
  fi

  local redirect_block=""
  if [[ "$www_redirect" == "true" ]]; then
    local www_domain="www.${domain}"
    redirect_block=$(cat <<BLOCK

server {
    listen 80;
    listen [::]:80;
    server_name ${www_domain};
    return 301 http://${domain}\$request_uri;
}
BLOCK
)
  fi

  local location_block
  if [[ "$runtime_mode" == "service" ]]; then
    location_block=$(cat <<BLOCK
    location / {
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_pass http://127.0.0.1:${runtime_port};
    }
BLOCK
)
  else
    location_block=$(cat <<BLOCK
    root ${static_root};
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }
BLOCK
)
  fi

  cat <<CONF
server {
    listen 80;
    listen [::]:80;
    server_name ${server_names};

${location_block}

    access_log /var/log/nginx/${site_name}.access.log;
    error_log  /var/log/nginx/${site_name}.error.log;
}
${redirect_block}
CONF
}

apply_nginx_site_config() {
  local site_name="$1"
  local domain="$2"
  local runtime_mode="$3"
  local release_dir="$4"
  local static_relative_root="$5"
  local runtime_port="$6"
  local www_redirect="$7"
  local tls_hostnames_csv="$8"

  local site_conf="/etc/nginx/sites-available/${site_name}.conf"
  local site_link="/etc/nginx/sites-enabled/${site_name}.conf"
  local default_link="/etc/nginx/sites-enabled/default"
  local backup_conf="${site_conf}.last-good"
  local static_root=""

  if [[ "$runtime_mode" == "static" ]]; then
    if [[ -z "$static_relative_root" ]]; then
      echo "  - Missing static root for site '$site_name'." >&2
      return 1
    fi

    if [[ "$static_relative_root" == "/" ]]; then
      static_root="$release_dir"
    else
      static_root="$release_dir/$static_relative_root"
    fi

    if [[ ! -d "$static_root" ]]; then
      echo "  - Static root does not exist for site '$site_name': $static_root" >&2
      return 1
    fi
  fi

  local conf_content
  conf_content=$(render_nginx_site_config "$site_name" "$domain" "$runtime_mode" "$static_root" "$runtime_port" "$www_redirect" "$tls_hostnames_csv")

  local had_existing=0
  if [[ -f "$site_conf" ]]; then
    had_existing=1
    cp "$site_conf" "$backup_conf"
  fi

  if write_if_changed "$site_conf" "$conf_content"; then
    echo "  - Updated Nginx site config: $site_conf"
  else
    echo "  - Nginx site config unchanged: $site_conf"
  fi

  ln -sfn "$site_conf" "$site_link"
  if [[ -L "$default_link" ]]; then
    rm -f "$default_link"
  fi

  if nginx -t; then
    systemctl reload nginx
    cp "$site_conf" "$backup_conf"
    echo "  - Nginx configuration validated and reloaded"
    return 0
  fi

  echo "  - nginx -t failed for site '$site_name'; restoring last known-good config" >&2
  if [[ -f "$backup_conf" ]]; then
    cp "$backup_conf" "$site_conf"
  elif [[ "$had_existing" -eq 0 ]]; then
    rm -f "$site_conf" "$site_link"
  fi

  if ! nginx -t >/dev/null 2>&1; then
    echo "  - WARNING: nginx config is still invalid after restore; manual intervention required." >&2
  fi

  return 1
}

wait_for_service_health() {
  local runtime_mode="$1"
  local port="$2"
  local endpoint="$3"

  if [[ "$runtime_mode" != "service" ]]; then
    return 0
  fi

  local url="http://127.0.0.1:${port}${endpoint}"
  local attempts=20
  local delay=2

  echo "  - Waiting for service health: $url"
  for _ in $(seq 1 "$attempts"); do
    if curl --silent --show-error --fail --max-time 2 "$url" >/dev/null; then
      echo "  - Health check passed"
      return 0
    fi
    sleep "$delay"
  done

  echo "  - Health check failed: $url" >&2
  return 1
}

atomic_switch_symlink() {
  local symlink_path="$1"
  local new_target="$2"
  local tmp_path="${symlink_path}.next"

  ln -sfn "$new_target" "$tmp_path"
  mv -Tf "$tmp_path" "$symlink_path"
}

capture_current_target() {
  local symlink_path="$1"

  if [[ -L "$symlink_path" ]]; then
    readlink -f "$symlink_path"
    return
  fi

  if [[ -d "$symlink_path" ]]; then
    readlink -f "$symlink_path"
    return
  fi

  echo ""
}

cleanup_old_releases() {
  local releases_dir="$1"
  local keep_releases="$2"
  local current_target="$3"
  local previous_target="$4"

  if ! [[ "$keep_releases" =~ ^[0-9]+$ ]]; then
    echo "  - keep_releases '$keep_releases' is not a non-negative integer; skipping cleanup." >&2
    return
  fi

  mapfile -t release_paths < <(find "$releases_dir" -mindepth 1 -maxdepth 1 -type d -printf '%p\n' | sort)
  local total="${#release_paths[@]}"

  if (( total <= keep_releases )); then
    return
  fi

  local remove_count=$((total - keep_releases))
  local removed=0

  for candidate in "${release_paths[@]}"; do
    if (( removed >= remove_count )); then
      break
    fi

    if [[ "$candidate" == "$current_target" || "$candidate" == "$previous_target" ]]; then
      continue
    fi

    echo "  - Cleaning old release: $candidate"
    rm -rf "$candidate"
    removed=$((removed + 1))
  done
}

rollback_site() {
  local site_name="$1"
  local config_path="$2"

  local site_json
  site_json=$(jq -c --arg site "$site_name" '.[] | select(.name == $site)' "$config_path")
  if [[ -z "$site_json" ]]; then
    echo "Site '$site_name' was not found in $config_path" >&2
    exit 1
  fi

  local workdir releases_dir current_symlink
  workdir=$(jq -r '.workdir // empty' <<<"$site_json")
  releases_dir=$(jq -r '.releases_dir // empty' <<<"$site_json")
  current_symlink=$(jq -r '.current_symlink // empty' <<<"$site_json")

  workdir=$(resolve_config_value "$site_name" "workdir" "$workdir")
  releases_dir=$(resolve_config_value "$site_name" "releases_dir" "$releases_dir")
  current_symlink=$(resolve_config_value "$site_name" "current_symlink" "$current_symlink")

  if [[ -z "$workdir" ]]; then
    echo "Site '$site_name' must define workdir for rollback." >&2
    exit 1
  fi

  if [[ -z "$releases_dir" ]]; then
    releases_dir="$workdir/releases"
  fi

  if [[ -z "$current_symlink" ]]; then
    current_symlink="$workdir/current"
  fi

  local previous_pointer_file="${releases_dir}/.previous_release"
  if [[ ! -f "$previous_pointer_file" ]]; then
    echo "No rollback metadata found for '$site_name' at $previous_pointer_file" >&2
    exit 1
  fi

  local previous_target
  previous_target=$(<"$previous_pointer_file")
  if [[ -z "$previous_target" || ! -d "$previous_target" ]]; then
    echo "Rollback target is invalid for '$site_name': $previous_target" >&2
    exit 1
  fi

  local current_target
  current_target=$(capture_current_target "$current_symlink")

  echo "==> Rolling back site: $site_name"
  echo "  - Current:  ${current_target:-<none>}"
  echo "  - Target:   $previous_target"

  atomic_switch_symlink "$current_symlink" "$previous_target"

  if [[ -n "$current_target" && -d "$current_target" ]]; then
    printf '%s\n' "$current_target" >"$previous_pointer_file"
    echo "  - Updated rollback pointer to: $current_target"
  fi

  echo "Rollback complete for '$site_name'."
}

if [[ -n "$ROLLBACK_SITE" ]]; then
  rollback_site "$ROLLBACK_SITE" "$CONFIG_PATH"
  exit 0
fi

for index in $(seq 0 $((SITE_COUNT - 1))); do
  site_json=$(jq ".[$index]" "$CONFIG_PATH")
  name=$(jq -r '.name // empty' <<<"$site_json")
  repo=$(jq -r '.repo // empty' <<<"$site_json")
  branch=$(jq -r '.branch // "main"' <<<"$site_json")
  workdir=$(jq -r '.workdir // empty' <<<"$site_json")
  releases_dir=$(jq -r '.releases_dir // empty' <<<"$site_json")
  current_symlink=$(jq -r '.current_symlink // empty' <<<"$site_json")
  keep_releases=$(jq -r '.keep_releases // 5' <<<"$site_json")
  site_url=$(jq -r '.site_url // empty' <<<"$site_json")
  git_ssh_command=$(jq -r '.git_ssh_command // empty' <<<"$site_json")
  deploy_script=$(jq -r '.deploy_script // empty' <<<"$site_json")
  pre_deploy_cmd=$(jq -r '.pre_deploy_cmd // empty' <<<"$site_json")
  build_cmd=$(jq -r '.build_cmd // empty' <<<"$site_json")
  post_deploy_cmd=$(jq -r '.post_deploy_cmd // empty' <<<"$site_json")
  unlighthouse_cmd=$(jq -r '.unlighthouse_cmd // empty' <<<"$site_json")
  unlighthouse_server_url=$(jq -r '.unlighthouse_server_url // empty' <<<"$site_json")
  unlighthouse_server_token=$(jq -r '.unlighthouse_server_token // empty' <<<"$site_json")
  runtime_mode=$(jq -r '.runtime.mode // .runtime.type // "static"' <<<"$site_json")
  runtime_command=$(jq -r '.runtime.command // empty' <<<"$site_json")
  runtime_working_dir=$(jq -r '.runtime.working_dir // empty' <<<"$site_json")
  runtime_user=$(jq -r '.runtime.user // empty' <<<"$site_json")
  runtime_env_file=$(jq -r '.runtime.env_file // empty' <<<"$site_json")
  runtime_port=$(jq -r '.runtime.port // empty' <<<"$site_json")
  runtime_health_endpoint=$(jq -r '.runtime.health_endpoint // "/health"' <<<"$site_json")
  domain=$(jq -r '.domain // empty' <<<"$site_json")
  web_root=$(jq -r '.web_root // empty' <<<"$site_json")
  build_output=$(jq -r '.build_output // empty' <<<"$site_json")
  nginx_www_redirect=$(jq -r '.nginx.www_redirect // false' <<<"$site_json")
  nginx_tls_hostnames_csv=$(jq -r '(.nginx.tls_hostnames // []) | join(" ")' <<<"$site_json")

  name=$(resolve_config_value "site-$index" "name" "$name")
  repo=$(resolve_config_value "$name" "repo" "$repo")
  branch=$(resolve_config_value "$name" "branch" "$branch")
  workdir=$(resolve_config_value "$name" "workdir" "$workdir")
  releases_dir=$(resolve_config_value "$name" "releases_dir" "$releases_dir")
  current_symlink=$(resolve_config_value "$name" "current_symlink" "$current_symlink")
  keep_releases=$(resolve_config_value "$name" "keep_releases" "$keep_releases")
  site_url=$(resolve_config_value "$name" "site_url" "$site_url")
  git_ssh_command=$(resolve_config_value "$name" "git_ssh_command" "$git_ssh_command")
  deploy_script=$(resolve_config_value "$name" "deploy_script" "$deploy_script")
  pre_deploy_cmd=$(resolve_config_value "$name" "pre_deploy_cmd" "$pre_deploy_cmd")
  build_cmd=$(resolve_config_value "$name" "build_cmd" "$build_cmd")
  post_deploy_cmd=$(resolve_config_value "$name" "post_deploy_cmd" "$post_deploy_cmd")
  unlighthouse_cmd=$(resolve_config_value "$name" "unlighthouse_cmd" "$unlighthouse_cmd")
  unlighthouse_server_url=$(resolve_config_value "$name" "unlighthouse_server_url" "$unlighthouse_server_url")
  unlighthouse_server_token=$(resolve_config_value "$name" "unlighthouse_server_token" "$unlighthouse_server_token")
  runtime_mode=$(resolve_config_value "$name" "runtime.mode" "$runtime_mode")
  runtime_command=$(resolve_config_value "$name" "runtime.command" "$runtime_command")
  runtime_working_dir=$(resolve_config_value "$name" "runtime.working_dir" "$runtime_working_dir")
  runtime_user=$(resolve_config_value "$name" "runtime.user" "$runtime_user")
  runtime_env_file=$(resolve_config_value "$name" "runtime.env_file" "$runtime_env_file")
  runtime_port=$(resolve_config_value "$name" "runtime.port" "$runtime_port")
  runtime_health_endpoint=$(resolve_config_value "$name" "runtime.health_endpoint" "$runtime_health_endpoint")
  domain=$(resolve_config_value "$name" "domain" "$domain")
  web_root=$(resolve_config_value "$name" "web_root" "$web_root")
  build_output=$(resolve_config_value "$name" "build_output" "$build_output")

  if [[ -z "$unlighthouse_server_url" ]]; then
    unlighthouse_server_url="${UNLIGHTHOUSE_SERVER_URL:-}"
  fi

  if [[ -z "$unlighthouse_server_token" ]]; then
    unlighthouse_server_token="${UNLIGHTHOUSE_SERVER_TOKEN:-}"
  fi

  if [[ -z "$name" || -z "$repo" || -z "$workdir" || -z "$domain" ]]; then
    echo "Skipping invalid site entry at index $index (name/repo/workdir/domain required)." >&2
    continue
  fi

  if [[ "$runtime_mode" != "static" && "$runtime_mode" != "service" ]]; then
    echo "Skipping invalid site '$name': runtime.mode must be static or service." >&2
    continue
  fi

  if [[ "$runtime_mode" == "service" ]]; then
    if [[ -z "$runtime_command" || -z "$runtime_port" ]]; then
      echo "Skipping invalid site '$name': service mode requires runtime.command and runtime.port." >&2
      continue
    fi
  fi

  if [[ "$runtime_mode" == "static" && -z "$web_root" && -z "$build_output" ]]; then
    echo "Skipping invalid site '$name': static mode requires web_root or build_output." >&2
    continue
  fi

  if [[ -n "$ONLY_SITE" && "$ONLY_SITE" != "$name" ]]; then
    continue
  fi

  if [[ -z "$releases_dir" ]]; then
    releases_dir="$workdir/releases"
  fi

  if [[ -z "$current_symlink" ]]; then
    current_symlink="$workdir/current"
  fi

  echo "==> Syncing site: $name"
  mkdir -p "$workdir" "$releases_dir"

  if [[ -z "$runtime_working_dir" ]]; then
    runtime_working_dir="."
  fi

  if [[ -z "$runtime_user" ]]; then
    runtime_user="$(id -un)"
  fi

  release_ts=$(date +%Y%m%d-%H%M%S)
  release_dir="$releases_dir/${release_ts}"
  while [[ -e "$release_dir" ]]; do
    release_ts="${release_ts}-$(printf '%04d' $((RANDOM % 10000)))"
    release_dir="$releases_dir/${release_ts}"
  done

  echo "  - Cloning $repo into $release_dir"
  run_git "$git_ssh_command" clone "$repo" "$release_dir"

  pushd "$release_dir" >/dev/null
  echo "  - Fetching latest refs"
  run_git "$git_ssh_command" fetch --prune origin
  run_git "$git_ssh_command" checkout "$branch"
  run_git "$git_ssh_command" reset --hard "origin/$branch"

  run_optional "$pre_deploy_cmd" "pre_deploy_cmd"
  run_optional "$build_cmd" "build_cmd"

  if [[ -n "$deploy_script" ]]; then
    if [[ ! -f "$deploy_script" ]]; then
      echo "  - deploy_script not found: $deploy_script" >&2
      popd >/dev/null
      exit 1
    fi
    echo "  - Running deploy_script: $deploy_script"
    chmod +x "$deploy_script"
    "$release_dir/$deploy_script"
  fi
  popd >/dev/null

  if [[ "$runtime_mode" == "service" ]]; then
    if [[ "$runtime_working_dir" == "." ]]; then
      runtime_working_dir="$release_dir"
    elif [[ "$runtime_working_dir" != /* ]]; then
      runtime_working_dir="$release_dir/$runtime_working_dir"
    fi

    ensure_runtime_service "$name" "$runtime_mode" "$runtime_command" "$runtime_working_dir" "$runtime_user" "$runtime_env_file"
    if ! wait_for_service_health "$runtime_mode" "$runtime_port" "$runtime_health_endpoint"; then
      echo "  - Deploy aborted before traffic switch due to failing health check." >&2
      rm -rf "$release_dir"
      exit 1
    fi
  fi

  static_root_candidate="$build_output"
  if [[ -z "$static_root_candidate" || "$static_root_candidate" == "null" ]]; then
    static_root_candidate="$web_root"
  fi

  if ! apply_nginx_site_config "$name" "$domain" "$runtime_mode" "$release_dir" "$static_root_candidate" "$runtime_port" "$nginx_www_redirect" "$nginx_tls_hostnames_csv"; then
    echo "  - Deploy aborted for '$name' because Nginx config validation failed." >&2
    rm -rf "$release_dir"
    exit 1
  fi

  previous_target=$(capture_current_target "$current_symlink")
  previous_pointer_file="${releases_dir}/.previous_release"

  if [[ -n "$previous_target" ]]; then
    printf '%s\n' "$previous_target" >"$previous_pointer_file"
    echo "  - Captured previous release: $previous_target"
  fi

  atomic_switch_symlink "$current_symlink" "$release_dir"
  echo "  - Updated current symlink: $current_symlink -> $release_dir"

  run_optional "$post_deploy_cmd" "post_deploy_cmd"
  run_unlighthouse "$name" "$site_url" "$unlighthouse_cmd" "$unlighthouse_server_url" "$unlighthouse_server_token"

  current_target=$(capture_current_target "$current_symlink")
  previous_target_for_cleanup=""
  if [[ -f "$previous_pointer_file" ]]; then
    previous_target_for_cleanup=$(<"$previous_pointer_file")
  fi
  cleanup_old_releases "$releases_dir" "$keep_releases" "$current_target" "$previous_target_for_cleanup"

  echo "  - Completed $name"
  echo

done

echo "Done syncing configured GitHub sites."
