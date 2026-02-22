#!/usr/bin/env bash
set -euo pipefail

# Idempotent server bootstrap for common tooling.
# Supports Debian/Ubuntu (apt), Fedora/RHEL (dnf/yum), Alpine (apk), Arch (pacman), and openSUSE (zypper).

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

refresh_pkg_index() {
  case "$PKG_MGR" in
    apt)
      $SUDO apt-get update -y
      ;;
    dnf)
      $SUDO dnf makecache -y
      ;;
    yum)
      $SUDO yum makecache -y
      ;;
    apk)
      $SUDO apk update
      ;;
    pacman)
      $SUDO pacman -Sy --noconfirm
      ;;
    zypper)
      $SUDO zypper --gpg-auto-import-keys refresh
      ;;
    *)
      log "Unsupported package manager: $PKG_MGR"
      exit 1
      ;;
  esac
}

upgrade_system_packages() {
  case "$PKG_MGR" in
    apt)
      DEBIAN_FRONTEND=noninteractive $SUDO apt-get upgrade -y
      ;;
    dnf)
      $SUDO dnf upgrade --refresh -y
      ;;
    yum)
      $SUDO yum update -y
      ;;
    apk)
      $SUDO apk upgrade
      ;;
    pacman)
      $SUDO pacman -Syu --noconfirm
      ;;
    zypper)
      $SUDO zypper update -y
      ;;
  esac
}

install_pkgs() {
  local -a pkgs=("$@")
  case "$PKG_MGR" in
    apt)
      DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "${pkgs[@]}"
      ;;
    dnf)
      $SUDO dnf install -y "${pkgs[@]}"
      ;;
    yum)
      $SUDO yum install -y "${pkgs[@]}"
      ;;
    apk)
      $SUDO apk add --no-cache "${pkgs[@]}"
      ;;
    pacman)
      $SUDO pacman -S --needed --noconfirm "${pkgs[@]}"
      ;;
    zypper)
      $SUDO zypper install -y --no-confirm "${pkgs[@]}"
      ;;
  esac
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

  # Ensure bun is in PATH for current and future non-login shells.
  if [[ -d "$HOME/.bun/bin" ]]; then
    export PATH="$HOME/.bun/bin:$PATH"
  fi
}

install_or_update_gh() {
  if have_cmd gh; then
    log "Updating GitHub CLI via package manager"
    install_pkgs gh
    return
  fi

  case "$PKG_MGR" in
    apt)
      install_pkgs gh
      ;;
    dnf|yum|apk|pacman|zypper)
      install_pkgs gh
      ;;
    *)
      log "Unable to install gh for package manager: $PKG_MGR"
      exit 1
      ;;
  esac
}

detect_package_manager() {
  if have_cmd apt-get; then
    PKG_MGR='apt'
  elif have_cmd dnf; then
    PKG_MGR='dnf'
  elif have_cmd yum; then
    PKG_MGR='yum'
  elif have_cmd apk; then
    PKG_MGR='apk'
  elif have_cmd pacman; then
    PKG_MGR='pacman'
  elif have_cmd zypper; then
    PKG_MGR='zypper'
  else
    log 'No supported package manager found (apt/dnf/yum/apk/pacman/zypper).'
    exit 1
  fi
}

main() {
  require_root
  detect_package_manager

  log "Using package manager: $PKG_MGR"
  log 'Refreshing package indexes'
  refresh_pkg_index

  log 'Upgrading installed system packages'
  upgrade_system_packages

  # Baseline tooling; includes postgres server/client, bun prerequisites, gh prerequisites,
  # and other common server helpers.
  case "$PKG_MGR" in
    apt)
      install_pkgs ca-certificates curl git jq unzip build-essential postgresql postgresql-client
      ;;
    dnf|yum)
      install_pkgs ca-certificates curl git jq unzip gcc gcc-c++ make postgresql-server postgresql
      ;;
    apk)
      install_pkgs ca-certificates curl git jq unzip build-base postgresql postgresql-client
      ;;
    pacman)
      install_pkgs ca-certificates curl git jq unzip base-devel postgresql
      ;;
    zypper)
      install_pkgs ca-certificates curl git jq unzip gcc gcc-c++ make postgresql-server postgresql
      ;;
  esac

  install_or_update_bun
  install_or_update_gh
  ensure_postgres_enabled

  log 'Finished: tools and postgres are installed and updated.'
}

main "$@"
