#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  sudo ./scripts/onboard-app.sh --repo-url <git-url> --dest <folder> --email <admin@example.com> [options]

Description:
  End-to-end onboarding for one app repository:
  1) Clone (or update) repo into --dest.
  2) Validate server.conf.
  3) Register/update app entry in deploy/sites.json.
  4) Deploy app (runtime + Nginx config generation handled by sync script).
  5) Acquire/update TLS cert with Certbot.
  6) Print post-run summary.

Options:
  --repo-url URL         Git repository URL to clone/update (required)
  --dest PATH            Destination folder for repository checkout (required)
  --email EMAIL          Email used for Let's Encrypt registration (required unless --skip-tls)
  --config PATH          Sites config path (default: deploy/sites.json)
  --skip-tls             Skip TLS acquisition/update step
  --branch NAME          Force branch checkout before validation/deploy
  -h, --help             Show help

Idempotency:
  - Re-running updates existing checkout/config entry for the same app name.
  - Existing entry is replaced in-place (no duplicate name/domain resources).
  - Deploy is release-based and safe to run repeatedly.
USAGE
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root (use sudo)." >&2
    exit 1
  fi
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

REPO_URL=""
DEST=""
EMAIL=""
CONFIG_PATH="deploy/sites.json"
SKIP_TLS=0
FORCE_BRANCH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url)
      REPO_URL="${2:-}"
      shift 2
      ;;
    --dest)
      DEST="${2:-}"
      shift 2
      ;;
    --email)
      EMAIL="${2:-}"
      shift 2
      ;;
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --skip-tls)
      SKIP_TLS=1
      shift
      ;;
    --branch)
      FORCE_BRANCH="${2:-}"
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

if [[ -z "$REPO_URL" || -z "$DEST" ]]; then
  echo "Error: --repo-url and --dest are required." >&2
  usage
  exit 1
fi

if [[ "$SKIP_TLS" -eq 0 && -z "$EMAIL" ]]; then
  echo "Error: --email is required unless --skip-tls is set." >&2
  exit 1
fi

if [[ -n "$EMAIL" && ! "$EMAIL" =~ ^[^@]+@[^@]+\.[^@]+$ ]]; then
  echo "Error: invalid email '$EMAIL'." >&2
  exit 1
fi

require_root
require_cmd git
require_cmd jq
require_cmd systemctl
require_cmd nginx

repo_checkout() {
  local repo_url="$1"
  local dest="$2"

  if [[ ! -e "$dest" ]]; then
    mkdir -p "$(dirname "$dest")"
    echo "[1/7] Cloning repository into $dest"
    git clone "$repo_url" "$dest"
    return
  fi

  if [[ ! -d "$dest/.git" ]]; then
    echo "Destination exists but is not a git repository: $dest" >&2
    exit 1
  fi

  local existing_origin
  existing_origin=$(git -C "$dest" remote get-url origin 2>/dev/null || true)

  if [[ -n "$existing_origin" && "$existing_origin" != "$repo_url" ]]; then
    echo "Destination repository origin mismatch." >&2
    echo "  existing: $existing_origin" >&2
    echo "  expected: $repo_url" >&2
    exit 1
  fi

  if [[ -z "$existing_origin" ]]; then
    git -C "$dest" remote add origin "$repo_url"
  fi

  echo "[1/7] Updating existing repository at $dest"
  git -C "$dest" fetch --prune origin
}

checkout_branch() {
  local dest="$1"
  local branch="$2"

  if [[ -z "$branch" ]]; then
    return
  fi

  echo "[2/7] Ensuring branch '$branch' is checked out"
  git -C "$dest" checkout "$branch"
  git -C "$dest" reset --hard "origin/$branch"
}

read_server_conf() {
  local conf_path="$1"

  if [[ ! -f "$conf_path" ]]; then
    echo "Missing required file: $conf_path" >&2
    exit 1
  fi

  if ! jq empty "$conf_path" >/dev/null 2>&1; then
    echo "Invalid JSON in $conf_path" >&2
    exit 1
  fi
}

