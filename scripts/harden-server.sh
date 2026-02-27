#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

warn() {
  log "WARNING: $*"
}

die() {
  log "ERROR: $*"
  exit 1
}

usage() {
  cat <<USAGE
Usage: sudo ./scripts/harden-server.sh

Configures baseline host hardening:
  - SSH daemon hardening (including PasswordAuthentication no with safety checks)
  - unattended-upgrades automatic security updates
  - fail2ban SSH protection
  - UFW default deny/allow with SSH, HTTP, HTTPS explicitly allowed
USAGE
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "This script must be run as root (use sudo)."
  fi
}

ensure_apt() {
  command -v apt-get >/dev/null 2>&1 || die "This script currently supports apt-based systems only."
}

install_pkgs() {
  DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
}

restart_or_enable_service() {
  local service_name="$1"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now "$service_name"
    systemctl restart "$service_name"
  else
    warn "systemctl not found; cannot enable/restart $service_name automatically."
  fi
}

current_ssh_user() {
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    printf '%s\n' "$SUDO_USER"
  else
    printf '%s\n' "${USER:-root}"
  fi
}

has_authorized_key_for_user() {
  local user_name="$1"
  local home_dir

  if [[ "$user_name" == "root" ]]; then
    home_dir="/root"
  else
    home_dir="/home/$user_name"
  fi

  [[ -s "$home_dir/.ssh/authorized_keys" ]]
}

ensure_safe_to_disable_password_auth() {
  local ssh_user
  ssh_user="$(current_ssh_user)"

  if [[ -n "${SSH_CONNECTION:-}" ]]; then
    if ! has_authorized_key_for_user "$ssh_user"; then
      cat <<MSG
WARNING: Active SSH session detected (SSH_CONNECTION is set), but no authorized_keys file was found for user '$ssh_user'.
Disabling password SSH login now could lock you out.

Aborting without changing sshd PasswordAuthentication.
Add a working SSH public key to ~$ssh_user/.ssh/authorized_keys and re-run this script.
MSG
      exit 1
    fi
  fi
}

write_sshd_hardening_config() {
  local cfg='/etc/ssh/sshd_config.d/99-server-setup-hardening.conf'

  mkdir -p /etc/ssh/sshd_config.d

  cat > "$cfg" <<'CFG'
# Managed by scripts/harden-server.sh
Protocol 2
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
PermitEmptyPasswords no
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
CFG

  if ! sshd -t; then
    die "sshd configuration test failed. Fix config before restarting sshd."
  fi

  warn "About to apply SSH hardening (including PasswordAuthentication no and PermitRootLogin no)."
  warn "Keep your current SSH session open until you verify a new login works with SSH keys."
  restart_or_enable_service ssh
}

configure_unattended_upgrades() {
  install_pkgs unattended-upgrades apt-listchanges

  cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CFG'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CFG

  cat > /etc/apt/apt.conf.d/52unattended-upgrades-local <<'CFG'
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
CFG

  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now unattended-upgrades || true
  fi
}

configure_fail2ban() {
  install_pkgs fail2ban
  mkdir -p /etc/fail2ban/jail.d

  cat > /etc/fail2ban/jail.d/sshd.local <<'CFG'
[sshd]
enabled = true
port = ssh
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
CFG

  restart_or_enable_service fail2ban
}

ufw_allow_if_missing() {
  local rule="$1"
  if ufw status | grep -Fq "$rule"; then
    log "UFW rule already present: $rule"
  else
    ufw allow "$rule"
  fi
}

configure_ufw() {
  install_pkgs ufw

  ufw --force default deny incoming
  ufw --force default allow outgoing

  ufw_allow_if_missing OpenSSH
  ufw_allow_if_missing 80/tcp
  ufw_allow_if_missing 443/tcp

  if ufw status | grep -q '^Status: active'; then
    log 'UFW already active; reloading rules.'
    ufw reload
  else
    warn "About to enable UFW with default deny incoming."
    warn "SSH (OpenSSH), HTTP (80), and HTTPS (443) are explicitly allowed before enable."
    ufw --force enable
  fi
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  require_root
  ensure_apt

  log 'Refreshing apt package index'
  apt-get update -y

  ensure_safe_to_disable_password_auth
  write_sshd_hardening_config

  configure_unattended_upgrades
  configure_fail2ban
  configure_ufw

  log 'Hardening complete.'
}

main "$@"
