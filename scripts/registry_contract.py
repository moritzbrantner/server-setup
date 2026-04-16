#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from simple_setup_common import github_repo_full_name


DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "deploy" / "registry.json"


class RegistryError(ValueError):
    """Raised when registry data is invalid."""


def _is_same_site(existing: dict, incoming: dict) -> bool:
    return (
        str(existing.get("name") or "") == str(incoming.get("name") or "")
        or str(existing.get("checkout_path") or "") == str(incoming.get("checkout_path") or "")
    )


def _normalized_domain(entry: dict) -> str:
    return str(entry.get("domain") or "").strip().lower()


def _service_runtime(entry: dict) -> tuple[str, int | None] | None:
    deploy_config = entry.get("deploy_config") or {}
    runtime = deploy_config.get("runtime") or {}
    service = deploy_config.get("service") or {}
    if runtime.get("mode") != "service":
        return None
    service_name = str(entry.get("service_name") or service.get("name") or "").strip()
    port = runtime.get("port")
    return service_name, port if isinstance(port, int) else None


def validate_registry_entry_conflicts(entries: list[dict], incoming: dict) -> None:
    incoming_domain = _normalized_domain(incoming)
    incoming_service_runtime = _service_runtime(incoming)

    for existing in entries:
        if _is_same_site(existing, incoming):
            continue

        existing_name = str(existing.get("name") or "").strip() or "<unknown>"
        if incoming_domain and _normalized_domain(existing) == incoming_domain:
            raise RegistryError(
                f"Registry conflict on domain '{incoming['domain']}': existing site '{existing_name}' already uses it."
            )

        existing_service_runtime = _service_runtime(existing)
        if not incoming_service_runtime or not existing_service_runtime:
            continue

        incoming_service_name, incoming_port = incoming_service_runtime
        existing_service_name, existing_port = existing_service_runtime
        if incoming_service_name and existing_service_name == incoming_service_name:
            raise RegistryError(
                f"Registry conflict on service_name '{incoming_service_name}': existing site '{existing_name}' already uses it."
            )
        if incoming_port is not None and existing_port == incoming_port:
            raise RegistryError(
                f"Registry conflict on runtime.port '{incoming_port}': existing site '{existing_name}' already uses it."
            )


def _write_json(path: Path, payload: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
    try:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        handle.close()
        Path(handle.name).replace(path)
    finally:
        Path(handle.name).unlink(missing_ok=True)


def load_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> list[dict]:
    registry_path = Path(path)
    if not registry_path.exists():
        return []
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RegistryError(f"Registry must contain a JSON array: {registry_path}")
    return [entry for entry in payload if isinstance(entry, dict)]


def save_registry(entries: list[dict], path: str | Path = DEFAULT_REGISTRY_PATH) -> None:
    _write_json(Path(path), entries)


def upsert_registry_entry(
    registry_path: str | Path,
    repo_url: str,
    branch: str,
    checkout_path: str | Path,
    normalized_conf: dict,
) -> dict:
    path = Path(registry_path)
    entries = load_registry(path)
    checkout = str(Path(checkout_path).resolve())
    entry = {
        "name": normalized_conf["name"],
        "repo_url": repo_url,
        "branch": branch,
        "checkout_path": checkout,
        "server_conf_path": normalized_conf["source_server_conf"],
        "service_name": normalized_conf["service"]["name"],
        "domain": normalized_conf["domain"],
        "webhook_repo": github_repo_full_name(repo_url),
        "managed_by": "deploy-repo",
        "deploy_config": normalized_conf,
    }

    validate_registry_entry_conflicts(entries, entry)

    filtered = [
        existing
        for existing in entries
        if existing.get("name") != entry["name"] and existing.get("checkout_path") != entry["checkout_path"]
    ]
    filtered.append(entry)
    filtered.sort(key=lambda item: str(item.get("name") or ""))
    save_registry(filtered, path)
    return entry


def find_registry_entry_by_push(
    repo_full_name: str,
    branch: str,
    path: str | Path = DEFAULT_REGISTRY_PATH,
) -> dict | None:
    for entry in load_registry(path):
        if entry.get("webhook_repo") == repo_full_name and entry.get("branch") == branch:
            return entry
    return None


@dataclass(frozen=True)
class ManagedSite:
    name: str
    runtime_service: str
    checkout_path: str

    @classmethod
    def from_registry_entry(cls, raw: dict) -> "ManagedSite":
        name = str(raw.get("name") or "").strip()
        if not name:
            raise RegistryError("Invalid registry entry: every site needs a non-empty 'name'.")
        deploy_config = raw.get("deploy_config") or {}
        runtime = deploy_config.get("runtime") or {}
        service = deploy_config.get("service") or {}
        runtime_service = ""
        if runtime.get("mode") == "service":
            runtime_service = str(service.get("name") or f"{name}.service")
        checkout_path = str(raw.get("checkout_path") or "")
        return cls(name=name, runtime_service=runtime_service, checkout_path=checkout_path)


def load_managed_sites(path: str | Path = DEFAULT_REGISTRY_PATH) -> list[ManagedSite]:
    return [ManagedSite.from_registry_entry(entry) for entry in load_registry(path)]