register_site_entry() {
  local discovered_json="$1"
  local config_path="$2"

  local site_name site_domain
  site_name=$(jq -r '.name' <<<"$discovered_json")
  site_domain=$(jq -r '.domain' <<<"$discovered_json")

  mkdir -p "$(dirname "$config_path")"
  if [[ ! -f "$config_path" ]]; then
    printf '[]\n' > "$config_path"
  fi

  if ! jq empty "$config_path" >/dev/null 2>&1; then
    echo "Existing config is not valid JSON: $config_path" >&2
    exit 1
  fi

  if [[ "$(jq -r 'type' "$config_path")" != "array" ]]; then
    echo "Existing config must be a JSON array: $config_path" >&2
    exit 1
  fi

  local conflicting
  conflicting=$(jq -r --arg name "$site_name" --arg domain "$site_domain" '.[] | select((.domain == $domain and .name != $name)) | .name' "$config_path" | head -n1 || true)
  if [[ -n "$conflicting" ]]; then
    echo "Refusing to register '$site_name': domain '$site_domain' is already used by '$conflicting'." >&2
    exit 1
  fi

  local tmp
  tmp=$(mktemp)
  jq --argjson entry "$discovered_json" --arg name "$site_name" '
    map(select(.name != $name)) + [$entry]
    | sort_by(.name)
  ' "$config_path" > "$tmp"
  mv "$tmp" "$config_path"

  if jq -e --arg name "$site_name" '.[] | select(.name == $name)' "$config_path" >/dev/null; then
    echo "[4/7] Registered/updated app '$site_name' in $config_path"
  else
    echo "Failed to register app '$site_name'" >&2
    exit 1
  fi
}

run_tls_step() {
  local domain="$1"
  local include_www="$2"

  if [[ "$SKIP_TLS" -eq 1 ]]; then
    echo "[6/7] TLS step skipped by --skip-tls"
    return
  fi

  echo "[6/7] Acquiring/updating TLS certificates"
  local tls_args=(--domain "$domain" --email "$EMAIL")
  if [[ "$include_www" == "1" ]]; then
    tls_args+=(--www)
  fi
  ./scripts/setup-letsencrypt.sh "${tls_args[@]}"
}

