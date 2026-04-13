#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from simple_setup_common import (
    AUTOMATION_ENV_FILE,
    detect_webhook_url,
    generate_webhook_secret,
    github_repo_full_name,
    load_env_file,
    maybe_allow_ufw_port,
    maybe_configure_github_webhook,
    repo_root,
    require_root,
    run_checked,
    setup_automation_units,
    update_env_file,
    merge_csv_values,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clone a repo, deploy it, and wire up webhook-based redeploys.")
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--config", default="deploy/sites.json")
    parser.add_argument("--branch", default="")
    parser.add_argument("--webhook-secret", default="")
    parser.add_argument("--webhook-url", default="")
    parser.add_argument("--skip-github-hook", action="store_true")
    return parser.parse_args()


def discover_site(root: Path, checkout: Path) -> dict:
    with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as handle:
        output_path = Path(handle.name)
    try:
        run_checked(
            [
                "python3",
                str(root / "scripts/discover_sites.py"),
                "--base-glob",
                str(checkout),
                "--output",
                str(output_path),
            ],
            cwd=root,
        )
        sites = json.loads(output_path.read_text(encoding="utf-8"))
    finally:
        output_path.unlink(missing_ok=True)

    if len(sites) != 1:
        raise SystemExit(f"Expected exactly one discovered site in {checkout}, found {len(sites)}.")
    return sites[0]


def update_automation_env(
    root: Path,
    dest: Path,
    config_path: str,
    repo_url: str,
    branch: str,
    webhook_secret: str,
) -> tuple[str, str]:
    env_before = load_env_file(AUTOMATION_ENV_FILE)
    repo_full_name = github_repo_full_name(repo_url)
    webhook_url = detect_webhook_url("")

    updates = {
        "REPO_ROOT": str(root),
        "APPS_DIR": str(dest.parent),
        "APPS_GLOB": str(dest.parent / "*"),
        "CONFIG_PATH": str((root / config_path) if not Path(config_path).is_absolute() else Path(config_path)),
        "WEBHOOK_SECRET": webhook_secret,
        "WEBHOOK_ALLOW_INSECURE": "false",
    }
    if repo_full_name:
        updates["WEBHOOK_ALLOWED_REPOS"] = merge_csv_values(env_before.get("WEBHOOK_ALLOWED_REPOS", ""), [repo_full_name])
    if branch:
        updates["WEBHOOK_ALLOWED_BRANCHES"] = merge_csv_values(env_before.get("WEBHOOK_ALLOWED_BRANCHES", ""), [branch])
    update_env_file(AUTOMATION_ENV_FILE, updates)
    return (repo_full_name, webhook_url)


def main() -> None:
    args = parse_args()
    require_root()
    root = repo_root()
    dest = Path(args.dest).resolve()
    webhook_secret = args.webhook_secret or load_env_file(AUTOMATION_ENV_FILE).get("WEBHOOK_SECRET") or generate_webhook_secret()

    print("[1/4] Installing deploy automation services")
    setup_automation_units(root, start_webhook=False)

    print("[2/4] Cloning and deploying repository")
    onboard_cmd = [
        "python3",
        str(root / "scripts/onboard_app.py"),
        "--repo-url",
        args.repo_url,
        "--dest",
        str(dest),
        "--config",
        args.config,
        "--skip-tls",
    ]
    if args.branch:
        onboard_cmd.extend(["--branch", args.branch])
    run_checked(onboard_cmd, cwd=root)

    site = discover_site(root, dest)
    branch = args.branch or str(site.get("branch") or "main")
    service_name = ((site.get("service") or {}).get("name") or f"{site['name']}.service")

    print("[3/4] Configuring webhook receiver")
    repo_full_name, detected_webhook_url = update_automation_env(
        root,
        dest,
        args.config,
        args.repo_url,
        branch,
        webhook_secret,
    )
    run_checked(["systemctl", "restart", "site-webhook-receiver.service"])
    run_checked(["systemctl", "restart", "site-apps-watcher.service"])
    run_checked(["systemctl", "restart", "site-discovery-deploy.timer"])
    maybe_allow_ufw_port("9001/tcp")

    webhook_url = detect_webhook_url(args.webhook_url) if args.webhook_url else detected_webhook_url
    hook_status = ("skipped", "github hook setup was skipped by request")
    if not args.skip_github_hook:
        hook_status = maybe_configure_github_webhook(repo_full_name, webhook_url, webhook_secret)

    print("[4/4] Deployment summary")
    print(
        f"Site: {site['name']}\n"
        f"Domain: {site['domain']}\n"
        f"Service: {service_name}\n"
        f"Repository checkout: {dest}\n"
        f"Webhook URL: {webhook_url or '<undetected>'}\n"
        f"Webhook secret: {webhook_secret}\n"
        f"GitHub webhook: {hook_status[0]} ({hook_status[1]})"
    )
    if hook_status[0] != "ok":
        print(
            "\nManual GitHub webhook values:\n"
            f"- Payload URL: {webhook_url or 'set this manually'}\n"
            "- Content type: application/json\n"
            f"- Secret: {webhook_secret}\n"
            "- Events: push"
        )


if __name__ == "__main__":
    main()
