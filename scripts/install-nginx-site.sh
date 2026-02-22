#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage:
  sudo ./scripts/install-nginx-site.sh \
    --domain example.com \
    --root /var/www/example.com/public \
    [--www-redirect] \
    [--email admin@example.com]

Description:
  - Installs Nginx if missing.
  - Creates a domain-based server block at /etc/nginx/sites-available/<domain>.conf
  - Enables the site in /etc/nginx/sites-enabled.
  - Creates the web root and a basic index.html if absent.
  - Opens the firewall for "Nginx Full" if UFW is active.

Notes:
  - This script configures HTTP only. Run setup-letsencrypt.sh afterwards for TLS.
USAGE
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root (use sudo)." >&2
    exit 1
  fi
}

DOMAIN=""
ROOT=""
WWW_REDIRECT=0
EMAIL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="${2:-}"
      shift 2
      ;;
    --root)
      ROOT="${2:-}"
      shift 2
      ;;
    --www-redirect)
      WWW_REDIRECT=1
      shift
      ;;
    --email)
      EMAIL="${2:-}"
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

if [[ -z "$DOMAIN" || -z "$ROOT" ]]; then
  echo "Error: --domain and --root are required." >&2
  usage
  exit 1
fi

if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "Error: invalid domain value '$DOMAIN'." >&2
  exit 1
fi

SITE_CONF="/etc/nginx/sites-available/${DOMAIN}.conf"
SITE_LINK="/etc/nginx/sites-enabled/${DOMAIN}.conf"

require_root

echo "[1/6] Installing Nginx..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx

echo "[2/6] Creating web root..."
mkdir -p "$ROOT"
if [[ ! -f "$ROOT/index.html" ]]; then
  cat > "$ROOT/index.html" <<HTML
<!doctype html>
<html lang=\"en\">
  <head><meta charset=\"utf-8\"><title>${DOMAIN}</title></head>
  <body><h1>${DOMAIN} is live</h1></body>
</html>
HTML
fi

PRIMARY_NAMES="$DOMAIN"

echo "[3/6] Writing Nginx site config..."
cat > "$SITE_CONF" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${PRIMARY_NAMES};

    root ${ROOT};
    index index.html index.htm;

    location / {
        try_files \$uri \$uri/ =404;
    }

    access_log /var/log/nginx/${DOMAIN}.access.log;
    error_log  /var/log/nginx/${DOMAIN}.error.log;
}
NGINX

if [[ "$WWW_REDIRECT" -eq 1 ]]; then
  cat >> "$SITE_CONF" <<NGINX

server {
    listen 80;
    listen [::]:80;
    server_name www.${DOMAIN};

    return 301 http://${DOMAIN}\$request_uri;
}
NGINX
fi

echo "[4/6] Enabling site..."
rm -f "$SITE_LINK"
ln -s "$SITE_CONF" "$SITE_LINK"

if [[ -f /etc/nginx/sites-enabled/default ]]; then
  rm -f /etc/nginx/sites-enabled/default
fi

echo "[5/6] Validating and reloading Nginx..."
nginx -t
systemctl enable nginx
systemctl restart nginx

echo "[6/6] Adjusting firewall (if UFW is active)..."
if command -v ufw >/dev/null 2>&1; then
  if ufw status | grep -q "Status: active"; then
    ufw allow 'Nginx Full' >/dev/null
    echo "UFW updated: allowed 'Nginx Full'."
  else
    echo "UFW installed but not active; skipping firewall change."
  fi
else
  echo "UFW not installed; skipping firewall change."
fi

cat <<DONE

Done.
Site: ${DOMAIN}
Root: ${ROOT}
Config: ${SITE_CONF}

Next step (recommended):
  sudo ./scripts/setup-letsencrypt.sh --domain ${DOMAIN}${EMAIL:+ --email ${EMAIL}}
DONE
