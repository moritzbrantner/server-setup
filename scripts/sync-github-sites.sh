#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/sync-github-sites.sh [--config deploy/sites.json] [--site SITE_NAME]
  ./scripts/sync-github-sites.sh [--discover-base '/srv/apps/*'] [--config deploy/sites.json] [--site SITE_NAME]
  ./scripts/sync-github-sites.sh [--config deploy/sites.json] --rollback SITE_NAME
  ./scripts/sync-github-sites.sh [--config deploy/sites.json] [--site SITE_NAME] --dry-run
  ./scripts/sync-github-sites.sh [--config deploy/sites.json] [--site SITE_NAME] --preflight-only
  ./scripts/sync-github-sites.sh [--config deploy/sites.json] [--site SITE_NAME] --json-status

Description:
  - Pulls website repos from Git.
  - Checks out configured branch.
  - Deploys into timestamped releases and atomically switches the current symlink.
  - Runs preflight validation before any host mutation.
  - Persists per-site deployment state and structured logs.
USAGE
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"

CONFIG_PATH="deploy/sites.json"
DISCOVER_BASE=""
ONLY_SITE=""
ROLLBACK_SITE=""
DRY_RUN=0
PREFLIGHT_ONLY=0
JSON_STATUS=0

STATE_DIR="${STATE_DIR:-/var/lib/server-setup/state}"
LOCK_DIR="${LOCK_DIR:-/var/lock/server-setup}"
LOG_DIR="${LOG_DIR:-/var/log/server-setup}"
LOG_RETENTION_DAYS="${LOG_RETENTION_DAYS:-14}"
NGINX_SITE_AVAILABLE_DIR="${NGINX_SITE_AVAILABLE_DIR:-/etc/nginx/sites-available}"
NGINX_SITE_ENABLED_DIR="${NGINX_SITE_ENABLED_DIR:-/etc/nginx/sites-enabled}"
NGINX_DEFAULT_SITE_LINK="${NGINX_DEFAULT_SITE_LINK:-/etc/nginx/sites-enabled/default}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
LOG_FILE=""

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
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --preflight-only)
      PREFLIGHT_ONLY=1
      shift
      ;;
    --json-status)
      JSON_STATUS=1
      shift
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
require_cmd python3

prune_old_logs() {
  if [[ -d "$LOG_DIR" ]]; then
    find "$LOG_DIR" -type f -name '*.log' -mtime +"$LOG_RETENTION_DAYS" -delete 2>/dev/null || true
  fi
}

init_runtime_dirs() {
  mkdir -p "$STATE_DIR" "$LOCK_DIR" "$LOG_DIR"
  prune_old_logs
  LOG_FILE="$LOG_DIR/deploy-$(date +%Y%m%d).log"
}

log_event() {
  local site="$1"
  local action="$2"
  local result="$3"
  local message="$4"
  local level="${5:-info}"
  local line
  line=$(jq -nc \
    --arg timestamp "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    --arg site "$site" \
    --arg action "$action" \
    --arg result "$result" \
    --arg level "$level" \
    --arg message "$message" \
    '{
      timestamp: $timestamp,
      site: (if $site == "" then null else $site end),
      action: $action,
      result: $result,
      level: $level,
      message: $message
    }')
  printf '%s\n' "$line"
  printf '%s\n' "$line" >>"$LOG_FILE"
}

state_file() {
  printf '%s/%s.json\n' "$STATE_DIR" "$1"
}

state_asset_path() {
  local site_name="$1"
  local asset_name="$2"
  printf '%s/%s-%s\n' "$STATE_DIR" "$site_name" "$asset_name"
}

read_state_json() {
  local site_name="$1"
  local path
  path="$(state_file "$site_name")"
  if [[ -f "$path" ]]; then
    cat "$path"
    return
  fi

  jq -nc --arg site "$site_name" '{
    site: $site,
    last_deploy_status: "never-run"
  }'
}

write_state_json() {
  local site_name="$1"
  local body="$2"
  local path
  local tmp
  path="$(state_file "$site_name")"
  tmp="$(mktemp)"
  printf '%s\n' "$body" >"$tmp"
  mv "$tmp" "$path"
}

