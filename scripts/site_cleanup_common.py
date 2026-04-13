#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


AUTOMATION_UNITS = (
    "site-discovery-deploy.service",
    "site-discovery-deploy.timer",
    "site-apps-watcher.service",
    "site-webhook-receiver.service",
)

RESET_UNITS = AUTOMATION_UNITS + (
    "server-setup-status-webapp.service",
    "server-setup-example-apps.service",
)


def first_config_value(*values: object) -> object:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value.strip()
            continue
        return value
    return ""


def runtime_service_name(site_json: dict, site_name: str) -> str:
    service = site_json.get("service") or {}
    configured_name = service.get("name")
    if isinstance(configured_name, str) and configured_name.strip():
        return configured_name.strip()
    top_level_name = site_json.get("service_name")
    if isinstance(top_level_name, str) and top_level_name.strip():
        return top_level_name.strip()
    if any(
        key in site_json
        for key in (
            "command",
            "port",
            "build",
            "pre_deploy",
            "post_deploy",
            "www_redirect",
            "tls_hostnames",
        )
    ):
        return f"{site_name}.service"
    return f"app-{site_name}.service"


def infer_runtime_mode(site_json: dict) -> str:
    runtime = site_json.get("runtime") or {}
    mode = first_config_value(
        runtime.get("mode"),
        runtime.get("type"),
        site_json.get("runtime_mode"),
        site_json.get("mode"),
    )
    if mode:
        return str(mode)
    has_runtime = first_config_value(
        runtime.get("command"),
        site_json.get("command"),
        runtime.get("port"),
        site_json.get("port"),
    )
    return "service" if has_runtime else "static"


@dataclass(frozen=True)
class ManagedSite:
    name: str
    runtime_service: str
    workdir: str
    releases_dir: str
    current_symlink: str

    @classmethod
    def from_json(cls, raw: dict) -> "ManagedSite":
        name = str(raw.get("name", "")).strip()
        if not name:
            raise SystemExit("Invalid deploy config: every site entry needs a non-empty 'name'.")
        workdir = str(first_config_value(raw.get("workdir"), f"/srv/github-sites/{name}"))
        releases_dir = str(first_config_value(raw.get("releases_dir"), str(Path(workdir) / "releases")))
        current_symlink = str(first_config_value(raw.get("current_symlink"), str(Path(workdir) / "current")))
        runtime_service = runtime_service_name(raw, name) if infer_runtime_mode(raw) == "service" else ""
        return cls(
            name=name,
            runtime_service=runtime_service,
            workdir=workdir,
            releases_dir=releases_dir,
            current_symlink=current_symlink,
        )


def load_managed_sites(config_path: Path) -> list[ManagedSite]:
    if not config_path.exists():
        return []
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"Deploy config must contain a JSON array: {config_path}")
    return [ManagedSite.from_json(item) for item in payload]
