#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage:
  sudo ./scripts/install-nginx-site.sh \
    --domain example.com \
    (--root /var/www/example.com/public | --port 3000) \
    [--www-redirect] \
    [--email admin@example.com]

Description:
  - Installs Nginx if missing.
  - Creates a domain-based server block at /etc/nginx/sites-available/<domain>.conf
  - Enables the site in /etc/nginx/sites-enabled.
  - Either serves a static web root or reverse proxies to 127.0.0.1:<port>.
  - Creates the web root and a basic index.html if absent when --root is used.
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

valid_port() {
  local value="$1"
  [[ "$value" =~ ^[0-9]+$ ]] && (( 10#$value >= 1 && 10#$value <= 65535 ))
}

DOMAIN=""
ROOT=""
PORT=""
WWW_REDIRECT=0
EMAIL=""
LETSENCRYPT_LIVE_DIR="${LETSENCRYPT_LIVE_DIR:-/etc/letsencrypt/live}"
LETSENCRYPT_OPTIONS_PATH="${LETSENCRYPT_OPTIONS_PATH:-/etc/letsencrypt/options-ssl-nginx.conf}"
LETSENCRYPT_DHPARAM_PATH="${LETSENCRYPT_DHPARAM_PATH:-/etc/letsencrypt/ssl-dhparams.pem}"

parse_args() {
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
      --port)
        PORT="${2:-}"
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
}

validate_args() {
  if [[ -z "$DOMAIN" ]]; then
    echo "Error: --domain is required." >&2
    usage
    exit 1
  fi

  if [[ -z "$ROOT" && -z "$PORT" ]]; then
    echo "Error: one of --root or --port is required." >&2
    usage
    exit 1
  fi

  if [[ -n "$ROOT" && -n "$PORT" ]]; then
    echo "Error: --root and --port are mutually exclusive." >&2
    usage
    exit 1
  fi

  if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
    echo "Error: invalid domain value '$DOMAIN'." >&2
    exit 1
  fi

  if [[ -n "$PORT" ]] && ! valid_port "$PORT"; then
    echo "Error: --port must be a numeric TCP port between 1 and 65535." >&2
    exit 1
  fi
}

render_nginx_site_config() {
  local domain="$1"
  local root="$2"
  local port="$3"
  local www_redirect="$4"
  local location_block=""
  local redirect_block=""
  local cert_dir="${LETSENCRYPT_LIVE_DIR}/${domain}"
  local cert_fullchain="${cert_dir}/fullchain.pem"
  local cert_privkey="${cert_dir}/privkey.pem"
  local has_tls=0

  if [[ -f "$cert_fullchain" && -f "$cert_privkey" && -f "$LETSENCRYPT_OPTIONS_PATH" && -f "$LETSENCRYPT_DHPARAM_PATH" ]]; then
    has_tls=1
  fi

  if [[ -n "$port" ]]; then
    location_block=$(cat <<NGINX
    location / {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port \$server_port;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_pass http://127.0.0.1:${port};
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
NGINX
)
  else
    location_block=$(cat <<NGINX
    root ${root};
    index index.html index.htm;

    location / {
        try_files \$uri \$uri/ =404;
    }
NGINX
)
  fi

  if [[ "$www_redirect" -eq 1 ]]; then
    redirect_block=$(cat <<NGINX

server {
    listen 80;
    listen [::]:80;
    server_name www.${domain};

    return 301 https://${domain}\$request_uri;
}
NGINX
)

    if [[ "$has_tls" -eq 1 ]]; then
      redirect_block+=$(cat <<NGINX

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name www.${domain};

    ssl_certificate ${cert_fullchain};
    ssl_certificate_key ${cert_privkey};
    include ${LETSENCRYPT_OPTIONS_PATH};
    ssl_dhparam ${LETSENCRYPT_DHPARAM_PATH};

    return 301 https://${domain}\$request_uri;
}
NGINX
)
    fi
  fi

  if [[ "$has_tls" -eq 1 ]]; then
    cat <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    return 301 https://${domain}\$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${domain};

    ssl_certificate ${cert_fullchain};
    ssl_certificate_key ${cert_privkey};
    include ${LETSENCRYPT_OPTIONS_PATH};
    ssl_dhparam ${LETSENCRYPT_DHPARAM_PATH};

${location_block}

    access_log /var/log/nginx/${domain}.access.log;
    error_log  /var/log/nginx/${domain}.error.log;
}
${redirect_block}
NGINX
    return
  fi

  cat <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

${location_block}

    access_log /var/log/nginx/${domain}.access.log;
    error_log  /var/log/nginx/${domain}.error.log;
}
${redirect_block}
NGINX
}

create_static_root_if_needed() {
  if [[ -z "$ROOT" ]]; then
    return 0
  fi

  echo "[2/6] Creating web root..."
  mkdir -p "$ROOT"
  if [[ ! -f "$ROOT/index.html" ]]; then
    cat >"$ROOT/index.html" <<HTML
<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><title>${DOMAIN}</title></head>
  <body><h1>${DOMAIN} is live</h1></body>
</html>
HTML
  fi
}

main() {
  parse_args "$@"
  validate_args

  local site_conf="/etc/nginx/sites-available/${DOMAIN}.conf"
  local site_link="/etc/nginx/sites-enabled/${DOMAIN}.conf"
  local target_desc=""

  require_root

  echo "[1/6] Installing Nginx..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y nginx

  if [[ -n "$ROOT" ]]; then
    create_static_root_if_needed
    target_desc="Root: ${ROOT}"
  else
    echo "[2/6] Configuring reverse proxy target..."
    echo "Proxy upstream: http://127.0.0.1:${PORT}"
    target_desc="Upstream: http://127.0.0.1:${PORT}"
  fi

  echo "[3/6] Writing Nginx site config..."
  render_nginx_site_config "$DOMAIN" "$ROOT" "$PORT" "$WWW_REDIRECT" >"$site_conf"

  echo "[4/6] Enabling site..."
  rm -f "$site_link"
  ln -s "$site_conf" "$site_link"

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
${target_desc}
Config: ${site_conf}

Next step (recommended):
  sudo ./scripts/setup-letsencrypt.sh --domain ${DOMAIN}${EMAIL:+ --email ${EMAIL}}
DONE
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
