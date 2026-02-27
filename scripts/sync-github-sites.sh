#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage:
  ./scripts/sync-github-sites.sh [--config deploy/sites.json] [--site SITE_NAME]

Description:
  - Pulls website repos from GitHub.
  - Checks out configured branch.
  - Runs optional build and deploy hooks.
  - Runs Unlighthouse metrics collection after each deployment.

Config format (JSON array):
[
  {
    "name": "marketing-site",
    "repo": "git@github.com:org/marketing-site.git",
    "branch": "main",
    "workdir": "/srv/github-sites/marketing-site",
    "site_url": "https://example.com",
    "git_ssh_command": "ssh -i ${MARKETING_DEPLOY_KEY_PATH} -o IdentitiesOnly=yes",
    "deploy_script": "scripts/deploy.sh",
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
ONLY_SITE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --site)
      ONLY_SITE="${2:-}"
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

require_cmd git
require_cmd jq

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

for index in $(seq 0 $((SITE_COUNT - 1))); do
  site_json=$(jq ".[$index]" "$CONFIG_PATH")
  name=$(jq -r '.name // empty' <<<"$site_json")
  repo=$(jq -r '.repo // empty' <<<"$site_json")
  branch=$(jq -r '.branch // "main"' <<<"$site_json")
  workdir=$(jq -r '.workdir // empty' <<<"$site_json")
  site_url=$(jq -r '.site_url // empty' <<<"$site_json")
  git_ssh_command=$(jq -r '.git_ssh_command // empty' <<<"$site_json")
  deploy_script=$(jq -r '.deploy_script // empty' <<<"$site_json")
  build_cmd=$(jq -r '.build_cmd // empty' <<<"$site_json")
  post_deploy_cmd=$(jq -r '.post_deploy_cmd // empty' <<<"$site_json")
  unlighthouse_cmd=$(jq -r '.unlighthouse_cmd // empty' <<<"$site_json")
  unlighthouse_server_url=$(jq -r '.unlighthouse_server_url // empty' <<<"$site_json")
  unlighthouse_server_token=$(jq -r '.unlighthouse_server_token // empty' <<<"$site_json")

  name=$(resolve_config_value "site-$index" "name" "$name")
  repo=$(resolve_config_value "$name" "repo" "$repo")
  branch=$(resolve_config_value "$name" "branch" "$branch")
  workdir=$(resolve_config_value "$name" "workdir" "$workdir")
  site_url=$(resolve_config_value "$name" "site_url" "$site_url")
  git_ssh_command=$(resolve_config_value "$name" "git_ssh_command" "$git_ssh_command")
  deploy_script=$(resolve_config_value "$name" "deploy_script" "$deploy_script")
  build_cmd=$(resolve_config_value "$name" "build_cmd" "$build_cmd")
  post_deploy_cmd=$(resolve_config_value "$name" "post_deploy_cmd" "$post_deploy_cmd")
  unlighthouse_cmd=$(resolve_config_value "$name" "unlighthouse_cmd" "$unlighthouse_cmd")
  unlighthouse_server_url=$(resolve_config_value "$name" "unlighthouse_server_url" "$unlighthouse_server_url")
  unlighthouse_server_token=$(resolve_config_value "$name" "unlighthouse_server_token" "$unlighthouse_server_token")

  if [[ -z "$unlighthouse_server_url" ]]; then
    unlighthouse_server_url="${UNLIGHTHOUSE_SERVER_URL:-}"
  fi

  if [[ -z "$unlighthouse_server_token" ]]; then
    unlighthouse_server_token="${UNLIGHTHOUSE_SERVER_TOKEN:-}"
  fi

  if [[ -z "$name" || -z "$repo" || -z "$workdir" ]]; then
    echo "Skipping invalid site entry at index $index (name/repo/workdir required)." >&2
    continue
  fi

  if [[ -n "$ONLY_SITE" && "$ONLY_SITE" != "$name" ]]; then
    continue
  fi

  echo "==> Syncing site: $name"
  mkdir -p "$workdir"

  if [[ ! -d "$workdir/.git" ]]; then
    echo "  - Cloning $repo into $workdir"
    rm -rf "$workdir"
    run_git "$git_ssh_command" clone "$repo" "$workdir"
  fi

  pushd "$workdir" >/dev/null
  echo "  - Fetching latest refs"
  run_git "$git_ssh_command" fetch --prune origin
  run_git "$git_ssh_command" checkout "$branch"
  run_git "$git_ssh_command" reset --hard "origin/$branch"

  run_optional "$build_cmd" "build_cmd"

  if [[ -n "$deploy_script" ]]; then
    if [[ ! -f "$deploy_script" ]]; then
      echo "  - deploy_script not found: $deploy_script" >&2
      popd >/dev/null
      exit 1
    fi
    echo "  - Running deploy_script: $deploy_script"
    chmod +x "$deploy_script"
    "$workdir/$deploy_script"
  fi

  run_optional "$post_deploy_cmd" "post_deploy_cmd"
  run_unlighthouse "$name" "$site_url" "$unlighthouse_cmd" "$unlighthouse_server_url" "$unlighthouse_server_token"

  echo "  - Completed $name"
  popd >/dev/null
  echo

done

echo "Done syncing configured GitHub sites."
