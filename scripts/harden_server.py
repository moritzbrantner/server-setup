#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {message}")


def warn(message: str) -> None:
    log(f"WARNING: {message}")


def die(message: str) -> None:
    log(f"ERROR: {message}")
    raise SystemExit(1)


def run_checked(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    allow_fail: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, text=True, capture_output=True, env=env, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0 and not allow_fail:
        raise SystemExit(result.returncode)
    return result


def require_root() -> None:
    if os.geteuid() != 0:
        die("This script must be run as root (use sudo).")


def install_pkgs(packages: list[str]) -> None:
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    run_checked(["apt-get", "install", "-y", *packages], env=env)


def restart_or_enable_service(service_name: str) -> None:
    if shutil.which("systemctl"):
        run_checked(["systemctl", "enable", "--now", service_name], allow_fail=True)
        run_checked(["systemctl", "restart", service_name], allow_fail=True)
    else:
        warn(f"systemctl not found; cannot enable/restart {service_name} automatically.")


def current_ssh_user() -> str:
    sudo_user = os.environ.get("SUDO_USER", "")
    if sudo_user and sudo_user != "root":
        return sudo_user
    return os.environ.get("USER", "root")


def has_authorized_key_for_user(user_name: str) -> bool:
    home_dir = Path("/root" if user_name == "root" else f"/home/{user_name}")
    return (home_dir / ".ssh/authorized_keys").is_file() and (home_dir / ".ssh/authorized_keys").stat().st_size > 0


def ensure_safe_to_disable_password_auth() -> None:
    ssh_user = current_ssh_user()
    if os.environ.get("SSH_CONNECTION") and not has_authorized_key_for_user(ssh_user):
        print(
            "WARNING: Active SSH session detected (SSH_CONNECTION is set), but no authorized_keys file was found "
            f"for user '{ssh_user}'.\n"
            "Disabling password SSH login now could lock you out.\n\n"
            "Aborting without changing sshd PasswordAuthentication.\n"
            f"Add a working SSH public key to ~{ssh_user}/.ssh/authorized_keys and re-run this script."
        )
        raise SystemExit(1)


def write_sshd_hardening_config() -> None:
    cfg = Path("/etc/ssh/sshd_config.d/99-server-setup-hardening.conf")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "# Managed by scripts/harden_server.py\n"
        "Protocol 2\n"
        "PasswordAuthentication no\n"
        "KbdInteractiveAuthentication no\n"
        "ChallengeResponseAuthentication no\n"
        "PubkeyAuthentication yes\n"
        "PermitRootLogin no\n"
        "PermitEmptyPasswords no\n"
        "X11Forwarding no\n"
        "MaxAuthTries 3\n"
        "LoginGraceTime 30\n"
        "ClientAliveInterval 300\n"
        "ClientAliveCountMax 2\n",
        encoding="utf-8",
    )
    if run_checked(["sshd", "-t"], allow_fail=True).returncode != 0:
        die("sshd configuration test failed. Fix config before restarting sshd.")
    warn("About to apply SSH hardening (including PasswordAuthentication no and PermitRootLogin no).")
    warn("Keep your current SSH session open until you verify a new login works with SSH keys.")
    restart_or_enable_service("ssh")


def configure_unattended_upgrades() -> None:
    install_pkgs(["unattended-upgrades", "apt-listchanges"])
    Path("/etc/apt/apt.conf.d/20auto-upgrades").write_text(
        'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n',
        encoding="utf-8",
    )
    Path("/etc/apt/apt.conf.d/52unattended-upgrades-local").write_text(
        'Unattended-Upgrade::Automatic-Reboot "false";\nUnattended-Upgrade::Remove-Unused-Dependencies "true";\n',
        encoding="utf-8",
    )
    if shutil.which("systemctl"):
        run_checked(["systemctl", "enable", "--now", "unattended-upgrades"], allow_fail=True)


def configure_fail2ban() -> None:
    install_pkgs(["fail2ban"])
    jail_dir = Path("/etc/fail2ban/jail.d")
    jail_dir.mkdir(parents=True, exist_ok=True)
    (jail_dir / "sshd.local").write_text(
        "[sshd]\n"
        "enabled = true\n"
        "port = ssh\n"
        "backend = systemd\n"
        "maxretry = 5\n"
        "findtime = 10m\n"
        "bantime = 1h\n",
        encoding="utf-8",
    )
    restart_or_enable_service("fail2ban")


def ufw_allow_if_missing(rule: str) -> None:
    status = run_checked(["ufw", "status"], allow_fail=True).stdout
    if rule in status:
        log(f"UFW rule already present: {rule}")
    else:
        run_checked(["ufw", "allow", rule])


def configure_ufw() -> None:
    install_pkgs(["ufw"])
    run_checked(["ufw", "--force", "default", "deny", "incoming"])
    run_checked(["ufw", "--force", "default", "allow", "outgoing"])
    for rule in ("OpenSSH", "80/tcp", "443/tcp"):
        ufw_allow_if_missing(rule)
    status = run_checked(["ufw", "status"], allow_fail=True).stdout
    if "Status: active" in status:
        log("UFW already active; reloading rules.")
        run_checked(["ufw", "reload"])
    else:
        warn("About to enable UFW with default deny incoming.")
        warn("SSH (OpenSSH), HTTP (80), and HTTPS (443) are explicitly allowed before enable.")
        run_checked(["ufw", "--force", "enable"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure baseline host hardening.")
    parser.add_argument(
        "--configure-ssh",
        action="store_true",
        help="Also manage sshd settings. Default behavior leaves SSH unchanged.",
    )
    args = parser.parse_args()
    require_root()
    if shutil.which("apt-get") is None:
        die("This script currently supports apt-based systems only.")
    log("Refreshing apt package index")
    run_checked(["apt-get", "update", "-y"])
    if args.configure_ssh:
        ensure_safe_to_disable_password_auth()
        write_sshd_hardening_config()
    else:
        log("Leaving SSH configuration unchanged. Re-run with --configure-ssh to manage sshd settings.")
    configure_unattended_upgrades()
    configure_fail2ban()
    configure_ufw()
    log("Hardening complete.")


if __name__ == "__main__":
    main()
