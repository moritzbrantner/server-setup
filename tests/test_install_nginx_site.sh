#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/test-helpers.sh
source "$SCRIPT_DIR/lib/test-helpers.sh"

# shellcheck source=../scripts/install-nginx-site.sh
source "$ROOT_DIR/scripts/install-nginx-site.sh"

test_render_static_site_config() {
  local conf
  conf="$(render_nginx_site_config "example.test" "/var/www/example.test/public" "" 0)"

  grep -Fq "root /var/www/example.test/public;" <<<"$conf"
  grep -Fq 'try_files $uri $uri/ =404;' <<<"$conf"
}

test_render_proxy_site_config() {
  local conf
  conf="$(render_nginx_site_config "example.test" "" "3000" 1)"

  grep -Fq "proxy_pass http://127.0.0.1:3000;" <<<"$conf"
  grep -Fq 'proxy_set_header Upgrade $http_upgrade;' <<<"$conf"
  grep -Fq 'proxy_set_header Connection "upgrade";' <<<"$conf"
  grep -Fq "server_name www.example.test;" <<<"$conf"
}

run_test "install-nginx-site renders static config" test_render_static_site_config
run_test "install-nginx-site renders proxy config" test_render_proxy_site_config

echo "All tests passed: $pass_count"
