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
    "deploy_script": "scripts/deploy.sh",
    "build_cmd": "bun install --frozen-lockfile && bun run build",
    "post_deploy_cmd": "sudo systemctl reload nginx",
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

run_unlighthouse() {
  local site_name="$1"
  local site_url="$2"
  local unlighthouse_cmd="$3"

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
  npx --yes unlighthouse-ci@latest --site "$site_url" --output-path "$report_dir"
}

for index in $(seq 0 $((SITE_COUNT - 1))); do
  site_json=$(jq ".[$index]" "$CONFIG_PATH")
  name=$(jq -r '.name // empty' <<<"$site_json")
  repo=$(jq -r '.repo // empty' <<<"$site_json")
  branch=$(jq -r '.branch // "main"' <<<"$site_json")
  workdir=$(jq -r '.workdir // empty' <<<"$site_json")
  site_url=$(jq -r '.site_url // empty' <<<"$site_json")
  deploy_script=$(jq -r '.deploy_script // empty' <<<"$site_json")
  build_cmd=$(jq -r '.build_cmd // empty' <<<"$site_json")
  post_deploy_cmd=$(jq -r '.post_deploy_cmd // empty' <<<"$site_json")
  unlighthouse_cmd=$(jq -r '.unlighthouse_cmd // empty' <<<"$site_json")

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
    git clone "$repo" "$workdir"
  fi

  pushd "$workdir" >/dev/null
  echo "  - Fetching latest refs"
  git fetch --prune origin
  git checkout "$branch"
  git reset --hard "origin/$branch"

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
  run_unlighthouse "$name" "$site_url" "$unlighthouse_cmd"

  echo "  - Completed $name"
  popd >/dev/null
  echo

done

echo "Done syncing configured GitHub sites."
