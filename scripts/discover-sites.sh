#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/discover-sites.sh [--base-glob '/srv/apps/*'] [--output deploy/sites.json] [--dry-run]

Description:
  Scans each directory that matches --base-glob for a server.conf JSON file,
  validates required keys, normalizes entries, and writes deploy/sites.json.

Stable server.conf format (JSON):
{
  "name": "marketing-site",                       // required, unique globally
  "repo": "git@github.com:your-org/marketing.git", // required
  "branch": "main",                              // required
  "domain": "example.com",                       // required, unique globally
  "workdir": "/srv/github-sites/marketing-site", // optional (default: /srv/github-sites/<name>)
  "web_root": "public",                          // required when build_output omitted
  "build_output": "dist",                        // required when web_root omitted
  "deploy_hooks": {                                // required object
    "pre_deploy": "echo pre",                    // optional string
    "build": "npm ci && npm run build",          // optional string
    "post_deploy": "sudo systemctl reload nginx" // optional string
  },
  "runtime": {                                     // required object
    "mode": "service",                           // required: static | service
    "command": "npm run start",                  // required when mode=service
    "working_dir": ".",                          // optional, default: release dir
    "user": "www-data",                          // optional, default: current user
    "env_file": "/etc/default/marketing-site",   // optional
    "port": 3000,                                  // required when mode=service
    "health_endpoint": "/healthz"                // optional, default: /health
  },
  "service": {                                     // required object
    "name": "marketing-site.service",
    "reload_cmd": "sudo systemctl reload nginx"
  }
}
USAGE
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

BASE_GLOB="/srv/apps/*"
OUTPUT_PATH="deploy/sites.json"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-glob)
      BASE_GLOB="${2:-}"
      shift 2
      ;;
    --output)
      OUTPUT_PATH="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
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

require_cmd jq

shopt -s nullglob
matches=( $BASE_GLOB )