state_mark_attempt() {
  local site_name="$1"
  local release_dir="$2"
  local body
  body=$(jq \
    --arg attempted "$release_dir" \
    --arg timestamp "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    '.last_attempted_release = $attempted
     | .last_deploy_timestamp = $timestamp
     | .last_deploy_status = "running"
     | .last_failure_reason = null' <<<"$(read_state_json "$site_name")")
  write_state_json "$site_name" "$body"
}

state_mark_health() {
  local site_name="$1"
  local status="$2"
  local url="$3"
  local message="$4"
  local body
  body=$(jq \
    --arg status "$status" \
    --arg url "$url" \
    --arg message "$message" \
    --arg checked_at "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    '.last_health_check = {
      status: $status,
      url: $url,
      message: $message,
      checked_at: $checked_at
    }' <<<"$(read_state_json "$site_name")")
  write_state_json "$site_name" "$body"
}

state_mark_failure() {
  local site_name="$1"
  local reason="$2"
  local body
  body=$(jq \
    --arg reason "$reason" \
    --arg timestamp "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    '.last_deploy_status = "failed"
     | .last_failure_reason = $reason
     | .last_failure_at = $timestamp' <<<"$(read_state_json "$site_name")")
  write_state_json "$site_name" "$body"
}

state_mark_success() {
  local site_name="$1"
  local release_dir="$2"
  local body
  body=$(jq \
    --arg release "$release_dir" \
    --arg timestamp "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    '.previous_successful_release = .last_successful_release
     | .last_successful_release = $release
     | .current_release = $release
     | .last_deploy_status = "success"
     | .last_failure_reason = null
     | .last_success_at = $timestamp' <<<"$(read_state_json "$site_name")")
  write_state_json "$site_name" "$body"
}

state_mark_rollback() {
  local site_name="$1"
  local release_dir="$2"
  local body
  body=$(jq \
    --arg release "$release_dir" \
    --arg timestamp "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    '.current_release = $release
     | .last_rollback_timestamp = $timestamp
     | .last_deploy_status = "rolled-back"' <<<"$(read_state_json "$site_name")")
  write_state_json "$site_name" "$body"
}

run_optional() {
  local cmd="$1"
  local where="$2"
  local site_name="$3"

  if [[ -n "$cmd" && "$cmd" != "null" ]]; then
    log_event "$site_name" "$where" "running" "$cmd"
    bash -lc "$cmd"
    log_event "$site_name" "$where" "success" "$cmd"
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
      return 1
    fi

    local token="\${${env_var}}"
    resolved="${resolved//${token}/$env_value}"
  done

  echo "$resolved"
}

resolve_into() {
  local __var_name="$1"
  local site_name="$2"
  local field_name="$3"
  local raw_value="$4"
  local resolved

  resolved="$(resolve_config_value "$site_name" "$field_name" "$raw_value")" || return 1
  printf -v "$__var_name" '%s' "$resolved"
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

  if [[ "${SKIP_UNLIGHTHOUSE:-0}" == "1" ]]; then
    log_event "$site_name" "unlighthouse" "skipped" "SKIP_UNLIGHTHOUSE=1"
    return
  fi

  if [[ -n "$unlighthouse_cmd" && "$unlighthouse_cmd" != "null" ]]; then
    log_event "$site_name" "unlighthouse" "running" "$unlighthouse_cmd"
    bash -lc "$unlighthouse_cmd"
    log_event "$site_name" "unlighthouse" "success" "$unlighthouse_cmd"
    return
  fi

  if [[ -z "$site_url" || "$site_url" == "null" ]]; then
    log_event "$site_name" "unlighthouse" "skipped" "site_url not configured"
    return
  fi

  require_cmd npx

  local ts report_dir
  ts=$(date +%Y%m%d-%H%M%S)
  report_dir="/var/log/unlighthouse/${site_name}/${ts}"
  mkdir -p "$report_dir"

  local cmd=(npx --yes unlighthouse-ci@latest --site "$site_url" --output-path "$report_dir")
  if [[ -n "$unlighthouse_server_url" && "$unlighthouse_server_url" != "null" ]]; then
    cmd+=(--server "$unlighthouse_server_url" --build-name "$site_name")
    if [[ -n "$unlighthouse_server_token" && "$unlighthouse_server_token" != "null" ]]; then
      cmd+=(--auth "$unlighthouse_server_token")
    fi
  fi

  log_event "$site_name" "unlighthouse" "running" "site=${site_url}"
  "${cmd[@]}"
  log_event "$site_name" "unlighthouse" "success" "site=${site_url}"
}

quote_for_bash_literal() {
  local value="$1"
  printf "'%s'" "$(printf '%s' "$value" | sed "s/'/'\\\\''/g")"
}

render_systemd_unit() {
  local name="$1"
  local command="$2"
  local working_dir="$3"
  local run_user="$4"
  local env_file="$5"
  local shell_payload

  shell_payload=$(quote_for_bash_literal "set -euo pipefail; export BUN_INSTALL=\"\${BUN_INSTALL:-\$HOME/.bun}\"; export PATH=\"\$BUN_INSTALL/bin:\$PATH\"; ${command}")

  cat <<UNIT
[Unit]
Description=Runtime service for ${name}
After=network.target

[Service]
Type=simple
WorkingDirectory=${working_dir}
ExecStart=/usr/bin/env bash -lc ${shell_payload}
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

  local unit_path="${SYSTEMD_UNIT_DIR}/app-${site_name}.service"
  local unit_content
  unit_content=$(render_systemd_unit "$site_name" "$runtime_command" "$runtime_working_dir" "$runtime_user" "$runtime_env_file")

  if write_if_changed "$unit_path" "$unit_content"; then
    log_event "$site_name" "systemd-unit" "updated" "$unit_path"
    systemctl daemon-reload
    systemctl enable "app-${site_name}.service"
  else
    log_event "$site_name" "systemd-unit" "unchanged" "$unit_path"
  fi

  cp "$unit_path" "$(state_asset_path "$site_name" "last-good-unit.service")"
  systemctl restart "app-${site_name}.service"
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

  local site_conf="${NGINX_SITE_AVAILABLE_DIR}/${site_name}.conf"
  local site_link="${NGINX_SITE_ENABLED_DIR}/${site_name}.conf"
  local backup_conf="${site_conf}.last-good"
  local static_root=""

  if [[ "$runtime_mode" == "static" ]]; then
    if [[ -z "$static_relative_root" ]]; then
      echo "Missing static root for site '$site_name'." >&2
      return 1
    fi

    if [[ "$static_relative_root" == "/" ]]; then
      static_root="$release_dir"
    else
      static_root="$release_dir/$static_relative_root"
    fi

    if [[ ! -d "$static_root" ]]; then
      echo "Static root does not exist for site '$site_name': $static_root" >&2
      return 1
    fi
  fi

  local conf_content
  conf_content=$(render_nginx_site_config "$site_name" "$domain" "$runtime_mode" "$static_root" "$runtime_port" "$www_redirect" "$tls_hostnames_csv")

  mkdir -p "$NGINX_SITE_AVAILABLE_DIR" "$NGINX_SITE_ENABLED_DIR"

  if [[ -f "$site_conf" ]]; then
    cp "$site_conf" "$backup_conf"
  fi

  write_if_changed "$site_conf" "$conf_content" || true
  ln -sfn "$site_conf" "$site_link"
  if [[ -L "$NGINX_DEFAULT_SITE_LINK" ]]; then
    rm -f "$NGINX_DEFAULT_SITE_LINK"
  fi

  if nginx -t; then
    systemctl reload nginx
    cp "$site_conf" "$backup_conf"
    cp "$site_conf" "$(state_asset_path "$site_name" "last-good-nginx.conf")"
    log_event "$site_name" "nginx-config" "success" "$site_conf"
    return 0
  fi

  log_event "$site_name" "nginx-config" "failed" "$site_conf" "error"
  if [[ -f "$backup_conf" ]]; then
    cp "$backup_conf" "$site_conf"
  else
    rm -f "$site_conf" "$site_link"
  fi

  nginx -t >/dev/null 2>&1 || true
  return 1
}

wait_for_service_health() {
  local site_name="$1"
  local runtime_mode="$2"
  local port="$3"
  local endpoint="$4"
  local attempts="$5"
  local delay="$6"

  if [[ "$runtime_mode" != "service" ]]; then
    state_mark_health "$site_name" "not-applicable" "" "static deployment"
    return 0
  fi

  local url="http://127.0.0.1:${port}${endpoint}"
  log_event "$site_name" "health-check" "running" "$url"
  for _ in $(seq 1 "$attempts"); do
    if curl --silent --show-error --fail --max-time 2 "$url" >/dev/null; then
      state_mark_health "$site_name" "passing" "$url" "health check passed"
      log_event "$site_name" "health-check" "success" "$url"
      return 0
    fi
    sleep "$delay"
  done

  state_mark_health "$site_name" "failing" "$url" "health check failed"
  log_event "$site_name" "health-check" "failed" "$url" "error"
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

  if [[ -L "$symlink_path" || -d "$symlink_path" ]]; then
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
    rm -rf "$candidate"
    removed=$((removed + 1))
  done
}

path_is_writable_or_creatable() {
  local target="$1"
  local probe="$target"

  while [[ ! -e "$probe" && "$probe" != "/" ]]; do
    probe="$(dirname "$probe")"
  done

  [[ -w "$probe" ]]
}

preflight_site() {
  local site_name="$1"
  local runtime_mode="$2"
  local runtime_command="$3"
  local workdir="$4"
  local releases_dir="$5"
  local current_symlink="$6"
  local runtime_port="$7"
  local runtime_working_dir="$8"

  if ! path_is_writable_or_creatable "$workdir"; then
    echo "Preflight failed for '$site_name': workdir is not writable/creatable: $workdir" >&2
    return 1
  fi
  if ! path_is_writable_or_creatable "$releases_dir"; then
    echo "Preflight failed for '$site_name': releases_dir is not writable/creatable: $releases_dir" >&2
    return 1
  fi
  if ! path_is_writable_or_creatable "$current_symlink"; then
    echo "Preflight failed for '$site_name': current_symlink is not writable/creatable: $current_symlink" >&2
    return 1
  fi
  if ! path_is_writable_or_creatable "${NGINX_SITE_AVAILABLE_DIR}/${site_name}.conf"; then
    echo "Preflight failed for '$site_name': nginx site directory is not writable" >&2
    return 1
  fi
  if [[ "$runtime_mode" == "service" ]]; then
    if [[ -z "$runtime_working_dir" ]]; then
      echo "Preflight failed for '$site_name': runtime.working_dir cannot be empty" >&2
      return 1
    fi
    if [[ "$runtime_working_dir" == /* ]]; then
      echo "Preflight failed for '$site_name': runtime.working_dir must be relative to the deployed release, got '$runtime_working_dir'" >&2
      return 1
    fi
    if ! path_is_writable_or_creatable "${SYSTEMD_UNIT_DIR}/app-${site_name}.service"; then
      echo "Preflight failed for '$site_name': systemd unit directory is not writable" >&2
      return 1
    fi
  fi
  if [[ "$runtime_mode" == "service" ]]; then
    local runtime_bin
    runtime_bin="$(awk '{
      for (i = 1; i <= NF; i++) {
        if ($i !~ /^[A-Za-z_][A-Za-z0-9_]*=/) {
          print $i
          exit
        }
      }
    }' <<<"$runtime_command")"
    if [[ -z "$runtime_bin" ]] || ! command -v "$runtime_bin" >/dev/null 2>&1; then
      echo "Preflight failed for '$site_name': missing runtime binary '$runtime_bin'" >&2
      return 1
    fi
    if [[ -z "$runtime_port" ]]; then
      echo "Preflight failed for '$site_name': runtime.port is required for service mode" >&2
      return 1
    fi
  fi
  if ! command -v nginx >/dev/null 2>&1; then
    echo "Preflight failed for '$site_name': missing nginx command" >&2
    return 1
  fi
  if ! nginx -t >/dev/null 2>&1; then
    echo "Preflight failed for '$site_name': nginx -t failed" >&2
    return 1
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "Preflight failed for '$site_name': missing systemctl command" >&2
    return 1
  fi

  log_event "$site_name" "preflight" "success" "validated deploy prerequisites"
  return 0
}

restore_last_good_files() {
  local site_name="$1"
  local nginx_backup
  local unit_backup
  local site_conf="${NGINX_SITE_AVAILABLE_DIR}/${site_name}.conf"
  local site_link="${NGINX_SITE_ENABLED_DIR}/${site_name}.conf"

  nginx_backup="$(state_asset_path "$site_name" "last-good-nginx.conf")"
  unit_backup="$(state_asset_path "$site_name" "last-good-unit.service")"

  if [[ -f "$nginx_backup" ]]; then
    mkdir -p "$NGINX_SITE_AVAILABLE_DIR" "$NGINX_SITE_ENABLED_DIR"
    cp "$nginx_backup" "$site_conf"
    ln -sfn "$site_conf" "$site_link"
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  fi

  if [[ -f "$unit_backup" ]]; then
    mkdir -p "$SYSTEMD_UNIT_DIR"
    cp "$unit_backup" "${SYSTEMD_UNIT_DIR}/app-${site_name}.service"
    systemctl daemon-reload || true
    systemctl restart "app-${site_name}.service" || true
  fi
}

rollback_site() {
  local site_name="$1"
  local state_json
  local current_symlink
  local previous_target

  state_json="$(read_state_json "$site_name")"
  current_symlink=$(jq -r --arg site "$site_name" '.[] | select(.name == $site) | .current_symlink // empty' "$CONFIG_PATH")

  previous_target=$(jq -r '.previous_successful_release // empty' <<<"$state_json")
  if [[ -z "$previous_target" ]]; then
    echo "No previous successful release recorded for '$site_name'." >&2
    exit 1
  fi
  if [[ ! -d "$previous_target" ]]; then
    echo "Rollback target is invalid for '$site_name': $previous_target" >&2
    exit 1
  fi

  restore_last_good_files "$site_name"
  atomic_switch_symlink "$current_symlink" "$previous_target"
  state_mark_rollback "$site_name" "$previous_target"
  log_event "$site_name" "rollback" "success" "$previous_target"
}

emit_status_json() {
  local output='[]'
  local site_json
  local site_name
  local state_json
  local index
  local site_count

  site_count=$(jq 'length' "$CONFIG_PATH")
  for index in $(seq 0 $((site_count - 1))); do
    site_json=$(jq ".[$index]" "$CONFIG_PATH")
    site_name=$(jq -r '.name // empty' <<<"$site_json")
    if [[ -n "$ONLY_SITE" && "$ONLY_SITE" != "$site_name" ]]; then
      continue
    fi
    state_json="$(read_state_json "$site_name")"
    output=$(jq \
      --argjson site "$site_json" \
      --argjson state "$state_json" \
      '. + [{
        name: $site.name,
        domain: ($site.domain // null),
        site_url: ($site.site_url // null),
        runtime: ($site.runtime // {}),
        deploy: $state
      }]' <<<"$output")
  done
  printf '%s\n' "$output"
}

load_config() {
  if [[ -n "$DISCOVER_BASE" ]]; then
    "$SCRIPT_DIR/discover-sites.sh" --base-glob "$DISCOVER_BASE" --output "$CONFIG_PATH"
  fi

  if [[ ! -f "$CONFIG_PATH" ]]; then
    echo "Config file not found: $CONFIG_PATH" >&2
    exit 1
  fi
}

resolve_site_fields() {
  site_json="$1"
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
  runtime_working_dir=$(jq -r '.runtime.working_dir // "."' <<<"$site_json")
  runtime_user=$(jq -r '.runtime.user // empty' <<<"$site_json")
  runtime_env_file=$(jq -r '.runtime.env_file // empty' <<<"$site_json")
  runtime_port=$(jq -r '.runtime.port // empty' <<<"$site_json")
  runtime_health_endpoint=$(jq -r '.runtime.health_endpoint // "/health"' <<<"$site_json")
  health_retries=$(jq -r '.runtime.health_retries // 20' <<<"$site_json")
  health_interval_seconds=$(jq -r '.runtime.health_interval_seconds // 2' <<<"$site_json")
  domain=$(jq -r '.domain // empty' <<<"$site_json")
  web_root=$(jq -r '.web_root // empty' <<<"$site_json")
  build_output=$(jq -r '.build_output // empty' <<<"$site_json")
  nginx_www_redirect=$(jq -r '.nginx.www_redirect // false' <<<"$site_json")
  nginx_tls_hostnames_csv=$(jq -r '(.nginx.tls_hostnames // []) | join(" ")' <<<"$site_json")

  resolve_into name "site" "name" "$name" || return 1
  resolve_into repo "$name" "repo" "$repo" || return 1
  resolve_into branch "$name" "branch" "$branch" || return 1
  resolve_into workdir "$name" "workdir" "$workdir" || return 1
  resolve_into releases_dir "$name" "releases_dir" "$releases_dir" || return 1
  resolve_into current_symlink "$name" "current_symlink" "$current_symlink" || return 1
  resolve_into keep_releases "$name" "keep_releases" "$keep_releases" || return 1
  resolve_into site_url "$name" "site_url" "$site_url" || return 1
  resolve_into git_ssh_command "$name" "git_ssh_command" "$git_ssh_command" || return 1
  resolve_into deploy_script "$name" "deploy_script" "$deploy_script" || return 1
  resolve_into pre_deploy_cmd "$name" "pre_deploy_cmd" "$pre_deploy_cmd" || return 1
  resolve_into build_cmd "$name" "build_cmd" "$build_cmd" || return 1
  resolve_into post_deploy_cmd "$name" "post_deploy_cmd" "$post_deploy_cmd" || return 1
  resolve_into unlighthouse_cmd "$name" "unlighthouse_cmd" "$unlighthouse_cmd" || return 1
  resolve_into unlighthouse_server_url "$name" "unlighthouse_server_url" "$unlighthouse_server_url" || return 1
  resolve_into unlighthouse_server_token "$name" "unlighthouse_server_token" "$unlighthouse_server_token" || return 1
  resolve_into runtime_mode "$name" "runtime.mode" "$runtime_mode" || return 1
  resolve_into runtime_command "$name" "runtime.command" "$runtime_command" || return 1
  resolve_into runtime_working_dir "$name" "runtime.working_dir" "$runtime_working_dir" || return 1
  resolve_into runtime_user "$name" "runtime.user" "$runtime_user" || return 1
  resolve_into runtime_env_file "$name" "runtime.env_file" "$runtime_env_file" || return 1
  resolve_into runtime_port "$name" "runtime.port" "$runtime_port" || return 1
  resolve_into runtime_health_endpoint "$name" "runtime.health_endpoint" "$runtime_health_endpoint" || return 1
  resolve_into health_retries "$name" "runtime.health_retries" "$health_retries" || return 1
  resolve_into health_interval_seconds "$name" "runtime.health_interval_seconds" "$health_interval_seconds" || return 1
  resolve_into domain "$name" "domain" "$domain" || return 1
  resolve_into web_root "$name" "web_root" "$web_root" || return 1
  resolve_into build_output "$name" "build_output" "$build_output" || return 1

  if [[ -z "$unlighthouse_server_url" ]]; then
    unlighthouse_server_url="${UNLIGHTHOUSE_SERVER_URL:-}"
  fi
  if [[ -z "$unlighthouse_server_token" ]]; then
    unlighthouse_server_token="${UNLIGHTHOUSE_SERVER_TOKEN:-}"
  fi
  if [[ -z "$releases_dir" ]]; then
    releases_dir="$workdir/releases"
  fi
  if [[ -z "$current_symlink" ]]; then
    current_symlink="$workdir/current"
  fi
  if [[ -z "$runtime_user" ]]; then
    runtime_user="$(id -un)"
  fi
}

deploy_site() {
  local site_json="$1"
  local previous_target=""
  local release_ts=""
  local release_dir=""
  local static_root_candidate=""
  local previous_successful=""

  resolve_site_fields "$site_json" || return 1

  if [[ -n "$ONLY_SITE" && "$ONLY_SITE" != "$name" ]]; then
    return 0
  fi

  preflight_site "$name" "$runtime_mode" "$runtime_command" "$workdir" "$releases_dir" "$current_symlink" "$runtime_port" "$runtime_working_dir" || return 1
  if [[ "$DRY_RUN" -eq 1 || "$PREFLIGHT_ONLY" -eq 1 ]]; then
    log_event "$name" "deploy" "dry-run" "preflight completed"
    return 0
  fi

  mkdir -p "$workdir" "$releases_dir"

  release_ts=$(date +%Y%m%d-%H%M%S)
  release_dir="$releases_dir/${release_ts}"
  while [[ -e "$release_dir" ]]; do
    release_ts="${release_ts}-$(printf '%04d' $((RANDOM % 10000)))"
    release_dir="$releases_dir/${release_ts}"
  done

  state_mark_attempt "$name" "$release_dir"
  log_event "$name" "deploy" "running" "release=${release_dir}"

  run_git "$git_ssh_command" clone "$repo" "$release_dir"
  pushd "$release_dir" >/dev/null
  run_git "$git_ssh_command" fetch --prune origin
  run_git "$git_ssh_command" checkout "$branch"
  run_git "$git_ssh_command" reset --hard "origin/$branch"
  run_optional "$pre_deploy_cmd" "pre-deploy" "$name"
  run_optional "$build_cmd" "build" "$name"
  if [[ -n "$deploy_script" && "$deploy_script" != "null" ]]; then
    if [[ ! -f "$deploy_script" ]]; then
      popd >/dev/null
      echo "deploy_script not found: $deploy_script" >&2
      return 1
    fi
    chmod +x "$deploy_script"
    log_event "$name" "deploy-script" "running" "$deploy_script"
    "$release_dir/$deploy_script"
    log_event "$name" "deploy-script" "success" "$deploy_script"
  fi
  popd >/dev/null

  if [[ "$runtime_mode" == "service" ]]; then
    if [[ "$runtime_working_dir" == "." ]]; then
      runtime_working_dir="$release_dir"
    elif [[ "$runtime_working_dir" != /* ]]; then
      runtime_working_dir="$release_dir/$runtime_working_dir"
    fi

    ensure_runtime_service "$name" "$runtime_mode" "$runtime_command" "$runtime_working_dir" "$runtime_user" "$runtime_env_file"
    if ! wait_for_service_health "$name" "$runtime_mode" "$runtime_port" "$runtime_health_endpoint" "$health_retries" "$health_interval_seconds"; then
      rm -rf "$release_dir"
      echo "Deploy aborted before traffic switch due to failing health check." >&2
      return 1
    fi
  fi

  static_root_candidate="$build_output"
  if [[ -z "$static_root_candidate" || "$static_root_candidate" == "null" ]]; then
    static_root_candidate="$web_root"
  fi

  if ! apply_nginx_site_config "$name" "$domain" "$runtime_mode" "$release_dir" "$static_root_candidate" "$runtime_port" "$nginx_www_redirect" "$nginx_tls_hostnames_csv"; then
    rm -rf "$release_dir"
    echo "Deploy aborted for '$name' because Nginx config validation failed." >&2
    return 1
  fi

  previous_target=$(capture_current_target "$current_symlink")
  previous_successful=$(jq -r '.last_successful_release // empty' <<<"$(read_state_json "$name")")
  atomic_switch_symlink "$current_symlink" "$release_dir"
  state_mark_success "$name" "$release_dir"
  if [[ -n "$current_symlink" ]]; then
    local body
    body=$(jq --arg current_symlink "$current_symlink" '.current_symlink = $current_symlink' <<<"$(read_state_json "$name")")
    write_state_json "$name" "$body"
  fi

  run_optional "$post_deploy_cmd" "post-deploy" "$name"
  run_unlighthouse "$name" "$site_url" "$unlighthouse_cmd" "$unlighthouse_server_url" "$unlighthouse_server_token"

  cleanup_old_releases "$releases_dir" "$keep_releases" "$release_dir" "$previous_successful"
  log_event "$name" "deploy" "success" "release=${release_dir}"
}

process_site_with_lock() {
  local site_name="$1"
  shift
  local lock_path="${LOCK_DIR}/${site_name}.lock"
  mkdir -p "$LOCK_DIR"
  exec {lock_fd}> "$lock_path"
  flock "$lock_fd"
  "$@"
  local status=$?
  flock -u "$lock_fd"
  eval "exec ${lock_fd}>&-"
  return "$status"
}

main() {
  local site_count
  local site_json
  local site_name
  local index
  local site_status

  init_runtime_dirs
  load_config

  if [[ "$JSON_STATUS" -eq 1 ]]; then
    emit_status_json
    exit 0
  fi

  site_count=$(jq 'length' "$CONFIG_PATH")
  if [[ "$site_count" -eq 0 ]]; then
    echo "No sites defined in $CONFIG_PATH"
    exit 0
  fi

  if [[ -n "$ROLLBACK_SITE" ]]; then
    rollback_site "$ROLLBACK_SITE"
    exit 0
  fi

  for index in $(seq 0 $((site_count - 1))); do
    site_json=$(jq ".[$index]" "$CONFIG_PATH")
    site_name=$(jq -r '.name // empty' <<<"$site_json")
    if [[ -z "$site_name" ]]; then
      continue
    fi
    process_site_with_lock "$site_name" deploy_site "$site_json"
    site_status=$?
    if [[ "$site_status" -ne 0 ]]; then
      state_mark_failure "$site_name" "deployment failed"
      restore_last_good_files "$site_name"
      log_event "$site_name" "deploy" "failed" "deployment failed" "error"
      exit 1
    fi
  done

  echo "Done syncing configured GitHub sites."
}

main
