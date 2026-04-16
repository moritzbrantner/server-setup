#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from registry_contract import DEFAULT_REGISTRY_PATH, load_registry
from repository_secrets import (
    delete_repository_secret,
    describe_repository_secrets,
    list_repository_secrets,
    set_repository_secret,
    to_json,
)
from simple_setup_common import github_repo_full_name


def normalize_repo_full_name(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""

    detected = github_repo_full_name(candidate)
    if detected:
        return detected

    trimmed = candidate.removeprefix("github.com/").strip("/")
    if trimmed.startswith("https://github.com/") or trimmed.startswith("http://github.com/"):
        trimmed = trimmed.split("github.com/", 1)[-1]
    trimmed = trimmed.removesuffix(".git").strip("/")
    parts = [part for part in trimmed.split("/") if part]
    return "/".join(parts[:2]) if len(parts) >= 2 else ""


def find_registry_entry(args: argparse.Namespace) -> dict[str, object]:
    registry_path = Path(args.config).resolve()
    entries = load_registry(registry_path)

    if args.site:
        match = next((entry for entry in entries if str(entry.get("name") or "").strip() == args.site.strip()), None)
        if not match:
            raise SystemExit(f"No registry entry named '{args.site}' was found in {registry_path}.")
        return match

    if args.repo:
        normalized_repo = normalize_repo_full_name(args.repo)
        for entry in entries:
            repo_url = str(entry.get("repo_url") or "").strip()
            webhook_repo = str(entry.get("webhook_repo") or "").strip()
            if normalized_repo and webhook_repo == normalized_repo:
                return entry
            if repo_url and repo_url == args.repo.strip():
                return entry
        raise SystemExit(f"No registry entry matched '{args.repo}' in {registry_path}.")

    raise SystemExit("One of --site, --repo, or --checkout must be provided.")


def resolve_target(args: argparse.Namespace) -> dict[str, str | None]:
    if args.checkout:
        checkout_path = str(Path(args.checkout).resolve())
        return {
            "siteName": None,
            "repo": None,
            "checkoutPath": checkout_path,
            "runtimeEnvFile": "",
        }

    entry = find_registry_entry(args)
    deploy_config = entry.get("deploy_config") or {}
    runtime = deploy_config.get("runtime") or {}
    repo_url = str(entry.get("repo_url") or "").strip()
    return {
        "siteName": str(entry.get("name") or "").strip() or None,
        "repo": str(entry.get("webhook_repo") or "").strip() or github_repo_full_name(repo_url) or None,
        "checkoutPath": str(entry.get("checkout_path") or "").strip() or None,
        "runtimeEnvFile": str(runtime.get("env_file") or "").strip(),
    }


def render_result(
    action: str,
    target: dict[str, str | None],
    payload: dict[str, object],
    as_json: bool,
    message: str | None = None,
) -> None:
    response = {
        "action": action,
        "siteName": target.get("siteName"),
        "repo": target.get("repo"),
        "checkoutPath": target.get("checkoutPath"),
        **payload,
    }
    if message:
        response["message"] = message

    if as_json:
        print(to_json(response))
        return

    if message:
        print(message)
    print(describe_repository_secrets(payload))


def read_secret_value(args: argparse.Namespace) -> str:
    if args.value is not None:
        return args.value
    if args.value_file:
        return Path(args.value_file).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Provide --value, --value-file, or pipe the secret value on stdin.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage workflow-discovered repository secrets stored in repo-local env files."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_target_options(target_parser: argparse.ArgumentParser) -> None:
        target = target_parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--site", default="", help="Managed site name from deploy/registry.json.")
        target.add_argument("--repo", default="", help="Repository URL or OWNER/REPO resolved through deploy/registry.json.")
        target.add_argument("--checkout", default="", help="Direct checkout path to a repository.")
        target_parser.add_argument(
            "--config",
            default=str(DEFAULT_REGISTRY_PATH),
            help="Registry path used with --site or --repo (default: deploy/registry.json).",
        )
        target_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    list_parser = subparsers.add_parser("list", help="List workflow/env-file secrets for a repository.")
    add_target_options(list_parser)

    set_parser = subparsers.add_parser("set", help="Create or update a secret in the repository env file.")
    add_target_options(set_parser)
    set_parser.add_argument("name", help="Secret name.")
    set_parser.add_argument("--value", default=None, help="Secret value. Reads from stdin if omitted.")
    set_parser.add_argument("--value-file", default="", help="Read the secret value from a file.")

    delete_parser = subparsers.add_parser("delete", help="Delete a secret from the repository env file.")
    add_target_options(delete_parser)
    delete_parser.add_argument("name", help="Secret name.")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    target = resolve_target(args)
    checkout_path = target.get("checkoutPath")
    if not checkout_path:
        raise SystemExit("The selected repository does not have a checkout_path in the registry.")
    runtime_env_file = target.get("runtimeEnvFile") or ""

    if args.command == "list":
        payload = list_repository_secrets(checkout_path, runtime_env_file=runtime_env_file)
        render_result("list", target, payload, args.json)
        return

    if args.command == "set":
        payload = set_repository_secret(
            checkout_path,
            args.name,
            read_secret_value(args),
            runtime_env_file=runtime_env_file,
        )
        render_result(
            "set",
            target,
            payload,
            args.json,
            message=f"Updated repository secret {args.name} in {payload['envFilePath']}.",
        )
        return

    if args.command == "delete":
        payload = delete_repository_secret(
            checkout_path,
            args.name,
            runtime_env_file=runtime_env_file,
        )
        render_result(
            "delete",
            target,
            payload,
            args.json,
            message=f"Deleted repository secret {args.name} from {payload['envFilePath']}.",
        )
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
