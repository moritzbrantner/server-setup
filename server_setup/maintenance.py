"""Read-only drift evidence and narrow host package maintenance helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from server_setup.config import ServerSetupConfig, parse_config
from server_setup.modules.dokploy import DokployModule
from server_setup.modules.host import BASE_PACKAGES
from server_setup.system import System

SNAPSHOT_SCHEMA = 1
DEFAULT_SNAPSHOT_PATH = Path("/var/lib/server-setup/last-upgrade.json")
_SECURITY_PACKAGES = ("unattended-upgrades", "apt-listchanges", "fail2ban", "ufw")
MANAGED_PACKAGES = tuple(dict.fromkeys((*BASE_PACKAGES, *_SECURITY_PACKAGES)))
_DEBIAN_VERSION_RE = re.compile(r"^[0-9A-Za-z.+:~_-]+$")


class SnapshotError(ValueError):
    """Raised when a maintenance snapshot is malformed or unsafe to use."""


@dataclass(frozen=True, slots=True)
class MaintenanceSnapshot:
    config_text: str
    package_versions: tuple[tuple[str, str], ...]
    dokploy_version: str | None

    @property
    def packages(self) -> dict[str, str]:
        return dict(self.package_versions)


def managed_packages_for_config(config: ServerSetupConfig) -> tuple[str, ...]:
    packages = list(BASE_PACKAGES)
    if config.host.unattended_upgrades:
        packages.extend(("unattended-upgrades", "apt-listchanges"))
    if config.security.fail2ban:
        packages.append("fail2ban")
    if config.security.firewall:
        packages.append("ufw")
    return tuple(dict.fromkeys(packages))


def package_version(system: System, package: str) -> str | None:
    result = system.run(["dpkg-query", "-W", "-f=${Version}", package])
    if result.returncode != 0:
        return None
    version = result.stdout.strip()
    return version or None


def current_package_versions(system: System, packages: Iterable[str] = MANAGED_PACKAGES) -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in packages:
        if package not in MANAGED_PACKAGES:
            raise SnapshotError(f"Refusing unmanaged package {package!r}")
        version = package_version(system, package)
        if version is not None:
            versions[package] = version
    return versions


def current_dokploy_version(system: System) -> str | None:
    state = DokployModule(system).inspect()
    return state.version if state.installed else None


def capture_snapshot(system: System, config_text: str) -> MaintenanceSnapshot:
    config = parse_config(config_text)
    versions = current_package_versions(system, managed_packages_for_config(config))
    return MaintenanceSnapshot(
        config_text=config_text,
        package_versions=tuple((package, versions[package]) for package in sorted(versions)),
        dokploy_version=current_dokploy_version(system),
    )


def render_snapshot(snapshot: MaintenanceSnapshot) -> str:
    payload = {
        "config": snapshot.config_text,
        "dokploy_version": snapshot.dokploy_version,
        "packages": snapshot.packages,
        "schema": SNAPSHOT_SCHEMA,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def parse_snapshot(text: str) -> MaintenanceSnapshot:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise SnapshotError(f"Invalid snapshot JSON: {error}") from error
    if not isinstance(raw, dict):
        raise SnapshotError("Snapshot root must be a JSON object")
    allowed_keys = {"schema", "config", "packages", "dokploy_version"}
    unknown = sorted(set(raw) - allowed_keys)
    if unknown:
        raise SnapshotError(f"Unknown snapshot key(s): {', '.join(unknown)}")
    if raw.get("schema") != SNAPSHOT_SCHEMA:
        raise SnapshotError(f"Unsupported snapshot schema {raw.get('schema')!r}; expected {SNAPSHOT_SCHEMA}")

    config_text = raw.get("config")
    if not isinstance(config_text, str) or not config_text.strip():
        raise SnapshotError("Snapshot config must be a non-empty string")
    config = parse_config(config_text)
    allowed_packages = set(managed_packages_for_config(config))

    raw_packages: Any = raw.get("packages")
    if not isinstance(raw_packages, dict):
        raise SnapshotError("Snapshot packages must be a JSON object")
    package_versions: list[tuple[str, str]] = []
    for package, version in sorted(raw_packages.items()):
        if package not in allowed_packages:
            raise SnapshotError(f"Snapshot contains package {package!r} that is not managed by its embedded configuration")
        if not isinstance(version, str) or not version or not _DEBIAN_VERSION_RE.fullmatch(version):
            raise SnapshotError(f"Snapshot contains invalid version for {package!r}")
        package_versions.append((package, version))

    dokploy_version = raw.get("dokploy_version")
    if dokploy_version is not None and (not isinstance(dokploy_version, str) or not dokploy_version.strip()):
        raise SnapshotError("Snapshot dokploy_version must be a non-empty string or null")

    return MaintenanceSnapshot(config_text, tuple(package_versions), dokploy_version)


def upgrade_managed_packages(system: System, packages: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(packages))
    versions = current_package_versions(system, selected)
    installed = tuple(package for package in selected if package in versions)
    if not installed:
        return ()
    system.run(["apt-get", "update", "-y"], check=True)
    system.run(
        ["apt-get", "install", "-y", "--only-upgrade", *installed],
        check=True,
        env={"DEBIAN_FRONTEND": "noninteractive"},
    )
    return installed


def rollback_package_versions(system: System, snapshot: MaintenanceSnapshot) -> tuple[str, ...]:
    snapshot_packages = tuple(package for package, _ in snapshot.package_versions)
    current = current_package_versions(system, snapshot_packages)
    targets = tuple(
        f"{package}={version}"
        for package, version in snapshot.package_versions
        if current.get(package) != version
    )
    if not targets:
        return ()
    system.run(["apt-get", "update", "-y"], check=True)
    system.run(
        ["apt-get", "install", "-y", "--allow-downgrades", *targets],
        check=True,
        env={"DEBIAN_FRONTEND": "noninteractive"},
    )
    return tuple(target.split("=", 1)[0] for target in targets)


def package_version_drift(system: System, snapshot: MaintenanceSnapshot) -> dict[str, tuple[str | None, str]]:
    snapshot_packages = tuple(package for package, _ in snapshot.package_versions)
    current = current_package_versions(system, snapshot_packages)
    return {
        package: (current.get(package), expected)
        for package, expected in snapshot.package_versions
        if current.get(package) != expected
    }
