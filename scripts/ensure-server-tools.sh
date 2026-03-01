#!/usr/bin/env bash
set -euo pipefail

# Idempotent server bootstrap for common tooling on Ubuntu LTS.

log() {
  printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

docker_package_name() {
  case "$PKG_MGR" in
    apt)
      printf 'docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin'
      ;;
    dnf|yum)
      printf 'docker'
      ;;
    apk)
      printf 'docker'
      ;;
    pacman)
      printf 'docker'
      ;;
    zypper)
      printf 'docker'
      ;;
    *)
      return 1
      ;;
  esac
}

require_root() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    if have_cmd sudo; then
      SUDO='sudo'
    else
      log "This script must run as root or with sudo available."
      exit 1
    fi
  else
    SUDO=''
  fi
}

ensure_apt() {
  if ! have_cmd apt-get; then
    log 'This script supports Ubuntu LTS (apt) only.'
    exit 1
  fi
}

refresh_pkg_index() {
  $SUDO apt-get update -y
}

upgrade_system_packages() {
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get upgrade -y
}

install_pkgs() {
  local -a pkgs=("$@")
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "${pkgs[@]}"
}

configure_docker_repo_for_apt() {
  local os_id codename arch keyring_path repo_file

  if [[ ! -f /etc/os-release ]]; then
    log 'Cannot configure Docker apt repository: /etc/os-release missing.'
    exit 1
  fi

  # shellcheck source=/etc/os-release
  . /etc/os-release
  os_id="${ID:-}"
  codename="${VERSION_CODENAME:-}"
  arch="$($SUDO dpkg --print-architecture)"

  if [[ "$os_id" != 'ubuntu' && "$os_id" != 'debian' ]]; then
    log "Apt detected but unsupported distro id for Docker CE repo: '$os_id'."
    exit 1
  fi

  if [[ -z "$codename" ]]; then
    codename="$(lsb_release -cs 2>/dev/null || true)"
  fi

  if [[ -z "$codename" ]]; then
    log 'Unable to determine distro codename for Docker apt repository setup.'
    exit 1
  fi

  keyring_path='/etc/apt/keyrings/docker.asc'
  repo_file='/etc/apt/sources.list.d/docker.list'

  install_pkgs ca-certificates curl gnupg
  $SUDO install -m 0755 -d /etc/apt/keyrings

  if [[ ! -s "$keyring_path" ]]; then
    curl -fsSL "https://download.docker.com/linux/$os_id/gpg" | $SUDO tee "$keyring_path" >/dev/null
    $SUDO chmod a+r "$keyring_path"
  fi

  $SUDO tee "$repo_file" >/dev/null <<EOF
deb [arch=$arch signed-by=$keyring_path] https://download.docker.com/linux/$os_id $codename stable
EOF

  $SUDO apt-get update -y
}

validate_docker_install() {
  if ! command -v docker >/dev/null 2>&1; then
    log 'Docker validation failed: docker binary not found in PATH.'
    exit 1
  fi

  if have_cmd systemctl; then
    if ! systemctl is-active --quiet docker; then
      log 'Docker validation failed: docker service is not active.'
      exit 1
    fi
  else
    log 'Skipping docker service active check because systemctl is unavailable.'
  fi
}

install_and_enable_docker() {
  local docker_pkgs

  if ! docker_pkgs="$(docker_package_name)"; then
    log "Docker installation is not supported for package manager: $PKG_MGR"
    exit 1
  fi

  log 'Ensuring Docker engine is installed'
  if [[ "$PKG_MGR" == 'apt' ]]; then
    configure_docker_repo_for_apt
  fi

  # shellcheck disable=SC2206
  local -a pkg_array=($docker_pkgs)
  install_pkgs "${pkg_array[@]}"

  if have_cmd systemctl; then
    $SUDO systemctl enable --now docker
  else
    log 'Skipping docker enable/start because systemctl is unavailable.'
  fi

  validate_docker_install
}

ensure_postgres_enabled() {
  if have_cmd systemctl; then
    if systemctl list-unit-files | grep -qE '^postgresql(\.service)?'; then
      $SUDO systemctl enable --now postgresql || true
    elif systemctl list-unit-files | grep -qE '^postgresql-[0-9]+\.service'; then
      local unit
      unit=$(systemctl list-unit-files | awk '/^postgresql-[0-9]+\.service/ {print $1; exit}')
      if [[ -n "$unit" ]]; then
        $SUDO systemctl enable --now "$unit" || true
      fi
    fi
  fi
}

install_or_update_bun() {
  if have_cmd bun; then
    log "Updating bun"
    bun upgrade || true
  else
    log "Installing bun"
    curl -fsSL https://bun.sh/install | bash
  fi

  if [[ -d "$HOME/.bun/bin" ]]; then
    export PATH="$HOME/.bun/bin:$PATH"
  fi
}

install_or_update_gh() {
  if have_cmd gh; then
    log "Updating GitHub CLI via apt"
  else
    log "Installing GitHub CLI via apt"
  fi
  install_pkgs gh
}

SKIP_DOCKER=0

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --skip-docker)
        SKIP_DOCKER=1
        shift
        ;;
      -h|--help)
        cat <<USAGE
Usage: ./scripts/ensure-server-tools.sh [--skip-docker]

Options:
  --skip-docker   Skip Docker installation/enabling step.
  -h, --help      Show this help text.
USAGE
        exit 0
        ;;
      *)
        log "Unknown argument: $1"
        exit 1
        ;;
    esac
  done
}

main() {
  parse_args "$@"
  require_root
  ensure_apt

  log 'Using package manager: apt'
  log 'Refreshing package indexes'
  refresh_pkg_index

  log 'Upgrading installed system packages'
  upgrade_system_packages

  install_pkgs ca-certificates curl git jq unzip build-essential postgresql postgresql-client inotify-tools

  install_or_update_bun
  install_or_update_gh
  ensure_postgres_enabled

  if [[ "$SKIP_DOCKER" -eq 1 ]]; then
    log 'Skipping Docker installation because --skip-docker was supplied.'
  else
    install_and_enable_docker
  fi

  log 'Finished: tools, postgres, and docker bootstrap steps are complete.'
}

main "$@"
