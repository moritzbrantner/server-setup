"""Opinionated host hardening with explicit dangerous-change boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from server_setup.config import ServerSetupConfig
from server_setup.modules.base import ModuleApplyError
from server_setup.plan import Change, ChangeKind, ValidationResult, ValidationStatus
from server_setup.system import System, package_installed, service_active

AUTO_UPGRADES = 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n'
UNATTENDED_LOCAL = 'Unattended-Upgrade::Automatic-Reboot "false";\nUnattended-Upgrade::Remove-Unused-Dependencies "true";\n'
FAIL2BAN_SSHD = "[sshd]\nenabled = true\nport = ssh\nbackend = systemd\nmaxretry = 5\nfindtime = 10m\nbantime = 1h\n"
SSHD_HARDENING = "# Managed by server-setup\nProtocol 2\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nChallengeResponseAuthentication no\nPubkeyAuthentication yes\nPermitRootLogin no\nPermitEmptyPasswords no\nX11Forwarding no\nMaxAuthTries 3\nLoginGraceTime 30\nClientAliveInterval 300\nClientAliveCountMax 2\n"
AUTO_UPGRADES_PATH = "/etc/apt/apt.conf.d/20auto-upgrades"
UNATTENDED_LOCAL_PATH = "/etc/apt/apt.conf.d/52unattended-upgrades-local"
FAIL2BAN_PATH = "/etc/fail2ban/jail.d/sshd.local"
SSHD_HARDENING_PATH = "/etc/ssh/sshd_config.d/99-server-setup-hardening.conf"


@dataclass(frozen=True, slots=True)
class SecurityDesired:
    unattended_upgrades: bool
    fail2ban: bool
    firewall: bool
    ssh_hardening: bool


@dataclass(frozen=True, slots=True)
class SecurityState:
    unattended_ready: bool
    fail2ban_ready: bool
    firewall_ready: bool
    firewall_active: bool
    ssh_hardening_ready: bool


class SecurityModule:
    name = "security"
    def __init__(self, system: System) -> None: self.system = system

    def _unattended_ready(self) -> bool:
        return package_installed(self.system, "unattended-upgrades") and package_installed(self.system, "apt-listchanges") and self.system.read_text(AUTO_UPGRADES_PATH) == AUTO_UPGRADES and self.system.read_text(UNATTENDED_LOCAL_PATH) == UNATTENDED_LOCAL and service_active(self.system, "unattended-upgrades")

    def _fail2ban_ready(self) -> bool:
        return package_installed(self.system, "fail2ban") and self.system.read_text(FAIL2BAN_PATH) == FAIL2BAN_SSHD and service_active(self.system, "fail2ban")

    def _firewall_state(self) -> tuple[bool, bool]:
        if not package_installed(self.system, "ufw"): return False, False
        status = self.system.run(["ufw", "status"]).stdout
        active = "Status: active" in status
        return active and all(port in status for port in ("22/tcp", "80/tcp", "443/tcp")), active

    def inspect(self) -> SecurityState:
        firewall_ready, firewall_active = self._firewall_state()
        return SecurityState(self._unattended_ready(), self._fail2ban_ready(), firewall_ready, firewall_active, self.system.read_text(SSHD_HARDENING_PATH) == SSHD_HARDENING)

    def desired(self, config: ServerSetupConfig) -> SecurityDesired:
        return SecurityDesired(config.host.unattended_upgrades, config.security.fail2ban, config.security.firewall, config.security.ssh_hardening)

    def plan(self, current: object, desired: object) -> tuple[Change, ...]:
        if not isinstance(current, SecurityState) or not isinstance(desired, SecurityDesired): raise TypeError("SecurityModule received incompatible state")
        changes: list[Change] = []
        if desired.unattended_upgrades and not current.unattended_ready: changes.append(Change(self.name, ChangeKind.UPDATE, "Configure unattended security upgrades", action="configure-unattended"))
        if desired.fail2ban and not current.fail2ban_ready: changes.append(Change(self.name, ChangeKind.UPDATE, "Configure fail2ban for SSH", action="configure-fail2ban"))
        if desired.firewall and not current.firewall_ready:
            kind = ChangeKind.DANGEROUS if not current.firewall_active else ChangeKind.UPDATE
            changes.append(Change(self.name, kind, "Enable and configure UFW" if not current.firewall_active else "Reconcile UFW rules", "Incoming traffic is denied by default; TCP 22, 80, and 443 are allowed before activation.", action="configure-firewall"))
        if desired.ssh_hardening and not current.ssh_hardening_ready:
            changes.append(Change(self.name, ChangeKind.DANGEROUS, "Harden SSH authentication", "Disables password authentication and root login; a working authorized SSH key is required for remote sessions.", action="configure-ssh"))
        return tuple(changes)

    def _ensure_packages(self, packages: tuple[str, ...]) -> None:
        missing = [p for p in packages if not package_installed(self.system, p)]
        if missing:
            self.system.run(["apt-get", "update", "-y"], check=True)
            self.system.run(["apt-get", "install", "-y", *missing], check=True, env={"DEBIAN_FRONTEND": "noninteractive"})

    def _configure_unattended(self) -> None:
        self._ensure_packages(("unattended-upgrades", "apt-listchanges"))
        self.system.write_text(AUTO_UPGRADES_PATH, AUTO_UPGRADES)
        self.system.write_text(UNATTENDED_LOCAL_PATH, UNATTENDED_LOCAL)
        self.system.run(["systemctl", "enable", "--now", "unattended-upgrades"], check=True)

    def _configure_fail2ban(self) -> None:
        self._ensure_packages(("fail2ban",))
        self.system.write_text(FAIL2BAN_PATH, FAIL2BAN_SSHD)
        self.system.run(["systemctl", "enable", "--now", "fail2ban"], check=True)
        self.system.run(["systemctl", "restart", "fail2ban"], check=True)

    def _configure_firewall(self) -> None:
        self._ensure_packages(("ufw",))
        self.system.run(["ufw", "--force", "default", "deny", "incoming"], check=True)
        self.system.run(["ufw", "--force", "default", "allow", "outgoing"], check=True)
        for port in ("22/tcp", "80/tcp", "443/tcp"): self.system.run(["ufw", "allow", port], check=True)
        if "Status: active" in self.system.run(["ufw", "status"]).stdout: self.system.run(["ufw", "reload"], check=True)
        else: self.system.run(["ufw", "--force", "enable"], check=True)

    def _ssh_user_home(self) -> str:
        user = self.system.getenv("SUDO_USER") or self.system.getenv("USER", "root") or "root"
        if user == "root": return "/root"
        for line in (self.system.read_text("/etc/passwd") or "").splitlines():
            fields = line.split(":")
            if len(fields) >= 6 and fields[0] == user: return fields[5]
        return f"/home/{user}"

    def _configure_ssh(self) -> None:
        if self.system.getenv("SSH_CONNECTION") and not (self.system.read_text(f"{self._ssh_user_home()}/.ssh/authorized_keys") or "").strip():
            raise ModuleApplyError("Refusing SSH hardening during an SSH session because no authorized_keys entry was found for the current user.")
        if not self.system.command_exists("sshd"): raise ModuleApplyError("Cannot harden SSH because sshd is not installed")
        old = self.system.read_text(SSHD_HARDENING_PATH)
        self.system.write_text(SSHD_HARDENING_PATH, SSHD_HARDENING)
        test = self.system.run(["sshd", "-t"])
        if test.returncode != 0:
            if old is None: self.system.remove(SSHD_HARDENING_PATH)
            else: self.system.write_text(SSHD_HARDENING_PATH, old)
            raise ModuleApplyError(f"sshd configuration validation failed: {test.stderr.strip() or test.stdout.strip()}")
        self.system.run(["systemctl", "reload", "ssh"], check=True)

    def apply(self, changes: tuple[Change, ...]) -> None:
        actions = {"configure-unattended": self._configure_unattended, "configure-fail2ban": self._configure_fail2ban, "configure-firewall": self._configure_firewall, "configure-ssh": self._configure_ssh}
        for change in changes:
            action = actions.get(change.action or "")
            if action is None: raise ModuleApplyError(f"Unknown security action: {change.action!r}")
            action()

    def validate(self, desired: object) -> tuple[ValidationResult, ...]:
        if not isinstance(desired, SecurityDesired): raise TypeError("SecurityModule received incompatible desired state")
        current = self.inspect()
        checks = (("Unattended upgrades", desired.unattended_upgrades, current.unattended_ready), ("fail2ban", desired.fail2ban, current.fail2ban_ready), ("UFW firewall", desired.firewall, current.firewall_ready), ("SSH hardening", desired.ssh_hardening, current.ssh_hardening_ready))
        results: list[ValidationResult] = []
        for label, managed, ready in checks:
            if not managed: results.append(ValidationResult(self.name, ValidationStatus.SKIP, f"{label} is not managed"))
            elif ready: results.append(ValidationResult(self.name, ValidationStatus.PASS, f"{label} is configured"))
            else: results.append(ValidationResult(self.name, ValidationStatus.FAIL, f"{label} is not in the desired state"))
        if desired.firewall and self.system.command_exists("docker"):
            results.append(ValidationResult(self.name, ValidationStatus.WARN, "Docker-published ports are not assumed to be protected by UFW", "Use Dokploy/Traefik exposure rules and, where appropriate, a provider firewall for externally published container ports."))
        return tuple(results)
