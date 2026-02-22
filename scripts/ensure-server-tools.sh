#!/usr/bin/env bash
set -euo pipefail

# Idempotent server bootstrap for common tooling on Ubuntu LTS.

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

main() {
  require_root
  ensure_apt

  log 'Using package manager: apt'
  log 'Refreshing package indexes'
  refresh_pkg_index

  log 'Upgrading installed system packages'
  upgrade_system_packages

  install_pkgs ca-certificates curl git jq unzip build-essential postgresql postgresql-client

  install_or_update_bun
  install_or_update_gh
  ensure_postgres_enabled

  log 'Finished: tools and postgres are installed and updated.'
}

main "$@"
