#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
INSTALL_DIR="${SERVER_SETUP_INSTALL_DIR:-/opt/server-setup}"
BIN_PATH="${SERVER_SETUP_BIN_PATH:-/usr/local/bin/server-setup}"

legacy_requested=0
for arg in "$@"; do
  case "$arg" in
    --legacy|--skip-dokploy|--skip-observability|--skip-hardening|--cutover-preflight|--replace-legacy|--confirm-legacy-cutover-ready|--public-observability|--with-beszel-agent|--with-ssh-hardening|--dry-run)
      legacy_requested=1
      ;;
  esac
done

if [[ "$legacy_requested" -eq 1 ]]; then
  args=("$@")
  if [[ "${args[0]:-}" == "--legacy" ]]; then
    args=("${args[@]:1}")
  fi
  echo "[server-setup] Using the compatibility installer. New installations should use the guided host bootstrap." >&2
  exec bash "$ROOT_DIR/scripts/legacy_setup.sh" "${args[@]}"
fi

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "[server-setup] ERROR: run setup.sh as root (for example with sudo)." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "[server-setup] ERROR: PR2 supports Debian 12 and Ubuntu 24.04 apt-based hosts only." >&2
  exit 1
fi

# Keep this bootstrap deliberately small. The Python core owns the actual desired host state.
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 ca-certificates

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("server-setup requires Python 3.11+ (Debian 12 or Ubuntu 24.04 in PR2)")
PY

install -d -m 0755 "$INSTALL_DIR"
rm -rf "$INSTALL_DIR/server_setup"
cp -a "$ROOT_DIR/server_setup" "$INSTALL_DIR/server_setup"
cp "$ROOT_DIR/config.example.toml" "$INSTALL_DIR/config.example.toml"

cat > "$BIN_PATH" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$INSTALL_DIR"
exec python3 -m server_setup "\$@"
EOF
chmod 0755 "$BIN_PATH"

exec "$BIN_PATH" setup "$@"
