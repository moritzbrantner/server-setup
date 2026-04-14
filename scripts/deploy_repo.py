#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from deploy_engine import build_registry_entry, clone_or_update_checkout, deploy_registry_entry, repo_basename, update_automation_env
from interactive_cli import prompt_bool, prompt_text
from registry_contract import DEFAULT_REGISTRY_PATH
from server_conf_contract import create_server_conf_interactively
from simple_setup_common import AUTOMATION_ENV_FILE, generate_webhook_secret, load_env_file, repo_root, require_root, run_checked, setup_automation_units


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clone a repo, deploy it, and wire up webhook-based redeploys.")
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--dest", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--email", default="")
    parser.add_argument("--skip-github-hook", action="store_true")
    return parser.parse_args()


def ensure_server_conf(checkout_path: str | Path) -> Path:
    checkout = Path(checkout_path)
    conf_path = checkout / "server.conf"
    if conf_path.is_file():
        return conf_path
    if not sys.stdin.isatty():
        raise SystemExit(
            f"Missing required file: {conf_path}. Run deploy_repo.py in an interactive terminal to create it,"
            " or add server.conf manually before deploying."
        )
    return create_server_conf_interactively(
        checkout,
        prompt_text_fn=prompt_text,
        prompt_bool_fn=prompt_bool,
        print_fn=print,
    )


def main() -> None:
    args = parse_args()
    require_root()
    root = repo_root()
    registry_path = Path(os.environ.get("REGISTRY_PATH", str(DEFAULT_REGISTRY_PATH)))
    default_dest = Path("/srv/apps") / repo_basename(args.repo_url)
    dest = Path(args.dest or default_dest).resolve()
    env_file = load_env_file(AUTOMATION_ENV_FILE)
    tls_email = args.email.strip() or env_file.get("DEFAULT_TLS_EMAIL", "").strip()
    if not tls_email:
        raise SystemExit(
            "--email is required unless DEFAULT_TLS_EMAIL is already configured in /etc/default/site-automation"
        )
    webhook_secret = env_file.get("WEBHOOK_SECRET", "").strip() or generate_webhook_secret()

    print("[1/5] Installing deploy automation services")
    setup_automation_units(root, start_webhook=False)

    print("[2/5] Cloning or updating repository checkout")
    branch = clone_or_update_checkout(args.repo_url, dest, args.branch)
    ensure_server_conf(dest)

    print("[3/5] Validating server.conf and updating registry")
    entry = build_registry_entry(
        registry_path,
        args.repo_url,
        branch,
        dest,
    )

    print("[4/5] Configuring webhook receiver")
    repo_full_name, detected_webhook_url = update_automation_env(
        repo_root=root,
        registry_path=registry_path,
        webhook_secret=webhook_secret,
        repo_url=args.repo_url,
        branch=branch,
        checkout_path=dest,
        default_tls_email=tls_email,
    )
    run_checked(["systemctl", "restart", "site-webhook-receiver.service"])
    print("[5/5] Deploying repository")
    result = deploy_registry_entry(
        entry,
        tls_email=tls_email,
        configure_webhook=not args.skip_github_hook,
        webhook_secret=webhook_secret,
        webhook_url=detected_webhook_url,
    )

    print("[done] Deployment summary")
    print(
        f"Site: {result.name}\n"
        f"Domain: {result.domain}\n"
        f"Service: {result.service_name}\n"
        f"Repository checkout: {dest}\n"
        f"Branch: {result.branch}\n"
        f"Registry: {registry_path}\n"
        f"Webhook URL: {result.webhook_url or '<undetected>'}\n"
        f"Webhook secret: {webhook_secret}\n"
        f"GitHub webhook: {result.hook_status[0]} ({result.hook_status[1]})"
    )
    if result.hook_status[0] != "ok":
        print(
            "\nManual GitHub webhook values:\n"
            f"- Payload URL: {result.webhook_url or 'set this manually'}\n"
            "- Content type: application/json\n"
            f"- Secret: {webhook_secret}\n"
            "- Events: push"
        )


if __name__ == "__main__":
    main()
