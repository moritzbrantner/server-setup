"""Versioned declarative configuration for server-setup."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_VERSION = 1
DEFAULT_CONFIG_PATH = Path("/etc/server-setup/config.toml")
DEFAULT_DOKPLOY_VERSION = "v0.30.2"
_DOKPLOY_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


class ConfigError(ValueError):
    """Raised when server-setup configuration is invalid."""


@dataclass(frozen=True, slots=True)
class HostConfig:
    timezone: str = "UTC"
    unattended_upgrades: bool = True


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    firewall: bool = True
    fail2ban: bool = True
    ssh_hardening: bool = False


@dataclass(frozen=True, slots=True)
class DokployConfig:
    enabled: bool = True
    version: str = DEFAULT_DOKPLOY_VERSION


@dataclass(frozen=True, slots=True)
class DnsConfig:
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class MonitoringConfig:
    uptime_kuma: bool = False
    beszel: bool = False


@dataclass(frozen=True, slots=True)
class ServerSetupConfig:
    version: int = CONFIG_VERSION
    host: HostConfig = field(default_factory=HostConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    dokploy: DokployConfig = field(default_factory=DokployConfig)
    dns: DnsConfig = field(default_factory=DnsConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)


_TOP_LEVEL_KEYS = {"version", "host", "security", "dokploy", "dns", "monitoring"}
_SECTION_KEYS = {
    "host": {"timezone", "unattended_upgrades"},
    "security": {"firewall", "fail2ban", "ssh_hardening"},
    "dokploy": {"enabled", "version"},
    "dns": {"enabled"},
    "monitoring": {"uptime_kuma", "beszel"},
}


def _unknown_keys(values: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ConfigError(f"Unknown key(s) in {location}: {', '.join(unknown)}")


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a TOML table")
    _unknown_keys(value, _SECTION_KEYS[name], f"[{name}]")
    return value


def _bool(section: dict[str, Any], key: str, default: bool, location: str) -> bool:
    value = section.get(key, default)
    if type(value) is not bool:
        raise ConfigError(f"{location}.{key} must be a boolean")
    return value


def _string(section: dict[str, Any], key: str, default: str, location: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _dokploy_version(section: dict[str, Any], default: str) -> str:
    value = _string(section, "version", default, "dokploy")
    if not _DOKPLOY_VERSION_RE.fullmatch(value):
        raise ConfigError("dokploy.version must be an exact pinned release such as v0.30.2")
    return value


def parse_config(text: str) -> ServerSetupConfig:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"Invalid TOML: {error}") from error
    _unknown_keys(raw, _TOP_LEVEL_KEYS, "configuration root")
    version = raw.get("version")
    if type(version) is not int:
        raise ConfigError("version must be an integer")
    if version != CONFIG_VERSION:
        raise ConfigError(f"Unsupported config version {version}; expected {CONFIG_VERSION}")
    host = _section(raw, "host")
    security = _section(raw, "security")
    dokploy = _section(raw, "dokploy")
    dns = _section(raw, "dns")
    monitoring = _section(raw, "monitoring")
    host_defaults = HostConfig()
    security_defaults = SecurityConfig()
    dokploy_defaults = DokployConfig()
    dns_defaults = DnsConfig()
    monitoring_defaults = MonitoringConfig()
    return ServerSetupConfig(
        version=version,
        host=HostConfig(
            timezone=_string(host, "timezone", host_defaults.timezone, "host"),
            unattended_upgrades=_bool(host, "unattended_upgrades", host_defaults.unattended_upgrades, "host"),
        ),
        security=SecurityConfig(
            firewall=_bool(security, "firewall", security_defaults.firewall, "security"),
            fail2ban=_bool(security, "fail2ban", security_defaults.fail2ban, "security"),
            ssh_hardening=_bool(security, "ssh_hardening", security_defaults.ssh_hardening, "security"),
        ),
        dokploy=DokployConfig(
            enabled=_bool(dokploy, "enabled", dokploy_defaults.enabled, "dokploy"),
            version=_dokploy_version(dokploy, dokploy_defaults.version),
        ),
        dns=DnsConfig(enabled=_bool(dns, "enabled", dns_defaults.enabled, "dns")),
        monitoring=MonitoringConfig(
            uptime_kuma=_bool(monitoring, "uptime_kuma", monitoring_defaults.uptime_kuma, "monitoring"),
            beszel=_bool(monitoring, "beszel", monitoring_defaults.beszel, "monitoring"),
        ),
    )


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> ServerSetupConfig:
    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"Unable to read configuration {config_path}: {error}") from error
    return parse_config(text)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def render_config(config: ServerSetupConfig) -> str:
    if config.version != CONFIG_VERSION:
        raise ConfigError(f"Unsupported config version {config.version}; expected {CONFIG_VERSION}")
    return "\n".join([
        f"version = {config.version}", "",
        "[host]",
        f"timezone = {_toml_string(config.host.timezone)}",
        f"unattended_upgrades = {_toml_bool(config.host.unattended_upgrades)}", "",
        "[security]",
        f"firewall = {_toml_bool(config.security.firewall)}",
        f"fail2ban = {_toml_bool(config.security.fail2ban)}",
        f"ssh_hardening = {_toml_bool(config.security.ssh_hardening)}", "",
        "[dokploy]",
        f"enabled = {_toml_bool(config.dokploy.enabled)}",
        f"version = {_toml_string(config.dokploy.version)}", "",
        "[dns]", f"enabled = {_toml_bool(config.dns.enabled)}", "",
        "[monitoring]",
        f"uptime_kuma = {_toml_bool(config.monitoring.uptime_kuma)}",
        f"beszel = {_toml_bool(config.monitoring.beszel)}", "",
    ])
