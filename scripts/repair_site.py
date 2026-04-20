#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from deploy_engine import (
    build_registry_entry,
    clone_or_update_checkout,
    deploy_registry_entry,
    update_automation_env,
)
from registry_contract import DEFAULT_REGISTRY_PATH, load_registry
from repo_config_bootstrap import find_example_dotfiles, target_dotfile_path
from server_conf_contract import normalize_server_conf
from simple_setup_common import (
    AUTOMATION_ENV_FILE,
    generate_webhook_secret,
    load_env_file,
    repo_root,
    require_root,
    run_checked,
    setup_automation_units,
)


class RepairError(RuntimeError):
    """Raised when a site cannot be safely repaired."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely refresh and redeploy one registry-managed website."
    )
    parser.add_argument("--site", required=True, help="Site name from deploy/registry.json.")
    parser.add_argument("--config", default=str(DEFAULT_REGISTRY_PATH), help="Path to deploy registry JSON.")
    parser.add_argument("--email", default="", help="TLS email override. Defaults to DEFAULT_TLS_EMAIL.")
    parser.add_argument(
        "--configure-github-hook",
        action="store_true",
        help="Also create or update the GitHub webhook. Omitted by default for repair runs.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the repair plan without changing anything.")
    parser.add_argument("--json", action="store_true", help="Emit a JSON result.")
    return parser.parse_args()


def _compact_dict(value: dict) -> dict:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def server_conf_from_deploy_config(deploy_config: dict) -> dict:
    runtime = dict(deploy_config.get("runtime") or {})
    service = dict(deploy_config.get("service") or {})
    nginx = dict(deploy_config.get("nginx") or {})
    dns = dict(deploy_config.get("dns") or {})
    hooks = {
        key: value
        for key, value in dict(deploy_config.get("deploy_hooks") or {}).items()
        if key in {"pre_deploy", "build", "post_deploy"} and value
    }

    rendered: dict[str, object] = {
        "name": deploy_config.get("name"),
        "domain": deploy_config.get("domain"),
    }
    if deploy_config.get("build_output"):
        rendered["build_output"] = deploy_config["build_output"]
    if deploy_config.get("web_root"):
        rendered["web_root"] = deploy_config["web_root"]
    if hooks:
        rendered["deploy_hooks"] = hooks

    runtime_rendered: dict[str, object] = {}
    for key in (
        "mode",
        "command",
        "port",
        "working_dir",
        "user",
        "env_file",
        "health_endpoint",
        "health_retries",
        "health_interval_seconds",
    ):
        if key in runtime and runtime[key] not in (None, ""):
            runtime_rendered[key] = runtime[key]
    if runtime_rendered:
        rendered["runtime"] = runtime_rendered

    service_rendered = _compact_dict({"name": service.get("name")})
    if service_rendered:
        rendered["service"] = service_rendered

    nginx_rendered: dict[str, object] = {}
    if "www_redirect" in nginx:
        nginx_rendered["www_redirect"] = nginx["www_redirect"]
    if nginx.get("tls_hostnames"):
        nginx_rendered["tls_hostnames"] = nginx["tls_hostnames"]
    if nginx_rendered:
        rendered["nginx"] = nginx_rendered

    dns_rendered = _compact_dict({"provider": dns.get("provider"), "zone": dns.get("zone")})
    if dns_rendered:
        rendered["dns"] = dns_rendered

    missing = [key for key in ("name", "domain") if not rendered.get(key)]
    if not (rendered.get("build_output") or rendered.get("web_root")):
        missing.append("build_output or web_root")
    if missing:
        raise RepairError(
            "Registry deploy_config cannot recreate server.conf; missing "
            + ", ".join(missing)
            + "."
        )
    return rendered


def write_missing_server_conf_from_registry(checkout_path: Path, entry: dict) -> tuple[bool, str]:
    conf_path = checkout_path / "server.conf"
    if conf_path.exists():
        normalize_server_conf(checkout_path)
        return False, f"existing server.conf is valid: {conf_path}"

    deploy_config = entry.get("deploy_config")
    if not isinstance(deploy_config, dict):
        raise RepairError(f"Missing server.conf and registry deploy_config for {entry.get('name')}.")

    rendered = server_conf_from_deploy_config(deploy_config)
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
    normalize_server_conf(checkout_path)
    return True, f"created missing server.conf from registry deploy_config: {conf_path}"


def pending_example_targets(checkout_path: Path) -> list[Path]:
    return [
        target_dotfile_path(example)
        for example in find_example_dotfiles(checkout_path)
        if not target_dotfile_path(example).exists()
    ]


def load_site_entry(registry_path: Path, site_name: str) -> dict:
    matches = [
        entry
        for entry in load_registry(registry_path)
        if str(entry.get("name") or "").strip() == site_name
    ]
    if not matches:
        raise RepairError(f"No registry entry named '{site_name}' was found in {registry_path}.")
    if len(matches) > 1:
        raise RepairError(f"Multiple registry entries named '{site_name}' were found in {registry_path}.")
    return matches[0]


def require_entry_string(entry: dict, key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RepairError(f"Registry entry '{entry.get('name')}' is missing '{key}'.")
    return value.strip()


def ensure_no_tracked_local_changes(checkout_path: Path) -> None:
    if not (checkout_path / ".git").is_dir():
        return
    result = run_checked(
        ["git", "-C", str(checkout_path), "status", "--porcelain", "--untracked-files=no"],
        capture=True,
    )
    if (result.stdout or "").strip():
        raise RepairError(
            "Refusing to repair because the checkout has tracked local changes that would be "
            f"overwritten by the deploy reset: {checkout_path}"
        )


def build_plan(entry: dict, registry_path: Path) -> list[str]:
    site_name = require_entry_string(entry, "name")
    repo_url = require_entry_string(entry, "repo_url")
    checkout_path = Path(require_entry_string(entry, "checkout_path"))
    branch = str(entry.get("branch") or "").strip() or "main"

    plan = [
        f"load registry entry '{site_name}' from {registry_path}",
        "abort if tracked local checkout changes would be overwritten",
        f"clone or update {repo_url} at {checkout_path} on branch {branch}",
    ]
    if (checkout_path / "server.conf").exists():
        plan.append(f"validate and refresh registry metadata from {checkout_path / 'server.conf'}")
    elif isinstance(entry.get("deploy_config"), dict):
        plan.append(f"create missing {checkout_path / 'server.conf'} from registry deploy_config")
    else:
        plan.append(f"blocked: missing {checkout_path / 'server.conf'} and deploy_config")

    pending = pending_example_targets(checkout_path) if checkout_path.exists() else []
    if pending:
        joined = ", ".join(str(path) for path in pending)
        plan.append(f"blocked until repository config files exist: {joined}")
    else:
        plan.append("verify repository config templates are complete")
    plan.append("upsert deploy registry, refresh automation env, restart webhook receiver")
    plan.append("redeploy site through deploy engine")
    return plan


def repair_site(args: argparse.Namespace) -> dict[str, object]:
    registry_path = Path(args.config).resolve()
    site_name = args.site.strip()
    if not site_name:
        raise RepairError("--site is required.")

    entry = load_site_entry(registry_path, site_name)
    plan = build_plan(entry, registry_path)
    if args.dry_run:
        return {
            "action": "repair-site",
            "site": site_name,
            "registry": str(registry_path),
            "dryRun": True,
            "plan": plan,
            "changed": False,
        }

    require_root()

    root = repo_root()
    env_file = load_env_file(AUTOMATION_ENV_FILE)
    tls_email = args.email.strip() or env_file.get("DEFAULT_TLS_EMAIL", "").strip()
    if not tls_email:
        raise RepairError(
            "--email is required unless DEFAULT_TLS_EMAIL is configured in /etc/default/site-automation."
        )

    repo_url = require_entry_string(entry, "repo_url")
    checkout_path = Path(require_entry_string(entry, "checkout_path")).resolve()
    branch = str(entry.get("branch") or "").strip() or "main"
    webhook_secret = env_file.get("WEBHOOK_SECRET", "").strip() or generate_webhook_secret()

    ensure_no_tracked_local_changes(checkout_path)
    setup_automation_units(root, start_webhook=False)
    resolved_branch = clone_or_update_checkout(repo_url, checkout_path, branch)
    server_conf_created, server_conf_message = write_missing_server_conf_from_registry(checkout_path, entry)

    pending = pending_example_targets(checkout_path)
    if pending:
        joined = ", ".join(str(path) for path in pending)
        raise RepairError(
            "Repository config templates are still incomplete. Create these files first, then rerun repair: "
            + joined
        )

    refreshed_entry = build_registry_entry(registry_path, repo_url, resolved_branch, checkout_path)
    _, detected_webhook_url = update_automation_env(
        repo_root=root,
        registry_path=registry_path,
        webhook_secret=webhook_secret,
        repo_url=repo_url,
        branch=resolved_branch,
        checkout_path=checkout_path,
        default_tls_email=tls_email,
    )
    run_checked(["systemctl", "restart", "site-webhook-receiver.service"])
    result = deploy_registry_entry(
        refreshed_entry,
        tls_email=tls_email,
        configure_webhook=args.configure_github_hook,
        webhook_secret=webhook_secret,
        webhook_url=detected_webhook_url,
    )

    return {
        "action": "repair-site",
        "site": result.name,
        "registry": str(registry_path),
        "dryRun": False,
        "changed": server_conf_created,
        "serverConf": server_conf_message,
        "checkoutPath": result.checkout_path,
        "branch": result.branch,
        "domain": result.domain,
        "serviceName": result.service_name,
        "webhook": {"status": result.hook_status[0], "message": result.hook_status[1]},
    }


def render_text(result: dict[str, object]) -> str:
    if result.get("dryRun"):
        lines = [
            f"Repair plan for {result['site']}:",
            *(f"- {item}" for item in result.get("plan", [])),
        ]
        return "\n".join(lines)

    return (
        f"Repair finished for {result['site']}.\n"
        f"Domain: {result['domain']}\n"
        f"Checkout: {result['checkoutPath']}\n"
        f"Branch: {result['branch']}\n"
        f"Server config: {result['serverConf']}"
    )


def main() -> None:
    args = parse_args()
    try:
        result = repair_site(args)
    except Exception as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "action": "repair-site",
                        "site": args.site,
                        "ok": False,
                        "error": str(exc),
                    },
                    indent=2,
                )
            )
            raise SystemExit(1) from exc
        raise SystemExit(str(exc)) from exc

    if args.json:
        print(json.dumps({"ok": True, **result}, indent=2))
    else:
        sys.stdout.write(render_text(result) + "\n")


if __name__ == "__main__":
    main()
