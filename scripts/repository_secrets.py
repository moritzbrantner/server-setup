#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from repo_config_bootstrap import find_example_dotfiles, target_dotfile_path
from simple_setup_common import load_env_file, update_env_file


WORKFLOW_SECRET_PATTERNS = (
    re.compile(r"\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)\b"),
    re.compile(r"""secrets\[['"]([A-Za-z_][A-Za-z0-9_]*)['"]\]"""),
)
WORKFLOW_SUFFIXES = {".yml", ".yaml"}


def find_workflow_files(checkout_path: str | Path) -> list[Path]:
    checkout = Path(checkout_path)
    workflow_dir = checkout / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return []
    return sorted(
        path
        for path in workflow_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in WORKFLOW_SUFFIXES
    )


def discover_workflow_secret_references(checkout_path: str | Path) -> dict[str, list[str]]:
    checkout = Path(checkout_path)
    discovered: dict[str, set[str]] = {}
    for workflow_path in find_workflow_files(checkout):
        relative_path = workflow_path.relative_to(checkout).as_posix()
        body = workflow_path.read_text(encoding="utf-8")
        for pattern in WORKFLOW_SECRET_PATTERNS:
            for match in pattern.finditer(body):
                discovered.setdefault(match.group(1), set()).add(relative_path)

    return {
        name: sorted(paths)
        for name, paths in sorted(discovered.items(), key=lambda item: item[0])
    }


def _repo_root_env_target(checkout: Path) -> Path:
    return checkout / ".env"


def _resolve_runtime_env_file(checkout: Path, runtime_env_file: str) -> Path:
    candidate = Path(runtime_env_file.strip())
    if candidate.is_absolute():
        return candidate
    return (checkout / candidate).resolve()


def select_secret_env_file(checkout_path: str | Path, runtime_env_file: str = "") -> Path:
    checkout = Path(checkout_path).resolve()
    root_env = _repo_root_env_target(checkout)
    root_example = checkout / ".env.example"
    if root_env.exists() or root_example.exists():
        return root_env

    if runtime_env_file.strip():
        resolved = _resolve_runtime_env_file(checkout, runtime_env_file)
        try:
            resolved.relative_to(checkout)
        except ValueError:
            return root_env
        return resolved

    example_targets = [target_dotfile_path(path) for path in find_example_dotfiles(checkout)]
    if example_targets:
        return example_targets[0].resolve()
    return root_env


def seed_env_file_from_example(target_path: str | Path) -> Path | None:
    target = Path(target_path)
    if target.exists():
        return None

    example = target.with_name(f"{target.name}.example")
    if not example.is_file():
        return None

    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", delete=False, dir=str(target.parent), encoding="utf-8")
    try:
        handle.write(example.read_text(encoding="utf-8"))
        handle.close()
        Path(handle.name).replace(target)
    finally:
        Path(handle.name).unlink(missing_ok=True)
    return target


def _render_env_value(value: str) -> str:
    if value == "":
        return value
    special_chars = ("\n", "\r", "#", '"', "'", " ")
    if any(char in value for char in special_chars) or value != value.strip():
        return json.dumps(value)
    return value


def write_env_keys(target_path: str | Path, updates: dict[str, str]) -> Path:
    target = Path(target_path)
    seed_env_file_from_example(target)
    update_env_file(target, {key: _render_env_value(value) for key, value in updates.items()})
    return target


def delete_env_keys(target_path: str | Path, keys: list[str]) -> Path:
    target = Path(target_path)
    if not target.exists():
        return target

    key_set = {key.strip() for key in keys if key.strip()}
    if not key_set:
        return target

    rendered: list[str] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rendered.append(line)
            continue
        key, _ = line.split("=", 1)
        if key.strip() in key_set:
            continue
        rendered.append(line)

    body = "\n".join(rendered).rstrip()
    target.write_text(f"{body}\n" if body else "", encoding="utf-8")
    return target


def list_repository_secrets(
    checkout_path: str | Path,
    *,
    runtime_env_file: str = "",
) -> dict[str, object]:
    checkout = Path(checkout_path).resolve()
    env_file = select_secret_env_file(checkout, runtime_env_file)
    workflow_refs = discover_workflow_secret_references(checkout)
    env_values = load_env_file(env_file)

    names = sorted(set(workflow_refs) | set(env_values))
    return {
        "checkoutPath": str(checkout),
        "envFilePath": str(env_file),
        "workflowFiles": [path.relative_to(checkout).as_posix() for path in find_workflow_files(checkout)],
        "secrets": [
            {
                "name": name,
                "configured": bool(env_values.get(name, "").strip()),
                "presentInEnvFile": name in env_values,
                "requiredByWorkflows": workflow_refs.get(name, []),
            }
            for name in names
        ],
    }


def set_repository_secret(
    checkout_path: str | Path,
    name: str,
    value: str,
    *,
    runtime_env_file: str = "",
) -> dict[str, object]:
    trimmed_name = name.strip()
    if not trimmed_name:
        raise ValueError("Secret name is required.")
    if value == "":
        raise ValueError("Secret value cannot be empty.")

    checkout = Path(checkout_path).resolve()
    env_file = select_secret_env_file(checkout, runtime_env_file)
    write_env_keys(env_file, {trimmed_name: value})
    return list_repository_secrets(checkout, runtime_env_file=runtime_env_file)


def delete_repository_secret(
    checkout_path: str | Path,
    name: str,
    *,
    runtime_env_file: str = "",
) -> dict[str, object]:
    trimmed_name = name.strip()
    if not trimmed_name:
        raise ValueError("Secret name is required.")

    checkout = Path(checkout_path).resolve()
    env_file = select_secret_env_file(checkout, runtime_env_file)
    delete_env_keys(env_file, [trimmed_name])
    return list_repository_secrets(checkout, runtime_env_file=runtime_env_file)


def describe_repository_secrets(payload: dict[str, object]) -> str:
    env_file_path = str(payload.get("envFilePath") or "")
    secrets = payload.get("secrets") or []
    if not isinstance(secrets, list):
        secrets = []

    lines = [f"Env file: {env_file_path}"]
    if not secrets:
        lines.append("No workflow or env-file secrets were found.")
        return "\n".join(lines)

    for secret in secrets:
        if not isinstance(secret, dict):
            continue
        status = "configured" if secret.get("configured") else "missing"
        required = secret.get("requiredByWorkflows") or []
        if isinstance(required, list) and required:
            lines.append(f"- {secret.get('name')} [{status}] via {', '.join(str(item) for item in required)}")
        else:
            lines.append(f"- {secret.get('name')} [{status}]")
    return "\n".join(lines)


def to_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2)
