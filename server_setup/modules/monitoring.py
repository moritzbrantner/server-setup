"""Optional Uptime Kuma and Beszel host monitoring via the bundled Compose model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from server_setup.config import ServerSetupConfig
from server_setup.modules.base import ModuleApplyError
from server_setup.plan import Change, ChangeKind, ValidationResult, ValidationStatus
from server_setup.system import System

INSTALL_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = INSTALL_ROOT / "services"
COMPOSE_PATH = SERVICES_DIR / "compose.yml"
ENV_PATH = SERVICES_DIR / ".env"

_SERVICE_BY_FIELD = {
    "uptime_kuma": "uptime-kuma",
    "beszel": "beszel",
}


@dataclass(frozen=True, slots=True)
class MonitoringDesired:
    uptime_kuma: bool
    beszel: bool


@dataclass(frozen=True, slots=True)
class MonitoringState:
    bundle_present: bool
    compose_available: bool
    running_services: frozenset[str]


def _compose_prefix(system: System) -> list[str]:
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(SERVICES_DIR),
        "-f",
        str(COMPOSE_PATH),
    ]
    if system.exists(ENV_PATH):
        command.extend(["--env-file", str(ENV_PATH)])
    return command


class MonitoringModule:
    name = "monitoring"

    def __init__(self, system: System) -> None:
        self.system = system

    def _compose_available(self) -> bool:
        if not self.system.command_exists("docker"):
            return False
        return self.system.run(["docker", "compose", "version"]).returncode == 0

    def inspect(self) -> MonitoringState:
        bundle_present = self.system.exists(COMPOSE_PATH)
        compose_available = bundle_present and self._compose_available()
        running: frozenset[str] = frozenset()
        if compose_available:
            result = self.system.run([*_compose_prefix(self.system), "ps", "--status", "running", "--services"])
            if result.returncode == 0:
                running = frozenset(line.strip() for line in result.stdout.splitlines() if line.strip())
        return MonitoringState(bundle_present, compose_available, running)

    def desired(self, config: ServerSetupConfig) -> MonitoringDesired:
        return MonitoringDesired(
            uptime_kuma=config.monitoring.uptime_kuma,
            beszel=config.monitoring.beszel,
        )

    def plan(self, current: object, desired: object) -> tuple[Change, ...]:
        if not isinstance(current, MonitoringState) or not isinstance(desired, MonitoringDesired):
            raise TypeError("MonitoringModule received incompatible state")

        wants_any = desired.uptime_kuma or desired.beszel
        if wants_any and not current.bundle_present:
            return (
                Change(
                    self.name,
                    ChangeKind.DANGEROUS,
                    "Monitoring service bundle is missing",
                    f"Expected the bundled Compose model at {COMPOSE_PATH}.",
                    action="blocked",
                ),
            )

        changes: list[Change] = []
        for field, service in _SERVICE_BY_FIELD.items():
            enabled = getattr(desired, field)
            running = service in current.running_services
            if enabled and not running:
                changes.append(
                    Change(
                        self.name,
                        ChangeKind.CREATE,
                        f"Start {service}",
                        "Docker Compose may be installed by the earlier Dokploy module in the same apply.",
                        action="start-service",
                        target=service,
                    )
                )
            elif not enabled and running:
                changes.append(
                    Change(
                        self.name,
                        ChangeKind.UPDATE,
                        f"Stop {service}",
                        "Named volumes are preserved; disabling monitoring does not delete service data.",
                        action="stop-service",
                        target=service,
                    )
                )
        return tuple(changes)

    def apply(self, changes: tuple[Change, ...]) -> None:
        if not self.system.exists(COMPOSE_PATH):
            raise ModuleApplyError(f"Monitoring bundle is missing at {COMPOSE_PATH}")
        if not self._compose_available():
            raise ModuleApplyError("Docker Compose is required before monitoring services can be reconciled")

        prefix = _compose_prefix(self.system)
        for change in changes:
            if change.action == "blocked":
                raise ModuleApplyError(change.summary)
            if change.target not in _SERVICE_BY_FIELD.values():
                raise ModuleApplyError(f"Unknown monitoring service target: {change.target!r}")
            if change.action == "start-service":
                self.system.run([*prefix, "up", "-d", change.target], check=True)
            elif change.action == "stop-service":
                self.system.run([*prefix, "stop", change.target], check=True)
            else:
                raise ModuleApplyError(f"Unknown monitoring action: {change.action!r}")

    def validate(self, desired: object) -> tuple[ValidationResult, ...]:
        if not isinstance(desired, MonitoringDesired):
            raise TypeError("MonitoringModule received incompatible desired state")
        current = self.inspect()
        wants_any = desired.uptime_kuma or desired.beszel
        results: list[ValidationResult] = []

        if wants_any:
            results.append(
                ValidationResult(
                    self.name,
                    ValidationStatus.PASS if current.bundle_present else ValidationStatus.FAIL,
                    "Monitoring Compose bundle is present" if current.bundle_present else "Monitoring Compose bundle is missing",
                    None if current.bundle_present else f"Expected {COMPOSE_PATH}.",
                )
            )
            results.append(
                ValidationResult(
                    self.name,
                    ValidationStatus.PASS if current.compose_available else ValidationStatus.FAIL,
                    "Docker Compose is available" if current.compose_available else "Docker Compose is unavailable",
                )
            )

        for field, service in _SERVICE_BY_FIELD.items():
            enabled = getattr(desired, field)
            running = service in current.running_services
            if enabled:
                results.append(
                    ValidationResult(
                        self.name,
                        ValidationStatus.PASS if running else ValidationStatus.FAIL,
                        f"{service} is running" if running else f"{service} is not running",
                    )
                )
            elif running:
                results.append(
                    ValidationResult(
                        self.name,
                        ValidationStatus.FAIL,
                        f"{service} is running but disabled in configuration",
                    )
                )
            else:
                results.append(ValidationResult(self.name, ValidationStatus.SKIP, f"{service} is not managed"))
        return tuple(results)
