"""Opinionated host hardening with explicit dangerous-change boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from server_setup.config import ServerSetupConfig
from server_setup.modules.base import ModuleApplyError
from server_setup.plan import Change, ChangeKind, ValidationResult, ValidationStatus
from server_setup.system import System, package_installed, service_active

AUTO_UPGRADES = 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n'
UNATTENDED_LOCAL = 'Unattended-Upgrade::Automatic-Reboot "false";\nUnattended-Upgrade::Remove-Unused-Dependencies "true";\n'
SSHD_HARDENING = (
    "# Managed by server-setup\n"
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
    "ClientAliveCountMax 2\n"
)
AUTO_UPGRADES_PATH = "/etc/apt/apt.conf.d/20auto-upgrades"
UNATTENDED_LOCAL_PATH = "/etc/apt/apt.conf.d/52unattended-upgrades-local"
FAIL2BAN_PATH = "/etc/fail2ban/jail.d/sshd.local"
SSHD_HARDENING_PATH = "/etc/ssh/sshd_config.d/99-server-setup-hardening.conf"


def _fail2ban_sshd_config(ssh_ports: tuple[int, ...]) -> str:
    ports = ",".join(map(str, ssh_ports))
    return (
        "[sshd]\n"
        "enabled = true\n"
        f"port = {ports}\n"
        "backend = systemd\n"
        "maxretry = 5\n"
        "findtime = 10m\n"
        "bantime = 1h\n"
    )


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
    ssh_ports: tuple[int, ...]


class SecurityModule:
    name = "security"

    def __init__(self, system: System) -> None:
        self.system = system

    def _unattended_ready(self) -> bool:
        return (
            package_installed(self.system, "unattended-upgrades")
            and package_installed(self.system, "apt-listchanges")
            and self.system.read_text(AUTO_UPGRADES_PATH) == AUTO_UPGRADES
            and self.system.read_text(UNATTENDED_LOCAL_PATH) == UNATTENDED_LOCAL
            and service_active(self.system, "unattended-upgrades")
        )

    def _fail2ban_ready(self, ssh_ports: tuple[int, ...]) -> bool:
        return (
            package_installed(self.system, "fail2ban")
            and self.system.read_text(FAIL2BAN_PATH) == _fail2ban_sshd_config(ssh_ports)
            and service_active(self.system, "fail2ban")
        )

    def _ssh_ports(self) -> tuple[int, ...]:
        ports: set[int] = set()
        if self.system.command_exists("sshd"):
            result = self.system.run(["sshd", "-T"])
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    key, _, value = line.partition(" ")
                    if key == "port" and value.strip().isdigit():
                        port = int(value.strip())
                        if 1 <= port <= 65535:
                            ports.add(port)
        connection = self.system.getenv("SSH_CONNECTION").split()
        if len(connection) >= 4 and connection[3].isdigit():
            port = int(connection[3])
            if 1 <= port <= 65535:
                ports.add(port)
        if not ports:
            ports.add(22)
        return tuple(sorted(ports))

    def _firewall_state(self, ssh_ports: tuple[int, ...]) -> tuple[bool, bool]:
        if not package_installed(self.system, "ufw"):
            return False, False
        status = self.system.run(["ufw", "status"]).stdout
        active = "Status: active" in status
        required_rules = tuple(f"{port}/tcp" for port in ssh_ports) + ("80/tcp", "443/tcp")
        rules_present = all(rule in status for rule in required_rules)
        return active and rules_present, active

    def inspect(self) -> SecurityState:
        ssh_ports = self._ssh_ports()
        firewall_ready, firewall_active = self._firewall_state(ssh_ports)
        return SecurityState(
            unattended_ready=self._unattended_ready(),
            fail2ban_ready=self._fail2ban_ready(ssh_ports),
            firewall_ready=firewall_ready,
            firewall_active=firewall_active,
            ssh_hardening_ready=self.system.read_text(SSHD_HARDENING_PATH) == SSHD_HARDENING,
            ssh_ports=ssh_ports,
        )

    def desired(self, config: ServerSetupConfig) -> SecurityDesired:
        return SecurityDesired(
            unattended_upgrades=config.host.unattended_upgrades,
            fail2ban=config.security.fail2ban,
            firewall=config.security.firewall,
            ssh_hardening=config.security.ssh_hardening,
        )

    def plan(self, current: object, desired: object) -> tuple[Change, ...]:
        if not isinstance(current, SecurityState) or not isinstance(desired, SecurityDesired):
            raise TypeError("SecurityModule received incompatible state")
        changes: list[Change] = []
        if desired.unattended_upgrades and not current.unattended_ready:
            changes.append(
                Change(
                    self.name,
                    ChangeKind.UPDATE,
                    "Configure unattended security upgrades",
                    action="configure-unattended",
                )
            )
        if desired.fail2ban and not current.fail2ban_ready:
            changes.append(Change(self.name, ChangeKind.UPDATE, "Configure fail2ban for SSH", action="configure-fail2ban"))
        if desired.firewall and not current.firewall_ready:
            kind = ChangeKind.DANGEROUS if not current.firewall_active else ChangeKind.UPDATE
            changes.append(
                Change(
                    self.name,
                    kind,
                    "Enable and configure UFW" if not current.firewall_active else "Reconcile UFW rules",
                    "Incoming traffic is denied by default; the detected SSH port(s) "
                    f"{', '.join(map(str, current.ssh_ports))} plus TCP 80/443 are allowed before activation.",
                    action="configure-firewall",
                )
            )
        if desired.ssh_hardening and not current.ssh_hardening_ready:
            changes.append(
                Change(
                    self.name,
                    ChangeKind.DANGEROUS,
                    "Harden SSH authentication",
                    "Disables password authentication and root login; a working authorized SSH key is required for remote sessions.",
                    action="configure-ssh",
                )
            )
        return tuple(changes)

    def _ensure_packages(self, packages: tuple[str, ...]) -> None:
        missing = [package for package in packages if not package_installed(self.system, package)]
        if not missing:
            return
        self.system.run(["apt-get", "update", "-y"], check=True)
        self.system.run(
            ["apt-get", "install", "-y", *missing],
            check=True,
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

    def _configure_unattended(self) -> None:
        self._ensure_packages(("unattended-upgrades", "apt-listchanges"))
        self.system.write_text(AUTO_UPGRADES_PATH, AUTO_UPGRADES)
        self.system.write_text(UNATTENDED_LOCAL_PATH, UNATTENDED_LOCAL)
        self.system.run(["systemctl", "enable", "--now", "unattended-upgrades"], check=True)

    def _configure_fail2ban(self) -> None:
        self._ensure_packages(("fail2ban",))
        self.system.write_text(FAIL2BAN_PATH, _fail2ban_sshd_config(self._ssh_ports()))
        self.system.run(["systemctl", "enable", "--now", "fail2ban"], check=True)
        self.system.run(["systemctl", "restart", "fail2ban"], check=True)

    def _configure_firewall(self) -> None:
        self._ensure_packages(("ufw",))
        self.system.run(["ufw", "--force", "default", "deny", "incoming"], check=True)
        self.system.run(["ufw", "--force", "default", "allow", "outgoing"], check=True)
        ssh_ports = self._ssh_ports()
        for rule in (*tuple(f"{port}/tcp" for port in ssh_ports), "80/tcp", "443/tcp"):
            self.system.run(["ufw", "allow", rule], check=True)
        status = self.system.run(["ufw", "status"]).stdout
        if "Status: active" in status:
            self.system.run(["ufw", "reload"], check=True)
        else:
            self.system.run(["ufw", "--force", "enable"], check=True)

    def _ssh_user_home(self) -> str:
        user = self.system.getenv("SUDO_USER") or self.system.getenv("USER", "root") or "root"
        if user == "root":
            return "/root"
        passwd = self.system.read_text("/etc/passwd") or ""
        for line in passwd.splitlines():
            fields = line.split(":")
            if len(fields) >= 6 and fields[0] == user:
                return fields[5]
        return f"/home/{user}"

    def _ensure_ssh_key_safety(self) -> None:
        if not self.system.getenv("SSH_CONNECTION"):
            return
        user = self.system.getenv("SUDO_USER") or self.system.getenv("USER", "root") or "root"
        if user == "root":
            raise ModuleApplyError(
                "Refusing SSH hardening from a remote root session because PermitRootLogin=no would remove the current "
                "reconnect path. Create and verify a non-root sudo user first."
            )
        authorized_keys = f"{self._ssh_user_home()}/.ssh/authorized_keys"
        if not (self.system.read_text(authorized_keys) or "").strip():
            raise ModuleApplyError(
                "Refusing SSH hardening during an SSH session because no authorized_keys entry was found for the current user."
            )

    def _configure_ssh(self) -> None:
        self._ensure_ssh_key_safety()
        if not self.system.command_exists("sshd"):
            raise ModuleApplyError("Cannot harden SSH because sshd is not installed")
        old_content = self.system.read_text(SSHD_HARDENING_PATH)
        self.system.write_text(SSHD_HARDENING_PATH, SSHD_HARDENING)
        test = self.system.run(["sshd", "-t"])
        if test.returncode != 0:
            if old_content is None:
                self.system.remove(SSHD_HARDENING_PATH)
            else:
                self.system.write_text(SSHD_HARDENING_PATH, old_content)
            raise ModuleApplyError(f"sshd configuration validation failed: {test.stderr.strip() or test.stdout.strip()}")
        self.system.run(["systemctl", "reload", "ssh"], check=True)

    def apply(self, changes: tuple[Change, ...]) -> None:
        for change in changes:
            if change.action == "configure-unattended":
                self._configure_unattended()
            elif change.action == "configure-fail2ban":
                self._configure_fail2ban()
            elif change.action == "configure-firewall":
                self._configure_firewall()
            elif change.action == "configure-ssh":
                self._configure_ssh()
            else:
                raise ModuleApplyError(f"Unknown security action: {change.action!r}")

    def validate(self, desired: object) -> tuple[ValidationResult, ...]:
        if not isinstance(desired, SecurityDesired):
            raise TypeError("SecurityModule received incompatible desired state")
        current = self.inspect()
        checks = (
            ("Unattended upgrades", desired.unattended_upgrades, current.unattended_ready),
            ("fail2ban", desired.fail2ban, current.fail2ban_ready),
            ("UFW firewall", desired.firewall, current.firewall_ready),
            ("SSH hardening", desired.ssh_hardening, current.ssh_hardening_ready),
        )
        results: list[ValidationResult] = []
        for label, managed, ready in checks:
            if not managed:
                results.append(ValidationResult(self.name, ValidationStatus.SKIP, f"{label} is not managed"))
            elif ready:
                results.append(ValidationResult(self.name, ValidationStatus.PASS, f"{label} is configured"))
            else:
                results.append(ValidationResult(self.name, ValidationStatus.FAIL, f"{label} is not in the desired state"))
        if desired.firewall and self.system.command_exists("docker"):
            results.append(
                ValidationResult(
                    self.name,
                    ValidationStatus.WARN,
                    "Docker-published ports are not assumed to be protected by UFW",
                    "Use Dokploy/Traefik exposure rules and, where appropriate, a provider firewall for externally published container ports.",
                )
            )
        return tuple(results)
