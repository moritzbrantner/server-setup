#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

from registry_contract import DEFAULT_REGISTRY_PATH, load_registry
from simple_setup_common import github_cli_env, github_repo_full_name


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


def resolve_repo_from_site(site_name: str, registry_path: Path) -> str:
    for entry in load_registry(registry_path):
        if str(entry.get("name") or "").strip() != site_name:
            continue

        repo_full_name = str(entry.get("webhook_repo") or "").strip()
        if repo_full_name:
            return repo_full_name

        repo_url = str(entry.get("repo_url") or "").strip()
        repo_full_name = github_repo_full_name(repo_url)
        if repo_full_name:
            return repo_full_name

        raise SystemExit(f"Registry entry '{site_name}' does not reference a github.com repository.")

    raise SystemExit(f"No registry entry named '{site_name}' was found in {registry_path}.")


def resolve_target_repo(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.repo:
        repo_full_name = normalize_repo_full_name(args.repo)
        if not repo_full_name:
            raise SystemExit(f"Unable to derive a GitHub repository name from '{args.repo}'.")
        return repo_full_name, None

    if args.site:
        registry_path = Path(args.config).resolve()
        return resolve_repo_from_site(args.site.strip(), registry_path), args.site.strip()

    raise SystemExit("Either --repo or --site is required.")


def run_gh(args: list[str], *, stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
    if not shutil_which("gh"):
        raise SystemExit("GitHub CLI is not installed. Install 'gh' first.")

    result = subprocess.run(
        ["gh", *args],
        text=True,
        input=stdin_text,
        capture_output=True,
        env=github_cli_env(),
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            raise SystemExit(detail)
        raise SystemExit(f"gh {' '.join(args)} failed with exit code {result.returncode}.")
    return result


def shutil_which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def list_repo_secrets(repo_full_name: str) -> list[dict[str, object]]:
    result = run_gh(
        [
            "secret",
            "list",
            "--repo",
            repo_full_name,
            "--json",
            "name,updatedAt,visibility,numSelectedRepos",
        ]
    )
    payload = json.loads(result.stdout or "[]")
    if not isinstance(payload, list):
        raise SystemExit("GitHub CLI returned an unexpected secret list payload.")
    secrets: list[dict[str, object]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        secrets.append(
            {
                "name": str(entry.get("name") or ""),
                "updatedAt": str(entry.get("updatedAt") or "") or None,
                "visibility": str(entry.get("visibility") or "") or None,
                "numSelectedRepos": (
                    int(entry["numSelectedRepos"])
                    if isinstance(entry.get("numSelectedRepos"), int)
                    else None
                ),
            }
        )
    secrets.sort(key=lambda item: str(item.get("name") or ""))
    return secrets


def read_secret_value(args: argparse.Namespace) -> str:
    if args.value is not None:
        return args.value

    if args.value_file:
        return Path(args.value_file).read_text(encoding="utf-8")

    if not sys.stdin.isatty():
        return sys.stdin.read()

    value = getpass.getpass("Secret value: ")
    if not value:
        raise SystemExit("Secret value cannot be empty.")
    return value


def render_result(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return

    repo = str(payload.get("repo") or "")
    site_name = str(payload.get("siteName") or "")
    action = str(payload.get("action") or "")
    if action == "list":
        print(f"Repository: {repo}")
        if site_name:
            print(f"Site: {site_name}")
        secrets = payload.get("secrets") or []
        if not isinstance(secrets, list) or not secrets:
            print("No repository secrets found.")
            return
        for secret in secrets:
            if not isinstance(secret, dict):
                continue
            updated_at = str(secret.get("updatedAt") or "unknown")
            print(f"- {secret.get('name')} (updated {updated_at})")
        return

    message = str(payload.get("message") or "").strip()
    if message:
        print(message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage repository-level GitHub Actions secrets.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_target_options(target_parser: argparse.ArgumentParser) -> None:
        target = target_parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--repo", default="", help="GitHub repository in OWNER/REPO format or as a github.com URL.")
        target.add_argument("--site", default="", help="Managed site name from deploy/registry.json.")
        target_parser.add_argument(
            "--config",
            default=str(DEFAULT_REGISTRY_PATH),
            help="Registry path used with --site (default: deploy/registry.json).",
        )
        target_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    list_parser = subparsers.add_parser("list", help="List repository secrets.")
    add_target_options(list_parser)

    set_parser = subparsers.add_parser("set", help="Create or update a repository secret.")
    add_target_options(set_parser)
    set_parser.add_argument("name", help="Secret name.")
    set_parser.add_argument("--value", default=None, help="Secret value. Reads from stdin or a prompt if omitted.")
    set_parser.add_argument("--value-file", default="", help="Read the secret value from a file.")

    delete_parser = subparsers.add_parser("delete", help="Delete a repository secret.")
    add_target_options(delete_parser)
    delete_parser.add_argument("name", help="Secret name.")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    repo_full_name, site_name = resolve_target_repo(args)

    if args.command == "list":
        render_result(
            {
                "action": "list",
                "repo": repo_full_name,
                "siteName": site_name,
                "secrets": list_repo_secrets(repo_full_name),
            },
            args.json,
        )
        return

    if args.command == "set":
        secret_value = read_secret_value(args)
        run_gh(["secret", "set", args.name, "--repo", repo_full_name], stdin_text=secret_value)
        render_result(
            {
                "action": "set",
                "repo": repo_full_name,
                "siteName": site_name,
                "name": args.name,
                "message": f"Updated GitHub secret {args.name} for {repo_full_name}.",
            },
            args.json,
        )
        return

    if args.command == "delete":
        run_gh(["secret", "delete", args.name, "--repo", repo_full_name])
        render_result(
            {
                "action": "delete",
                "repo": repo_full_name,
                "siteName": site_name,
                "name": args.name,
                "message": f"Deleted GitHub secret {args.name} from {repo_full_name}.",
            },
            args.json,
        )
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
