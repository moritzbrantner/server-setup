#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage:
  sudo ./scripts/init-server.sh \
    --domain example.com \
    --web-root /var/www/example.com/public \
    --email admin@example.com \
    [--www] \
    [--skip-certbot] \
    [--skip-docker] \
    [--non-interactive]

Description:
  Canonical one-command server bootstrap that runs:
    1) scripts/ensure-server-tools.sh
    2) scripts/install-nginx-site.sh
    3) scripts/setup-letsencrypt.sh (unless --skip-certbot)

Options:
  --domain            Required. Primary domain (example.com).
  --web-root          Required. Nginx web root path.
  --email             Required. Email for Let's Encrypt registration.
  --www               Optional. Configure www redirect and include www cert SAN.
  --skip-certbot      Optional. Skip Let's Encrypt/certbot step.
  --skip-docker       Optional. Skip Docker installation check.
  --non-interactive   Optional. Do not prompt for confirmation when DNS looks wrong.
  -h, --help          Show this help.
USAGE
}

log() {
  printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "This script must be run as root (use sudo)."
  fi
}

DOMAIN=""
WEB_ROOT=""
EMAIL=""
INCLUDE_WWW=0
SKIP_CERTBOT=0
SKIP_DOCKER=0
NON_INTERACTIVE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="${2:-}"
      shift 2
      ;;
    --web-root)
      WEB_ROOT="${2:-}"
      shift 2
      ;;
    --email)
      EMAIL="${2:-}"
      shift 2
      ;;
    --www)
      INCLUDE_WWW=1
      shift
      ;;
    --skip-certbot)
      SKIP_CERTBOT=1
      shift
      ;;
    --skip-docker)
      SKIP_DOCKER=1
      shift
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[[ -n "$DOMAIN" ]] || { usage; die "--domain is required."; }
[[ -n "$WEB_ROOT" ]] || { usage; die "--web-root is required."; }
[[ -n "$EMAIL" ]] || { usage; die "--email is required."; }

[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || die "Invalid domain '$DOMAIN'."
[[ "$EMAIL" =~ ^[^@]+@[^@]+\.[^@]+$ ]] || die "Invalid email '$EMAIL'."

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

status_tools="not-run"
status_docker="not-run"
status_nginx="not-run"
status_dns="not-run"
status_certbot="not-run"

print_summary() {
  cat <<SUMMARY

========== init-server summary ==========
Domain:        $DOMAIN
Web root:      $WEB_ROOT
Email:         $EMAIL
www enabled:   $([[ "$INCLUDE_WWW" -eq 1 ]] && echo yes || echo no)

Step status:
- ensure-server-tools: $status_tools
- docker check/install: $status_docker
- install-nginx-site:  $status_nginx
- dns preflight:       $status_dns
- setup-letsencrypt:   $status_certbot
========================================
SUMMARY
}

cleanup() {
  local ec=$?
  print_summary
  exit "$ec"
}
trap cleanup EXIT

collect_local_ips() {
  local ips=""

  if command -v hostname >/dev/null 2>&1; then
    ips+=" $(hostname -I 2>/dev/null || true)"
  fi

  if command -v curl >/dev/null 2>&1; then
    ips+=" $(curl -4fsS --max-time 3 https://ifconfig.me 2>/dev/null || true)"
    ips+=" $(curl -4fsS --max-time 3 https://api.ipify.org 2>/dev/null || true)"
  fi

  printf '%s\n' "$ips" | tr ' ' '\n' | awk 'NF' | sort -u
}

collect_domain_ips() {
  local name="$1"
  getent ahosts "$name" 2>/dev/null | awk '{print $1}' | sort -u
}

dns_preflight_or_die() {
  local -a hosts=("$DOMAIN")
  if [[ "$INCLUDE_WWW" -eq 1 ]]; then
    hosts+=("www.$DOMAIN")
  fi

  local local_ips
  local_ips="$(collect_local_ips || true)"

  local any_missing=0
  local any_mismatch=0

  for host in "${hosts[@]}"; do
    local host_ips
    host_ips="$(collect_domain_ips "$host" || true)"

    if [[ -z "$host_ips" ]]; then
      log "DNS preflight: '$host' does not resolve yet."
      any_missing=1
      continue
    fi

    if [[ -n "$local_ips" ]]; then
      local match=0
      while IFS= read -r hip; do
        [[ -n "$hip" ]] || continue
        if printf '%s\n' "$local_ips" | grep -Fxq "$hip"; then
          match=1
          break
        fi
      done <<< "$host_ips"

      if [[ "$match" -eq 0 ]]; then
        log "DNS preflight: '$host' resolves to [$(echo "$host_ips" | xargs)], but local server IPs appear to be [$(echo "$local_ips" | xargs)]"
        any_mismatch=1
      fi
    fi
  done

  if [[ "$any_missing" -eq 1 || "$any_mismatch" -eq 1 ]]; then
    status_dns="failed"

    cat <<MSG
DNS check indicates records are not ready for certbot.
Action items:
  1) Point A/AAAA records for $DOMAIN$([[ "$INCLUDE_WWW" -eq 1 ]] && echo " and www.$DOMAIN") to this server.
  2) Wait for propagation.
  3) Re-run init with --skip-certbot, then run setup-letsencrypt.sh later.
MSG

    if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
      die "Aborting certbot step because --non-interactive was supplied and DNS is not ready."
    fi

    read -r -p "Continue anyway and attempt certbot? [y/N] " reply
    if [[ ! "$reply" =~ ^[Yy]$ ]]; then
      die "Aborting before certbot due to DNS readiness check."
    fi
  fi

  status_dns="ok"
}

require_root
cd "$ROOT_DIR"

log "[1/5] Ensuring baseline server tools are present"
tools_args=()
if [[ "$SKIP_DOCKER" -eq 1 ]]; then
  tools_args+=(--skip-docker)
fi
"$SCRIPT_DIR/ensure-server-tools.sh" "${tools_args[@]}"
status_tools="ok"

if [[ "$SKIP_DOCKER" -eq 1 ]]; then
  status_docker="skipped"
else
  status_docker="ok"
fi

log "[3/5] Installing/updating Nginx site configuration"
nginx_args=(--domain "$DOMAIN" --root "$WEB_ROOT" --email "$EMAIL")
if [[ "$INCLUDE_WWW" -eq 1 ]]; then
  nginx_args+=(--www-redirect)
fi
"$SCRIPT_DIR/install-nginx-site.sh" "${nginx_args[@]}"
status_nginx="ok"

if [[ "$SKIP_CERTBOT" -eq 1 ]]; then
  status_dns="skipped"
  status_certbot="skipped"
  log "[4/5] Certbot step skipped by --skip-certbot"
  exit 0
fi

log "[4/5] Checking DNS readiness before certbot"
dns_preflight_or_die

log "[5/5] Provisioning TLS certificate with certbot"
certbot_args=(--domain "$DOMAIN" --email "$EMAIL")
if [[ "$INCLUDE_WWW" -eq 1 ]]; then
  certbot_args+=(--www)
fi
"$SCRIPT_DIR/setup-letsencrypt.sh" "${certbot_args[@]}"
status_certbot="ok"
