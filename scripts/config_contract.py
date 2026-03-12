#!/usr/bin/env python3
"""Validation and normalization contract for discovered site configs."""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path


class ValidationError(ValueError):
    """Raised when a config cannot be normalized safely."""


def git_output(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def require_object(conf: dict, key: str, conf_path: Path) -> dict:
    value = conf.get(key)
    if not isinstance(value, dict):
        raise ValidationError(
            f"Validation error in {conf_path}: missing or invalid required object '{key}'"
        )
    return value


def optional_object(conf: dict, key: str, conf_path: Path) -> dict:
    value = conf.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError(
            f"Validation error in {conf_path}: optional key '{key}' must be an object when provided"
        )
    return value


def require_non_empty_string(value: object, key: str, conf_path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Validation error in {conf_path}: missing required key '{key}'")
    return value.strip()


def normalize_server_conf(conf_path: Path) -> dict:
    try:
        conf = json.loads(conf_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON in {conf_path}: {exc.msg}") from exc

    if not isinstance(conf, dict):
        raise ValidationError(f"Invalid config in {conf_path}: root must be a JSON object")

    repo_dir = conf_path.parent
    name = require_non_empty_string(conf.get("name"), "name", conf_path)
    domain = require_non_empty_string(conf.get("domain"), "domain", conf_path)

    repo = conf.get("repo")
    if not isinstance(repo, str) or not repo.strip():
        repo = git_output(repo_dir, "config", "--get", "remote.origin.url") or str(repo_dir.resolve())

    branch = conf.get("branch")
    if not isinstance(branch, str) or not branch.strip():
        branch = git_output(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")

    if not repo:
        raise ValidationError(
            f"Validation error in {conf_path}: missing required key 'repo' and could not auto-detect origin URL"
        )
    if not branch:
        raise ValidationError(
            f"Validation error in {conf_path}: missing required key 'branch' and could not auto-detect current branch"
        )

    web_root = conf.get("web_root")
    build_output = conf.get("build_output")
    if not isinstance(web_root, str) or not web_root.strip():
        web_root = None
    if not isinstance(build_output, str) or not build_output.strip():
        build_output = None
    if not web_root and not build_output:
        raise ValidationError(
            f"Validation error in {conf_path}: one of 'web_root' or 'build_output' must be set"
        )

    deploy_hooks = require_object(conf, "deploy_hooks", conf_path)
    runtime = require_object(conf, "runtime", conf_path)
    service = require_object(conf, "service", conf_path)
    nginx = optional_object(conf, "nginx", conf_path)
    deploy = optional_object(conf, "deploy", conf_path)

    runtime_mode = runtime.get("mode") or runtime.get("type")
    if not isinstance(runtime_mode, str) or runtime_mode not in {"static", "service"}:
        raise ValidationError(
            f"Validation error in {conf_path}: runtime.mode must be either 'static' or 'service'"
        )

    service_name = service.get("name")
    if not isinstance(service_name, str) or not service_name.strip():
        raise ValidationError(f"Validation error in {conf_path}: missing required key 'service.name'")

    if "www_redirect" in nginx and not isinstance(nginx["www_redirect"], bool):
        raise ValidationError(
            f"Validation error in {conf_path}: optional key 'nginx.www_redirect' must be a boolean when provided"
        )

    tls_hostnames = nginx.get("tls_hostnames", [])
    if tls_hostnames is None:
        tls_hostnames = []
    if not isinstance(tls_hostnames, list) or any(
        not isinstance(item, str) or not item.strip() for item in tls_hostnames
    ):
        raise ValidationError(
            f"Validation error in {conf_path}: optional key 'nginx.tls_hostnames' must contain only non-empty strings"
        )

    if runtime_mode == "service":
        command = runtime.get("command")
        port = runtime.get("port")
        if not isinstance(command, str) or not command.strip():
            raise ValidationError(
                f"Validation error in {conf_path}: missing required key 'runtime.command' for service mode"
            )
        if not isinstance(port, int):
            raise ValidationError(
                f"Validation error in {conf_path}: runtime.port must be numeric"
            )

    workdir = conf.get("workdir")
    if not isinstance(workdir, str) or not workdir.strip():
        workdir = f"/srv/github-sites/{name}"

    keep_releases = deploy.get("keep_releases", conf.get("keep_releases", 5))
    if not isinstance(keep_releases, int) or keep_releases < 0:
        raise ValidationError(
            f"Validation error in {conf_path}: keep_releases must be a non-negative integer"
        )

    health_retries = deploy.get("health_retries", runtime.get("health_retries", 20))
    if not isinstance(health_retries, int) or health_retries <= 0:
        raise ValidationError(
            f"Validation error in {conf_path}: health_retries must be a positive integer"
        )

    health_interval_seconds = deploy.get(
        "health_interval_seconds", runtime.get("health_interval_seconds", 2)
    )
    if not isinstance(health_interval_seconds, int) or health_interval_seconds <= 0:
        raise ValidationError(
            f"Validation error in {conf_path}: health_interval_seconds must be a positive integer"
        )

    normalized_runtime = dict(runtime)
    normalized_runtime["mode"] = runtime_mode
    normalized_runtime.setdefault("working_dir", ".")
    normalized_runtime.setdefault("user", os.environ.get("USER", "root"))
    normalized_runtime.setdefault("health_endpoint", "/health")
    normalized_runtime.setdefault("health_retries", health_retries)
    normalized_runtime.setdefault("health_interval_seconds", health_interval_seconds)

    return {
        "name": name,
        "repo": repo,
        "branch": branch,
        "domain": domain,
        "site_url": conf.get("site_url") or f"https://{domain}",
        "workdir": workdir,
        "releases_dir": conf.get("releases_dir") or f"{workdir}/releases",
        "current_symlink": conf.get("current_symlink") or f"{workdir}/current",
        "keep_releases": keep_releases,
        "web_root": web_root,
        "build_output": build_output,
        "deploy_script": conf.get("deploy_script"),
        "pre_deploy_cmd": deploy_hooks.get("pre_deploy"),
        "build_cmd": deploy_hooks.get("build"),
        "post_deploy_cmd": deploy_hooks.get("post_deploy") or service.get("reload_cmd"),
        "git_ssh_command": conf.get("git_ssh_command"),
        "unlighthouse_server_url": conf.get("unlighthouse_server_url"),
        "unlighthouse_server_token": conf.get("unlighthouse_server_token"),
        "unlighthouse_cmd": conf.get("unlighthouse_cmd"),
        "runtime": normalized_runtime,
        "service": dict(service),
        "nginx": {
            "www_redirect": bool(nginx.get("www_redirect", False)),
            "tls_hostnames": tls_hostnames,
        },
        "source_server_conf": str(conf_path),
    }


def discover_sites(base_glob: str) -> list[dict]:
    matches = [Path(path) for path in sorted(glob.glob(base_glob))]
    if not matches:
        raise ValidationError(f"No directories matched base glob: {base_glob}")

    normalized: list[dict] = []
    seen_names: dict[str, Path] = {}
    seen_domains: dict[str, Path] = {}

    for repo_dir in matches:
        if not repo_dir.is_dir():
            continue
        conf_path = repo_dir / "server.conf"
        if not conf_path.is_file():
            continue

        entry = normalize_server_conf(conf_path)
        name = entry["name"]
        domain = entry["domain"]

        if name in seen_names:
            raise ValidationError(
                f"Validation error: duplicate site name '{name}' in {conf_path} and {seen_names[name]}"
            )
        if domain in seen_domains:
            raise ValidationError(
                f"Validation error: duplicate site domain '{domain}' in {conf_path} and {seen_domains[domain]}"
            )

        seen_names[name] = conf_path
        seen_domains[domain] = conf_path
        normalized.append(entry)

    return normalized


def write_output(entries: list[dict], output_path: Path, dry_run: bool) -> None:
    body = json.dumps(entries, indent=2, sort_keys=False)
    if dry_run:
        print(body)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{body}\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and normalize site discovery config")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="Discover server.conf files")
    discover.add_argument("--base-glob", default="/srv/apps/*")
    discover.add_argument("--output", default="deploy/sites.json")
    discover.add_argument("--dry-run", action="store_true")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "discover":
            entries = discover_sites(args.base_glob)
            for entry in entries:
                print(f"Discovered site '{entry['name']}' from {entry['source_server_conf']}", file=sys.stderr)
            write_output(entries, Path(args.output), args.dry_run)
            return 0
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
