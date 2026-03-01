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

  if [[ -z "$releases_dir" ]]; then
    releases_dir="$workdir/releases"
  fi

  if [[ -z "$current_symlink" ]]; then
    current_symlink="$workdir/current"
  fi

  echo "==> Syncing site: $name"
  mkdir -p "$workdir" "$releases_dir"

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
