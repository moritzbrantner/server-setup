#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from repo_config_bootstrap import suggested_runtime_env_file


class ValidationError(ValueError):
    """Raised when a repo-owned server.conf is invalid."""


SUPPORTED_ROOT_KEYS = {
    "name",
    "domain",
    "build_output",
    "web_root",
    "deploy_hooks",
    "runtime",
    "service",
    "nginx",
    "dns",
}
UNSUPPORTED_ROOT_KEYS = {
    "repo",
    "branch",
    "workdir",
    "releases_dir",
    "current_symlink",
    "keep_releases",
    "deploy_mode",
    "repo_auth",
    "git_ssh_command",
    "managed_via",
    "source_server_conf",
    "site_url",
    "deploy_script",
    "pre_deploy_cmd",
    "build_cmd",
    "post_deploy_cmd",
    "unlighthouse_server_url",
    "unlighthouse_server_token",
    "unlighthouse_cmd",
}
UNSUPPORTED_PREFIXES = ("unlighthouse_",)
LEGACY_TOP_LEVEL_KEYS = {
    "build",
    "command",
    "port",
    "user",
    "env_file",
    "health_endpoint",
    "health_retries",
    "health_interval_seconds",
    "reload_cmd",
    "www_redirect",
    "tls_hostnames",
    "working_dir",
    "runtime_mode",
    "mode",
}
SUPPORTED_DEPLOY_HOOK_KEYS = {"pre_deploy", "build", "post_deploy"}
SUPPORTED_RUNTIME_KEYS = {
    "mode",
    "command",
    "port",
    "working_dir",
    "user",
    "env_file",
    "health_endpoint",
    "health_retries",
    "health_interval_seconds",
}
SUPPORTED_SERVICE_KEYS = {"name"}
SUPPORTED_NGINX_KEYS = {"www_redirect", "tls_hostnames"}
SUPPORTED_DNS_KEYS = {"provider", "zone"}


def _require_object(parent: dict, key: str, conf_path: Path) -> dict:
    value = parent.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError(f"Validation error in {conf_path}: '{key}' must be an object when provided")
    return value


