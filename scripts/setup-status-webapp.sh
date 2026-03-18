#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  sudo ./scripts/setup-status-webapp.sh [--root /path/to/server-setup]

Description:
  Installs Node.js when required, builds the monitoring webapp, installs the
  systemd unit, and enables the service so it always runs on port 4000.
USAGE
}

log() {
  printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

require_root() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    if have_cmd sudo; then
      SUDO='sudo'
    else
      log 'This script must run as root or with sudo available.'
      exit 1
    fi
  else
    SUDO=''
  fi
}

install_pkgs() {
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "$@"
}

ensure_apt() {
  if ! have_cmd apt-get; then
    log 'This script supports Ubuntu/Debian hosts with apt.'
    exit 1
  fi
}

configure_nodesource_repo() {
  local os_id codename arch keyring_path repo_file

  if [[ ! -f /etc/os-release ]]; then
    log 'Cannot configure NodeSource repository: /etc/os-release missing.'
    exit 1
  fi

  # shellcheck source=/etc/os-release
  . /etc/os-release
  os_id="${ID:-}"
  codename="${VERSION_CODENAME:-}"
  arch="$($SUDO dpkg --print-architecture)"

  if [[ "$os_id" != 'ubuntu' && "$os_id" != 'debian' ]]; then
    log "Unsupported distro id for NodeSource repo: '$os_id'."
    exit 1
  fi

  if [[ -z "$codename" ]]; then
    codename="$(lsb_release -cs 2>/dev/null || true)"
  fi

  if [[ -z "$codename" ]]; then
    log 'Unable to determine distro codename for NodeSource repo setup.'
    exit 1
  fi

  keyring_path='/etc/apt/keyrings/nodesource.gpg'
  repo_file='/etc/apt/sources.list.d/nodesource.list'

  install_pkgs ca-certificates curl gnupg
  $SUDO install -m 0755 -d /etc/apt/keyrings

  if [[ ! -s "$keyring_path" ]]; then
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor \
      | $SUDO tee "$keyring_path" >/dev/null
    $SUDO chmod a+r "$keyring_path"
  fi

  $SUDO tee "$repo_file" >/dev/null <<EOF
deb [arch=$arch signed-by=$keyring_path] https://deb.nodesource.com/node_22.x $codename main
EOF
}

node_major_version() {
  node -p 'process.versions.node.split(".")[0]'
}

ensure_nodejs() {
  local need_install=0

  if have_cmd node && have_cmd npm; then
    if [[ "$(node_major_version)" -lt 20 ]]; then
      log "Node.js $(node -v) is too old; upgrading to Node.js 22."
      need_install=1
    else
      log "Node.js already present: $(node -v)"
    fi
  else
    need_install=1
  fi

  if [[ "$need_install" -eq 0 ]]; then
    return
  fi

  log 'Installing Node.js 22.x'
  $SUDO apt-get update -y
  configure_nodesource_repo
  $SUDO apt-get update -y
  install_pkgs nodejs
}

render_status_webapp_env() {
  local root_dir="$1"
  local host="$2"
  local port="$3"

  cat <<EOF
SERVER_SETUP_ROOT=$root_dir
STATUS_WEBAPP_HOST=$host
STATUS_WEBAPP_PORT=$port
EOF
}

render_status_webapp_service() {
  local root_dir="$1"
  local env_file="$2"

  cat <<EOF
[Unit]
Description=Server Setup status webapp
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=-$env_file
Environment=SERVER_SETUP_ROOT=$root_dir
Environment=STATUS_WEBAPP_HOST=0.0.0.0
Environment=STATUS_WEBAPP_PORT=4000
WorkingDirectory=$root_dir/monitor/webapp
ExecStart=/usr/bin/env bash $root_dir/scripts/start-status-webapp.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
}

write_file() {
  local destination="$1"
  local mode="$2"
  local tmp
  tmp="$(mktemp)"
  cat >"$tmp"
  $SUDO install -m "$mode" "$tmp" "$destination"
  rm -f "$tmp"
}

build_status_webapp() {
  local webapp_dir="$1"

  if [[ ! -d "$webapp_dir" ]]; then
    log "Monitoring webapp directory not found at $webapp_dir"
    exit 1
  fi

  log 'Installing monitoring webapp dependencies'
  (
    cd "$webapp_dir"
    npm ci --no-fund --no-audit
  )

  log 'Building monitoring webapp'
  (
    cd "$webapp_dir"
    npm run build
  )
}

enable_status_webapp_service() {
  local service_name="$1"

  if ! have_cmd systemctl; then
    log 'systemctl not found; cannot enable the monitoring webapp service.'
    exit 1
  fi

  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now "$service_name"

  if ! $SUDO systemctl is-active --quiet "$service_name"; then
    log "Service '$service_name' failed to start."
    $SUDO systemctl status "$service_name" --no-pager || true
    exit 1
  fi
}

wait_for_status_webapp() {
  local port="$1"
  local attempt

  if ! have_cmd curl; then
    return
  fi

  for attempt in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:$port/" >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done

  log "Monitoring webapp did not answer on port $port within the expected time."
  exit 1
}

main() {
  local script_dir root_dir env_file service_name service_path webapp_dir
  local status_host='0.0.0.0'
  local status_port='4000'

  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  root_dir="$(cd -- "$script_dir/.." && pwd -P)"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root)
        root_dir="${2:-}"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        log "Unknown argument: $1"
        usage
        exit 1
        ;;
    esac
  done

  root_dir="$(cd -- "$root_dir" && pwd -P)"
  env_file='/etc/default/server-setup-status-webapp'
  service_name='server-setup-status-webapp.service'
  service_path="/etc/systemd/system/$service_name"
  webapp_dir="$root_dir/monitor/webapp"

  require_root
  ensure_apt
  ensure_nodejs
  build_status_webapp "$webapp_dir"

  log "Writing monitoring webapp environment to $env_file"
  render_status_webapp_env "$root_dir" "$status_host" "$status_port" \
    | write_file "$env_file" 0644

  log "Installing systemd unit at $service_path"
  render_status_webapp_service "$root_dir" "$env_file" \
    | write_file "$service_path" 0644

  log "Enabling monitoring webapp service on port $status_port"
  enable_status_webapp_service "$service_name"
  wait_for_status_webapp "$status_port"

  log 'Monitoring webapp is configured and running.'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
