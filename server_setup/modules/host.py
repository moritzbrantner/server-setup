"""Base operating-system prerequisites and timezone management."""

from __future__ import annotations

from dataclasses import dataclass

from server_setup.config import ServerSetupConfig
from server_setup.modules.base import ModuleApplyError
from server_setup.plan import Change, ChangeKind, ValidationResult, ValidationStatus
from server_setup.system import System, package_installed

BASE_PACKAGES = ("ca-certificates", "curl", "git", "jq", "python3", "iproute2")
SUPPORTED_HOSTS = {("debian", "12"), ("ubuntu", "24.04")}


@dataclass(frozen=True, slots=True)
class HostState:
    os_id: str
    version_id: str
    installed_packages: frozenset[str]
    timezone: str

    @property
    def supported(self) -> bool:
        return (self.os_id, self.version_id) in SUPPORTED_HOSTS


@dataclass(frozen=True, slots=True)
class HostDesired:
    packages: tuple[str, ...]
    timezone: str


def _parse_os_release(text: str | None) -> tuple[str, str]:
    values: dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values.get("ID", "unknown").lower(), values.get("VERSION_ID", "unknown")


class HostModule:
    name = "host"

    def __init__(self, system: System) -> None:
        self.system = system

    def inspect(self) -> HostState:
        os_id, version_id = _parse_os_release(self.system.read_text("/etc/os-release"))
        installed = frozenset(package for package in BASE_PACKAGES if package_installed(self.system, package))
        timezone_result = self.system.run(["timedatectl", "show", "--property=Timezone", "--value"])
        timezone = timezone_result.stdout.strip() if timezone_result.returncode == 0 else ""
        if not timezone:
            timezone = (self.system.read_text("/etc/timezone") or "").strip()
        return HostState(os_id, version_id, installed, timezone)

    def desired(self, config: ServerSetupConfig) -> HostDesired:
        return HostDesired(BASE_PACKAGES, config.host.timezone)

    def plan(self, current: object, desired: object) -> tuple[Change, ...]:
        if not isinstance(current, HostState) or not isinstance(desired, HostDesired):
            raise TypeError("HostModule received incompatible state")
        changes: list[Change] = []
        if not current.supported:
            return (Change(self.name, ChangeKind.DANGEROUS, f"Unsupported host: {current.os_id} {current.version_id}", "PR2 supports Debian 12 and Ubuntu 24.04 only; no host changes will be applied.", action="unsupported-host"),)
        missing = tuple(package for package in desired.packages if package not in current.installed_packages)
        if missing:
            changes.append(Change(self.name, ChangeKind.CREATE, f"Install base packages: {', '.join(missing)}", action="install-packages", target=",".join(missing)))
        if current.timezone != desired.timezone:
            changes.append(Change(self.name, ChangeKind.UPDATE, f"Set host timezone to {desired.timezone}", f"Current timezone: {current.timezone or '<unknown>'}", action="set-timezone", target=desired.timezone))
        return tuple(changes)

    def apply(self, changes: tuple[Change, ...]) -> None:
        for change in changes:
            if change.action == "unsupported-host":
                raise ModuleApplyError(change.summary)
            if change.action == "install-packages":
                packages = [part for part in (change.target or "").split(",") if part]
                if packages:
                    self.system.run(["apt-get", "update", "-y"], check=True)
                    self.system.run(["apt-get", "install", "-y", *packages], check=True, env={"DEBIAN_FRONTEND": "noninteractive"})
            elif change.action == "set-timezone":
                if not change.target:
                    raise ModuleApplyError("Timezone change is missing a target")
                self.system.run(["timedatectl", "set-timezone", change.target], check=True)
            else:
                raise ModuleApplyError(f"Unknown host action: {change.action!r}")

    def validate(self, desired: object) -> tuple[ValidationResult, ...]:
        if not isinstance(desired, HostDesired):
            raise TypeError("HostModule received incompatible desired state")
        current = self.inspect()
        missing = tuple(package for package in desired.packages if package not in current.installed_packages)
        return (
            ValidationResult(self.name, ValidationStatus.PASS if current.supported else ValidationStatus.FAIL, f"Host {current.os_id} {current.version_id} is supported" if current.supported else f"Unsupported host {current.os_id} {current.version_id}", None if current.supported else "Supported in PR2: Debian 12 and Ubuntu 24.04."),
            ValidationResult(self.name, ValidationStatus.PASS if not missing else ValidationStatus.FAIL, "Base packages are installed" if not missing else f"Missing base packages: {', '.join(missing)}"),
            ValidationResult(self.name, ValidationStatus.PASS if current.timezone == desired.timezone else ValidationStatus.FAIL, f"Timezone is {desired.timezone}" if current.timezone == desired.timezone else f"Timezone is {current.timezone or '<unknown>'}, expected {desired.timezone}"),
        )
