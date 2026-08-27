"""Dokploy host installation and health verification."""

from __future__ import annotations

import re
from dataclasses import dataclass

from server_setup.config import ServerSetupConfig
from server_setup.modules.base import ModuleApplyError
from server_setup.plan import Change, ChangeKind, ValidationResult, ValidationStatus
from server_setup.system import System

INSTALL_PATH = "/tmp/server-setup-dokploy-install.sh"


@dataclass(frozen=True, slots=True)
class DokployDesired:
    enabled: bool
    version: str

@dataclass(frozen=True, slots=True)
class DokployState:
    installed: bool
    version: str | None
    swarm_active: bool
    network_exists: bool
    ports: frozenset[int]
    @property
    def healthy(self) -> bool: return self.installed and self.swarm_active and self.network_exists and {80, 443}.issubset(self.ports)


def _listening_ports(text: str) -> frozenset[int]:
    ports: set[int] = set()
    for line in text.splitlines():
        for match in re.finditer(r":(\d+)(?:\s|$)", line):
            try: ports.add(int(match.group(1)))
            except ValueError: continue
    return frozenset(ports)


def _image_version(image: str) -> str | None:
    without_digest = image.strip().split("@", 1)[0]
    if ":" not in without_digest: return None
    candidate = without_digest.rsplit(":", 1)[1]
    return candidate if candidate.startswith("v") else None


class DokployModule:
    name = "dokploy"
    def __init__(self, system: System) -> None: self.system = system

    def _service_image(self) -> str | None:
        if not self.system.command_exists("docker"): return None
        result = self.system.run(["docker", "service", "inspect", "dokploy", "--format", "{{.Spec.TaskTemplate.ContainerSpec.Image}}"])
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None

    def _installed(self) -> bool:
        if not self.system.command_exists("docker"): return False
        if self.system.run(["docker", "service", "inspect", "dokploy"]).returncode == 0: return True
        names = self.system.run(["docker", "ps", "--format", "{{.Names}}"])
        return names.returncode == 0 and any("dokploy" in name for name in names.stdout.splitlines())

    def inspect(self) -> DokployState:
        installed = self._installed()
        version = _image_version(self._service_image() or "") if installed else None
        swarm_active = network_exists = False
        if self.system.command_exists("docker"):
            swarm = self.system.run(["docker", "info", "--format", "{{.Swarm.LocalNodeState}}"])
            swarm_active = swarm.returncode == 0 and swarm.stdout.strip() == "active"
            network_exists = self.system.run(["docker", "network", "inspect", "dokploy-network"]).returncode == 0
        sockets = self.system.run(["ss", "-H", "-ltn"])
        ports = _listening_ports(sockets.stdout) if sockets.returncode == 0 else frozenset()
        return DokployState(installed, version, swarm_active, network_exists, ports)

    def desired(self, config: ServerSetupConfig) -> DokployDesired: return DokployDesired(config.dokploy.enabled, config.dokploy.version)

    def plan(self, current: object, desired: object) -> tuple[Change, ...]:
        if not isinstance(current, DokployState) or not isinstance(desired, DokployDesired): raise TypeError("DokployModule received incompatible state")
        if not desired.enabled: return ()
        if not current.installed:
            occupied = sorted({80, 443, 3000}.intersection(current.ports))
            if occupied: return (Change(self.name, ChangeKind.DANGEROUS, f"Dokploy installation blocked by occupied ports: {', '.join(map(str, occupied))}", "Free ports 80, 443, and 3000 before installation.", action="blocked"),)
            if current.swarm_active: return (Change(self.name, ChangeKind.DANGEROUS, "Dokploy installation blocked by an existing Docker Swarm", "The standard Dokploy installer reinitializes Swarm; migrate or install Dokploy manually instead.", action="blocked"),)
            return (Change(self.name, ChangeKind.CREATE, f"Install Dokploy {desired.version}", action="install", target=desired.version),)
        if not current.healthy: return (Change(self.name, ChangeKind.DANGEROUS, "Dokploy is installed but its host prerequisites are unhealthy", "Run server-setup doctor before attempting an update or repair.", action="blocked"),)
        if current.version and current.version != desired.version: return (Change(self.name, ChangeKind.DANGEROUS, f"Update Dokploy from {current.version} to {desired.version}", action="update", target=desired.version),)
        return ()

    def _download_installer(self, version: str) -> None:
        url = f"https://github.com/Dokploy/dokploy/releases/download/{version}/install.sh"
        self.system.remove(INSTALL_PATH)
        self.system.run(["curl", "-fsSL", url, "-o", INSTALL_PATH], check=True)

    def _wait_until_healthy(self) -> None:
        for _ in range(60):
            if self.inspect().healthy: return
            self.system.sleep(2)
        raise ModuleApplyError("Dokploy did not become healthy after installation/update")

    def apply(self, changes: tuple[Change, ...]) -> None:
        for change in changes:
            if change.action == "blocked": raise ModuleApplyError(change.summary)
            if change.action not in {"install", "update"} or not change.target: raise ModuleApplyError(f"Unknown Dokploy action: {change.action!r}")
            if change.action == "install":
                fresh = self.inspect()
                occupied = sorted({80, 443, 3000}.intersection(fresh.ports))
                if occupied: raise ModuleApplyError(f"Dokploy install blocked: ports now occupied: {', '.join(map(str, occupied))}")
                if fresh.swarm_active and not fresh.installed: raise ModuleApplyError("Dokploy install blocked: an unrelated Docker Swarm is active")
            self._download_installer(change.target)
            try:
                command = ["bash", INSTALL_PATH]
                if change.action == "update": command.extend(["-s", "update"])
                self.system.run(command, check=True)
                self._wait_until_healthy()
            finally: self.system.remove(INSTALL_PATH)

    def validate(self, desired: object) -> tuple[ValidationResult, ...]:
        if not isinstance(desired, DokployDesired): raise TypeError("DokployModule received incompatible desired state")
        if not desired.enabled: return (ValidationResult(self.name, ValidationStatus.SKIP, "Dokploy is not managed"),)
        current = self.inspect()
        results = [ValidationResult(self.name, ValidationStatus.PASS if current.installed else ValidationStatus.FAIL, "Dokploy is installed" if current.installed else "Dokploy is not installed")]
        if current.installed:
            if current.version is None: results.append(ValidationResult(self.name, ValidationStatus.WARN, "Dokploy version could not be determined", f"Expected pinned version {desired.version}."))
            else: results.append(ValidationResult(self.name, ValidationStatus.PASS if current.version == desired.version else ValidationStatus.FAIL, f"Dokploy version is {current.version}", None if current.version == desired.version else f"Expected {desired.version}."))
            results.append(ValidationResult(self.name, ValidationStatus.PASS if current.swarm_active else ValidationStatus.FAIL, "Docker Swarm is active" if current.swarm_active else "Docker Swarm is not active"))
            results.append(ValidationResult(self.name, ValidationStatus.PASS if current.network_exists else ValidationStatus.FAIL, "dokploy-network exists" if current.network_exists else "dokploy-network is missing"))
            edge_ready = {80, 443}.issubset(current.ports)
            results.append(ValidationResult(self.name, ValidationStatus.PASS if edge_ready else ValidationStatus.FAIL, "Dokploy edge is listening on 80/443" if edge_ready else "Dokploy edge is not listening on both 80 and 443"))
            if 3000 not in current.ports: results.append(ValidationResult(self.name, ValidationStatus.WARN, "Dokploy port 3000 is not directly exposed", "This is expected after securing the admin UI behind an HTTPS domain."))
        return tuple(results)