def _require_string(value: object, key: str, conf_path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Validation error in {conf_path}: missing required key '{key}'")
    return value.strip()


def _prompt_choice(
    prompt_text_fn: Callable[..., str],
    print_fn: Callable[[str], None],
    prompt: str,
    *,
    choices: tuple[str, ...],
    default: str,
) -> str:
    while True:
        value = prompt_text_fn(prompt, default=default, required=True).strip().lower()
        if value in choices:
            return value
        print_fn(f"Please enter one of: {', '.join(choices)}")


def _prompt_port(prompt_text_fn: Callable[..., str], print_fn: Callable[[str], None], default: int) -> int:
    while True:
        value = prompt_text_fn("Runtime port", default=str(default), required=True).strip()
        if value.isdigit() and 0 < int(value) <= 65535:
            return int(value)
        print_fn("Please enter a valid TCP port between 1 and 65535")


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _default_build_command(checkout: Path) -> str:
    if (checkout / "package.json").is_file():
        if (checkout / "bun.lock").exists() or (checkout / "bun.lockb").exists():
            return "bun run build"
        if (checkout / "package-lock.json").exists():
            return "npm run build"
        if (checkout / "pnpm-lock.yaml").exists():
            return "pnpm run build"
        if (checkout / "yarn.lock").exists():
            return "yarn build"
    return ""


def create_server_conf_interactively(
    checkout_path: str | Path,
    *,
    prompt_text_fn: Callable[..., str],
    prompt_bool_fn: Callable[..., bool],
    print_fn: Callable[[str], None] = print,
) -> Path:
    checkout = Path(checkout_path)
    conf_path = checkout / "server.conf"
    if conf_path.exists():
        return conf_path

    print_fn(f"server.conf was not found in {checkout}.")
    print_fn("Enter the deployment settings to create it.")

    name = prompt_text_fn("Site name", default=checkout.name, required=True).strip()
    domain = prompt_text_fn("Primary domain", required=True).strip()
    mode = _prompt_choice(
        prompt_text_fn,
        print_fn,
        "Runtime mode (static/service)",
        choices=("static", "service"),
        default="static",
    )

    build_output_default = "public" if mode == "static" else "."
    build_output = prompt_text_fn(
        "Relative build output path",
        default=build_output_default,
        required=True,
    ).strip()
    build_command = _clean_optional(prompt_text_fn("Build command", default=_default_build_command(checkout)))
    enable_www_redirect = prompt_bool_fn("Redirect www to the primary domain", default=False)

    config: dict[str, object] = {
        "name": name,
        "domain": domain,
        "build_output": build_output,
    }
    if build_command:
        config["deploy_hooks"] = {"build": build_command}
    if enable_www_redirect:
        config["nginx"] = {
            "www_redirect": True,
            "tls_hostnames": [domain, f"www.{domain}"],
        }

    if mode == "service":
        runtime_command = prompt_text_fn("Runtime command", required=True).strip()
        runtime_port = _prompt_port(prompt_text_fn, print_fn, default=3000)
        health_endpoint = _clean_optional(prompt_text_fn("Health endpoint", default="/health"))
        env_file = _clean_optional(
            prompt_text_fn("Environment file path", default=suggested_runtime_env_file(checkout))
        )

        runtime = {
            "mode": "service",
            "command": runtime_command,
            "port": runtime_port,
        }
        if health_endpoint and health_endpoint != "/health":
            runtime["health_endpoint"] = health_endpoint
        if env_file:
            runtime["env_file"] = env_file
        config["runtime"] = runtime

    conf_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    normalize_server_conf(checkout)
    return conf_path


def _validate_allowed_keys(conf_path: Path, section: str, payload: dict, allowed: set[str]) -> None:
    extra = sorted(set(payload) - allowed)
    if extra:
        joined = ", ".join(extra)
        raise ValidationError(f"Validation error in {conf_path}: unsupported keys in {section}: {joined}")


def _validate_root_keys(conf: dict, conf_path: Path) -> None:
    errors: list[str] = []
    for key in sorted(conf):
        if key in SUPPORTED_ROOT_KEYS:
            continue
        if key in LEGACY_TOP_LEVEL_KEYS:
            errors.append(f"legacy top-level key '{key}' is no longer supported")
            continue
        if key in UNSUPPORTED_ROOT_KEYS or key.startswith(UNSUPPORTED_PREFIXES):
            errors.append(f"unsupported key '{key}' is no longer allowed")
            continue
        errors.append(f"unknown key '{key}' is not supported")
    if errors:
        detail = "; ".join(errors)
        raise ValidationError(f"Validation error in {conf_path}: {detail}")


def _normalize_runtime(runtime: dict, conf_path: Path) -> dict:
    _validate_allowed_keys(conf_path, "runtime", runtime, SUPPORTED_RUNTIME_KEYS)
    mode = runtime.get("mode", "static")
    if mode not in {"static", "service"}:
        raise ValidationError(f"Validation error in {conf_path}: runtime.mode must be 'static' or 'service'")

    working_dir = runtime.get("working_dir", ".")
    if not isinstance(working_dir, str) or not working_dir.strip():
        raise ValidationError(f"Validation error in {conf_path}: runtime.working_dir must be a non-empty string")
    if os.path.isabs(working_dir):
        raise ValidationError(
            f"Validation error in {conf_path}: runtime.working_dir must be relative to the checkout"
        )

    retries = runtime.get("health_retries", 20)
    if not isinstance(retries, int) or retries <= 0:
        raise ValidationError(f"Validation error in {conf_path}: runtime.health_retries must be a positive integer")
    interval = runtime.get("health_interval_seconds", 2)
    if not isinstance(interval, int) or interval <= 0:
        raise ValidationError(
            f"Validation error in {conf_path}: runtime.health_interval_seconds must be a positive integer"
        )

    normalized = {
        "mode": mode,
        "working_dir": working_dir.strip(),
        "user": str(runtime.get("user") or os.environ.get("USER") or "root"),
        "health_endpoint": str(runtime.get("health_endpoint") or "/health"),
        "health_retries": retries,
        "health_interval_seconds": interval,
    }

    env_file = runtime.get("env_file")
    if env_file is not None:
        if not isinstance(env_file, str) or not env_file.strip():
            raise ValidationError(f"Validation error in {conf_path}: runtime.env_file must be a non-empty string")
        normalized["env_file"] = env_file.strip()

    if mode == "service":
        command = runtime.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValidationError(
                f"Validation error in {conf_path}: runtime.command is required when runtime.mode is 'service'"
            )
        port = runtime.get("port")
        if not isinstance(port, int):
            raise ValidationError(f"Validation error in {conf_path}: runtime.port must be numeric")
        normalized["command"] = command.strip()
        normalized["port"] = port

    return normalized


def normalize_server_conf(checkout_path: str | Path) -> dict:
    checkout = Path(checkout_path)
    conf_path = checkout / "server.conf"
    if not conf_path.is_file():
        raise ValidationError(f"Missing required file: {conf_path}")

    try:
        conf = json.loads(conf_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON in {conf_path}: {exc.msg}") from exc

    if not isinstance(conf, dict):
        raise ValidationError(f"Invalid config in {conf_path}: root must be a JSON object")

    _validate_root_keys(conf, conf_path)
    deploy_hooks = _require_object(conf, "deploy_hooks", conf_path)
    runtime = _require_object(conf, "runtime", conf_path)
    service = _require_object(conf, "service", conf_path)
    nginx = _require_object(conf, "nginx", conf_path)
    dns = _require_object(conf, "dns", conf_path)
    _validate_allowed_keys(conf_path, "deploy_hooks", deploy_hooks, SUPPORTED_DEPLOY_HOOK_KEYS)
    _validate_allowed_keys(conf_path, "service", service, SUPPORTED_SERVICE_KEYS)
    _validate_allowed_keys(conf_path, "nginx", nginx, SUPPORTED_NGINX_KEYS)
    _validate_allowed_keys(conf_path, "dns", dns, SUPPORTED_DNS_KEYS)

    name = _require_string(conf.get("name"), "name", conf_path)
    domain = _require_string(conf.get("domain"), "domain", conf_path)
    build_output = conf.get("build_output")
    web_root = conf.get("web_root")
    if build_output is None and web_root is None:
        raise ValidationError(f"Validation error in {conf_path}: one of 'build_output' or 'web_root' must be set")
    if build_output is not None and (not isinstance(build_output, str) or not build_output.strip()):
        raise ValidationError(f"Validation error in {conf_path}: build_output must be a non-empty string")
    if web_root is not None and (not isinstance(web_root, str) or not web_root.strip()):
        raise ValidationError(f"Validation error in {conf_path}: web_root must be a non-empty string")

    service_name = service.get("name") or f"{name}.service"
    if not isinstance(service_name, str) or not service_name.strip():
        raise ValidationError(f"Validation error in {conf_path}: service.name must be a non-empty string")

    www_redirect = nginx.get("www_redirect", False)
    if not isinstance(www_redirect, bool):
        raise ValidationError(f"Validation error in {conf_path}: nginx.www_redirect must be a boolean")
    tls_hostnames = nginx.get("tls_hostnames", [domain])
    if tls_hostnames is None:
        tls_hostnames = [domain]
    if not isinstance(tls_hostnames, list) or any(not isinstance(item, str) or not item.strip() for item in tls_hostnames):
        raise ValidationError(
            f"Validation error in {conf_path}: nginx.tls_hostnames must be a list of non-empty strings"
        )

    normalized_dns = None
    if dns:
        provider = dns.get("provider")
        if provider not in {"namecheap", "porkbun"}:
            raise ValidationError(
                f"Validation error in {conf_path}: dns.provider must be 'namecheap' or 'porkbun'"
            )
        zone = dns.get("zone", domain)
        if not isinstance(zone, str) or not zone.strip():
            raise ValidationError(f"Validation error in {conf_path}: dns.zone must be a non-empty string")
        normalized_dns = {
            "provider": provider,
            "zone": zone.strip().lower(),
        }

    normalized = {
        "name": name,
        "domain": domain,
        "build_output": build_output.strip() if isinstance(build_output, str) else None,
        "web_root": web_root.strip() if isinstance(web_root, str) else None,
        "deploy_hooks": {
            "pre_deploy": deploy_hooks.get("pre_deploy"),
            "build": deploy_hooks.get("build"),
            "post_deploy": deploy_hooks.get("post_deploy"),
        },
        "runtime": _normalize_runtime(runtime, conf_path),
        "service": {"name": service_name.strip()},
        "nginx": {
            "www_redirect": www_redirect,
            "tls_hostnames": [item.strip() for item in tls_hostnames],
        },
        "dns": normalized_dns,
        "source_server_conf": str(conf_path.resolve()),
    }
    return normalized
