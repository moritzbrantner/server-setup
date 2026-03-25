#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/test-helpers.sh
source "$SCRIPT_DIR/lib/test-helpers.sh"

test_render_static_site_config() {
  local conf
  conf="$(python3 "$ROOT_DIR/scripts/install_nginx_site.py" --domain example.test --root /var/www/example.test/public --render-config)"

  grep -Fq "root /var/www/example.test/public;" <<<"$conf"
  grep -Fq 'try_files $uri $uri/ =404;' <<<"$conf"
}

test_render_proxy_site_config() {
  local conf
  conf="$(python3 "$ROOT_DIR/scripts/install_nginx_site.py" --domain example.test --port 3000 --www-redirect --render-config)"

  grep -Fq "proxy_pass http://127.0.0.1:3000;" <<<"$conf"
  grep -Fq 'proxy_set_header X-Forwarded-Host $host;' <<<"$conf"
  grep -Fq 'proxy_set_header X-Forwarded-Port $server_port;' <<<"$conf"
  grep -Fq 'proxy_set_header Upgrade $http_upgrade;' <<<"$conf"
  grep -Fq 'proxy_set_header Connection "upgrade";' <<<"$conf"
  grep -Fq 'proxy_read_timeout 60s;' <<<"$conf"
  grep -Fq 'proxy_send_timeout 60s;' <<<"$conf"
  grep -Fq "server_name www.example.test;" <<<"$conf"
}

test_render_proxy_site_config_with_tls() {
  local tmp
  local conf
  tmp="$(make_temp_dir)"
  mkdir -p "$tmp/live/example.test"
  printf 'fullchain\n' >"$tmp/live/example.test/fullchain.pem"
  printf 'privkey\n' >"$tmp/live/example.test/privkey.pem"
  printf 'options\n' >"$tmp/options-ssl-nginx.conf"
  printf 'dhparams\n' >"$tmp/ssl-dhparams.pem"

  conf="$(
    LETSENCRYPT_LIVE_DIR="$tmp/live" \
      LETSENCRYPT_OPTIONS_PATH="$tmp/options-ssl-nginx.conf" \
      LETSENCRYPT_DHPARAM_PATH="$tmp/ssl-dhparams.pem" \
      python3 "$ROOT_DIR/scripts/install_nginx_site.py" --domain example.test --port 3000 --www-redirect --render-config
  )"

  grep -Fq 'listen 443 ssl;' <<<"$conf"
  grep -Fq 'server_name example.test;' <<<"$conf"
  grep -Fq 'server_name www.example.test;' <<<"$conf"
  grep -Fq 'return 301 https://example.test$request_uri;' <<<"$conf"
  grep -Fq "ssl_certificate $tmp/live/example.test/fullchain.pem;" <<<"$conf"
  grep -Fq "ssl_certificate_key $tmp/live/example.test/privkey.pem;" <<<"$conf"
  rm -rf "$tmp"
}

run_test "install-nginx-site renders static config" test_render_static_site_config
run_test "install-nginx-site renders proxy config" test_render_proxy_site_config
run_test "install-nginx-site renders tls-aware proxy config" test_render_proxy_site_config_with_tls

echo "All tests passed: $pass_count"