if [[ ${#matches[@]} -eq 0 ]]; then
  echo "No directories matched base glob: $BASE_GLOB" >&2
  exit 1
fi

normalized='[]'
declare -A seen_names=()
declare -A seen_domains=()

for repo_dir in "${matches[@]}"; do
  [[ -d "$repo_dir" ]] || continue

  conf_path="$repo_dir/server.conf"
  if [[ ! -f "$conf_path" ]]; then
    continue
  fi

  if ! jq empty "$conf_path" >/dev/null 2>&1; then
    echo "Invalid JSON in $conf_path" >&2
    exit 1
  fi

  if [[ "$(jq -r 'type' "$conf_path")" != "object" ]]; then
    echo "Invalid config in $conf_path: root must be a JSON object" >&2
    exit 1
  fi

  name="$(jq -r '.name // empty' "$conf_path")"
  repo="$(jq -r '.repo // empty' "$conf_path")"
  branch="$(jq -r '.branch // empty' "$conf_path")"
  domain="$(jq -r '.domain // empty' "$conf_path")"
  workdir="$(jq -r '.workdir // empty' "$conf_path")"
  web_root="$(jq -r '.web_root // empty' "$conf_path")"
  build_output="$(jq -r '.build_output // empty' "$conf_path")"

  [[ -n "$name" ]] || { echo "Validation error in $conf_path: missing required key 'name'" >&2; exit 1; }
  [[ -n "$repo" ]] || { echo "Validation error in $conf_path: missing required key 'repo'" >&2; exit 1; }
  [[ -n "$branch" ]] || { echo "Validation error in $conf_path: missing required key 'branch'" >&2; exit 1; }
  [[ -n "$domain" ]] || { echo "Validation error in $conf_path: missing required key 'domain'" >&2; exit 1; }

  if [[ -z "$web_root" && -z "$build_output" ]]; then
    echo "Validation error in $conf_path: one of 'web_root' or 'build_output' must be set" >&2
    exit 1
  fi

  if [[ "$(jq -r '.deploy_hooks // empty | type' "$conf_path")" != "object" ]]; then
    echo "Validation error in $conf_path: missing or invalid required object 'deploy_hooks'" >&2
    exit 1
  fi

  if [[ "$(jq -r '.runtime // empty | type' "$conf_path")" != "object" ]]; then
    echo "Validation error in $conf_path: missing or invalid required object 'runtime'" >&2
    exit 1
  fi

  if [[ "$(jq -r '.service // empty | type' "$conf_path")" != "object" ]]; then
    echo "Validation error in $conf_path: missing or invalid required object 'service'" >&2
    exit 1
  fi

  runtime_mode="$(jq -r '.runtime.mode // empty' "$conf_path")"
  if [[ -z "$runtime_mode" ]]; then
    runtime_mode="$(jq -r '.runtime.type // empty' "$conf_path")"
  fi
  service_name="$(jq -r '.service.name // empty' "$conf_path")"
  [[ -n "$runtime_mode" ]] || { echo "Validation error in $conf_path: missing required key 'runtime.mode'" >&2; exit 1; }
  [[ -n "$service_name" ]] || { echo "Validation error in $conf_path: missing required key 'service.name'" >&2; exit 1; }

  if [[ "$runtime_mode" != "static" && "$runtime_mode" != "service" ]]; then
    echo "Validation error in $conf_path: runtime.mode must be either 'static' or 'service'" >&2
    exit 1
  fi

  if [[ "$runtime_mode" == "service" ]]; then
    runtime_command="$(jq -r '.runtime.command // empty' "$conf_path")"
    runtime_port="$(jq -r '.runtime.port // empty' "$conf_path")"

    [[ -n "$runtime_command" ]] || { echo "Validation error in $conf_path: missing required key 'runtime.command' for service mode" >&2; exit 1; }
    [[ -n "$runtime_port" ]] || { echo "Validation error in $conf_path: missing required key 'runtime.port' for service mode" >&2; exit 1; }
    [[ "$runtime_port" =~ ^[0-9]+$ ]] || { echo "Validation error in $conf_path: runtime.port must be numeric" >&2; exit 1; }
  fi

  if [[ -n "${seen_names[$name]:-}" ]]; then
    echo "Validation error: duplicate site name '$name' in $conf_path and ${seen_names[$name]}" >&2
    exit 1
  fi
  if [[ -n "${seen_domains[$domain]:-}" ]]; then
    echo "Validation error: duplicate site domain '$domain' in $conf_path and ${seen_domains[$domain]}" >&2
    exit 1
  fi
  seen_names[$name]="$conf_path"
  seen_domains[$domain]="$conf_path"

  if [[ -z "$workdir" ]]; then
    workdir="/srv/github-sites/$name"
  fi

  site_url="https://$domain"

  normalized_entry=$(jq -n \
    --arg name "$name" \
    --arg repo "$repo" \
    --arg branch "$branch" \
    --arg domain "$domain" \
    --arg site_url "$site_url" \
    --arg workdir "$workdir" \
    --arg releases_dir "$workdir/releases" \
    --arg current_symlink "$workdir/current" \
    --arg web_root "$web_root" \
    --arg build_output "$build_output" \
    --arg repo_dir "$repo_dir" \
    --argjson deploy_hooks "$(jq -c '.deploy_hooks' "$conf_path")" \
    --argjson runtime "$(jq -c --arg mode "$runtime_mode" '.runtime + {mode: $mode}' "$conf_path")" \
    --argjson service "$(jq -c '.service' "$conf_path")" \
    '{
      name: $name,
      repo: $repo,
      branch: $branch,
      domain: $domain,
      site_url: $site_url,
      workdir: $workdir,
      releases_dir: $releases_dir,
      current_symlink: $current_symlink,
      web_root: (if $web_root == "" then null else $web_root end),
      build_output: (if $build_output == "" then null else $build_output end),
      pre_deploy_cmd: ($deploy_hooks.pre_deploy // null),
      build_cmd: ($deploy_hooks.build // null),
      post_deploy_cmd: ($deploy_hooks.post_deploy // ($service.reload_cmd // null)),
      runtime: $runtime,
      service: $service,
      source_server_conf: ($repo_dir + "/server.conf")
    }')

  normalized=$(jq --argjson entry "$normalized_entry" '. + [$entry]' <<<"$normalized")
  echo "Discovered site '$name' from $conf_path"
done

count=$(jq 'length' <<<"$normalized")
if [[ "$count" -eq 0 ]]; then
  echo "No server.conf files found under $BASE_GLOB" >&2
  exit 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  jq '.' <<<"$normalized"
  exit 0
fi

mkdir -p "$(dirname "$OUTPUT_PATH")"
jq '.' <<<"$normalized" > "$OUTPUT_PATH"
echo "Wrote $count site entries to $OUTPUT_PATH"
