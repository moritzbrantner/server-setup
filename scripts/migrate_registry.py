#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from registry_contract import DEFAULT_REGISTRY_PATH, save_registry, upsert_registry_entry
from server_conf_contract import normalize_server_conf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-time migration from deploy/sites.json to deploy/registry.json.")
    parser.add_argument("--input", default="deploy/sites.json")
    parser.add_argument("--output", default=str(DEFAULT_REGISTRY_PATH))
    parser.add_argument("--rewrite-server-conf", action="store_true")
    return parser.parse_args()


def legacy_to_server_conf(entry: dict) -> dict:
    runtime_mode = "service" if any(key in entry for key in ("command", "port")) else "static"
    converted = {
        "name": entry["name"],
        "domain": entry["domain"],
        "build_output": entry.get("build_output"),
        "web_root": entry.get("web_root"),
        "deploy_hooks": {
            "pre_deploy": entry.get("pre_deploy_cmd") or entry.get("pre_deploy"),
            "build": entry.get("build_cmd") or entry.get("build"),
            "post_deploy": entry.get("post_deploy_cmd") or entry.get("post_deploy") or entry.get("reload_cmd"),
        },
        "runtime": {
            "mode": runtime_mode,
            "working_dir": entry.get("working_dir") or ".",
            "user": entry.get("user") or "root",
            "health_endpoint": entry.get("health_endpoint") or "/health",
            "health_retries": entry.get("health_retries") or 20,
            "health_interval_seconds": entry.get("health_interval_seconds") or 2,
        },
        "service": {
            "name": ((entry.get("service") or {}).get("name")) or entry.get("service_name") or f"{entry['name']}.service",
        },
        "nginx": {
            "www_redirect": entry.get("www_redirect", False),
            "tls_hostnames": entry.get("tls_hostnames") or [entry["domain"]],
        },
    }
    if runtime_mode == "service":
        converted["runtime"]["command"] = entry.get("command")
        converted["runtime"]["port"] = entry.get("port")
        if entry.get("env_file"):
            converted["runtime"]["env_file"] = entry["env_file"]
    return converted


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"Legacy config must contain a JSON array: {input_path}")

    save_registry([], output_path)
    warnings: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            warnings.append("Skipped non-object entry in legacy config")
            continue
        checkout_path = str(item.get("workdir") or "")
        if not checkout_path:
            warnings.append(f"Skipped {item.get('name', '<unknown>')}: missing workdir")
            continue
        repo_url = str(item.get("repo") or checkout_path)
        branch = str(item.get("branch") or "main")
        server_conf_path = Path(checkout_path) / "server.conf"

        if args.rewrite_server_conf:
            converted = legacy_to_server_conf(item)
            server_conf_path.write_text(json.dumps(converted, indent=2) + "\n", encoding="utf-8")

        try:
            normalized = normalize_server_conf(checkout_path)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Skipped {item.get('name', '<unknown>')}: {exc}")
            continue
        upsert_registry_entry(output_path, repo_url, branch, checkout_path, normalized)

    print(f"Migrated registry written to {output_path}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")


if __name__ == "__main__":
    main()
