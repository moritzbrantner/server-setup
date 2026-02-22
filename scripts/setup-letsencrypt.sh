#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage:
  sudo ./scripts/setup-letsencrypt.sh --domain example.com --email admin@example.com [--www]

Description:
  - Installs Certbot + nginx plugin.
  - Requests/installs Let's Encrypt certificates for the provided domain.
  - Optionally includes www.<domain> as SAN.
  - Enables auto-renewal and performs a dry-run renewal test.

Requirements:
  - DNS for domain (and www, if used) must point to this server.
  - Nginx site for the domain should already exist and be reachable on port 80.
USAGE
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root (use sudo)." >&2
    exit 1
  fi
}

DOMAIN=""
EMAIL=""
INCLUDE_WWW=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="${2:-}"
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

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
  echo "Error: --domain and --email are required." >&2
  usage
  exit 1
fi

if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "Error: invalid domain value '$DOMAIN'." >&2
  exit 1
fi

if [[ ! "$EMAIL" =~ ^[^@]+@[^@]+\.[^@]+$ ]]; then
  echo "Error: invalid email '$EMAIL'." >&2
  exit 1
fi

require_root

echo "[1/5] Installing Certbot and Nginx plugin..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y certbot python3-certbot-nginx

domains=("-d" "$DOMAIN")
if [[ "$INCLUDE_WWW" -eq 1 ]]; then
  domains+=("-d" "www.$DOMAIN")
fi

echo "[2/5] Requesting and installing certificate..."
certbot --nginx \
  --non-interactive \
  --agree-tos \
  --email "$EMAIL" \
  --redirect \
  "${domains[@]}"

echo "[3/5] Ensuring certbot.timer is enabled..."
systemctl enable certbot.timer >/dev/null 2>&1 || true
systemctl start certbot.timer >/dev/null 2>&1 || true

echo "[4/5] Checking renewal configuration..."
certbot renew --dry-run

echo "[5/5] Reloading Nginx..."
nginx -t
systemctl reload nginx

cat <<DONE

Done.
Certificate installed for: ${DOMAIN}$( [[ "$INCLUDE_WWW" -eq 1 ]] && echo " and www.${DOMAIN}" )
Auto-renewal: enabled (certbot.timer)

Verify:
  sudo certbot certificates
DONE