check_dns_status() {
  local domain="$1"
  local target_ip="$2"

  if ! command -v getent >/dev/null 2>&1; then
    echo "unknown (getent unavailable)"
    return
  fi

  mapfile -t resolved_ips < <(getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | sort -u)

  if [[ ${#resolved_ips[@]} -eq 0 ]]; then
    echo "not-resolved"
    return
  fi

  if [[ -n "$target_ip" ]]; then
    for ip in "${resolved_ips[@]}"; do
      if [[ "$ip" == "$target_ip" ]]; then
        echo "ok"
        return
      fi
    done
    echo "mismatch"
    return
  fi

  echo "resolved"
}

# 1) Clone/update
repo_checkout "$REPO_URL" "$DEST"

# Optional branch override prior to validation.
if [[ -n "$FORCE_BRANCH" ]]; then
  checkout_branch "$DEST" "$FORCE_BRANCH"
fi

# 2) Verify server.conf structure and discover normalized entry.
CONF_PATH="$DEST/server.conf"
read_server_conf "$CONF_PATH"

echo "[3/7] Validating server.conf via discover-sites"
DISCOVER_TMP=$(mktemp)
./scripts/discover-sites.sh --base-glob "$DEST" --output "$DISCOVER_TMP"
SITE_COUNT=$(jq 'length' "$DISCOVER_TMP")
if [[ "$SITE_COUNT" -ne 1 ]]; then
  echo "Expected exactly one site in discovered output, got $SITE_COUNT" >&2
  rm -f "$DISCOVER_TMP"
  exit 1
fi
SITE_ENTRY=$(jq '.[0]' "$DISCOVER_TMP")
rm -f "$DISCOVER_TMP"

SITE_NAME=$(jq -r '.name' <<<"$SITE_ENTRY")
SITE_DOMAIN=$(jq -r '.domain' <<<"$SITE_ENTRY")
RUNTIME_MODE=$(jq -r '.runtime.mode // "static"' <<<"$SITE_ENTRY")
CURRENT_SYMLINK=$(jq -r '.current_symlink' <<<"$SITE_ENTRY")
WWW_REDIRECT=$(jq -r '.nginx.www_redirect // false' <<<"$SITE_ENTRY")
TLS_HAS_WWW=$(jq -r --arg www "www.${SITE_DOMAIN}" '.nginx.tls_hostnames // [] | index($www) != null' <<<"$SITE_ENTRY")

if [[ -z "$SITE_NAME" || -z "$SITE_DOMAIN" ]]; then
  echo "Discovered site is missing name/domain" >&2
  exit 1
fi

# Align checked-out branch if server.conf defines one and user did not force override.
if [[ -z "$FORCE_BRANCH" ]]; then
  CONF_BRANCH=$(jq -r '.branch // empty' "$CONF_PATH")
  if [[ -n "$CONF_BRANCH" ]]; then
    checkout_branch "$DEST" "$CONF_BRANCH"
  fi
fi

# 3) Register/update site entry idempotently.
register_site_entry "$SITE_ENTRY" "$CONFIG_PATH"

# 4) Deploy this site (generates runtime + nginx config).
echo "[5/7] Deploying '$SITE_NAME'"
./scripts/sync-github-sites.sh --config "$CONFIG_PATH" --site "$SITE_NAME"

# 5) Acquire/update TLS certs.
INCLUDE_WWW=0
if [[ "$WWW_REDIRECT" == "true" || "$TLS_HAS_WWW" == "true" ]]; then
  INCLUDE_WWW=1
fi
run_tls_step "$SITE_DOMAIN" "$INCLUDE_WWW"

# 6) Post-run summary.
echo "[7/7] Post-run summary"
ACTIVE_RELEASE="<missing>"
if [[ -L "$CURRENT_SYMLINK" || -e "$CURRENT_SYMLINK" ]]; then
  ACTIVE_RELEASE=$(readlink -f "$CURRENT_SYMLINK" || true)
  if [[ -z "$ACTIVE_RELEASE" ]]; then
    ACTIVE_RELEASE="$CURRENT_SYMLINK"
  fi
fi

SERVICE_STATUS="n/a (static mode)"
if [[ "$RUNTIME_MODE" == "service" ]]; then
  UNIT_NAME="app-${SITE_NAME}.service"
  SERVICE_STATUS=$(systemctl is-active "$UNIT_NAME" 2>/dev/null || true)
  if [[ -z "$SERVICE_STATUS" ]]; then
    SERVICE_STATUS="unknown"
  fi
fi

PUBLIC_IP=""
if command -v curl >/dev/null 2>&1; then
  PUBLIC_IP=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)
fi
DNS_STATUS=$(check_dns_status "$SITE_DOMAIN" "$PUBLIC_IP")
MANUAL_ACTION="none"
if [[ "$SKIP_TLS" -eq 1 ]]; then
  MANUAL_ACTION="TLS step skipped; run scripts/setup-letsencrypt.sh when DNS is ready"
elif [[ "$DNS_STATUS" == "not-resolved" || "$DNS_STATUS" == "mismatch" ]]; then
  MANUAL_ACTION="DNS may not be fully propagated for $SITE_DOMAIN; verify A/AAAA records and re-run onboarding"
fi

cat <<SUMMARY

Onboarding complete.
- Domain: $SITE_DOMAIN
- Service status: $SERVICE_STATUS
- Active release path: $ACTIVE_RELEASE
- DNS status: $DNS_STATUS
- Manual action required: $MANUAL_ACTION
SUMMARY
