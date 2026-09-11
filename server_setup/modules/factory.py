"""Default host-module assembly."""

from __future__ import annotations
from server_setup.modules.base import ServerModule
from server_setup.modules.deferred import DeferredFeatureModule
from server_setup.modules.dokploy import DokployModule
from server_setup.modules.host import HostModule
from server_setup.modules.security import SecurityModule
from server_setup.system import LocalSystem, System

def default_modules(system: System | None = None) -> tuple[ServerModule, ...]:
    host_system = system or LocalSystem()
    return (
        HostModule(host_system),
        SecurityModule(host_system),
        DokployModule(host_system),
        DeferredFeatureModule("dns", lambda config: config.dns.enabled),
        DeferredFeatureModule("monitoring", lambda config: config.monitoring.uptime_kuma or config.monitoring.beszel),
    )
