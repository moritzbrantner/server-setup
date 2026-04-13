#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

from interactive_cli import ensure_interactive, maybe_sudo, prompt_bool, prompt_text, repo_root, run_command
from simple_setup_common import git_command_with_github_auth


def run_checked(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive single-repository onboarding.")
    parser.add_argument("--repo-url")
    parser.add_argument("--dest")
    parser.add_argument("--email")
    parser.add_argument("--config", default="deploy/sites.json")
    parser.add_argument("--branch", default="")
    parser.add_argument("--skip-tls", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--summary-output", default="")
    return parser.parse_args()


def collect_missing(args: argparse.Namespace) -> None:
    interactive = args.interactive or sys.stdin.isatty()
    if interactive and not args.skip_tls and not args.email:
        args.skip_tls = prompt_bool("Skip TLS provisioning?", default=False)
    required = ["repo_url", "dest"]
    if not args.skip_tls:
        required.append("email")
    ensure_interactive(args, required)
    if interactive and not args.repo_url:
        args.repo_url = prompt_text("Git repository URL", required=True)
    if interactive and not args.dest:
        args.dest = prompt_text("Checkout destination", required=True)
    if interactive and not args.branch:
        args.branch = prompt_text("Branch override", default="")
    if interactive and not args.skip_tls and not args.email:
        args.email = prompt_text("Let's Encrypt email", required=True)
    if interactive and not args.dry_run:
        args.dry_run = prompt_bool("Run dry-run only?", default=False)


def repo_checkout(repo_url: str, dest: Path) -> None:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"[1/7] Cloning repository into {dest}")
        run_checked(git_command_with_github_auth(repo_url, "clone", repo_url, str(dest)))
        return
    if not (dest / ".git").is_dir():
        raise SystemExit(f"Destination exists but is not a git repository: {dest}")
    existing_origin = subprocess.run(
        ["git", "-C", str(dest), "remote", "get-url", "origin"], text=True, capture_output=True, check=False
    ).stdout.strip()
    if existing_origin and existing_origin != repo_url:
        raise SystemExit(
            f"Destination repository origin mismatch.\n  existing: {existing_origin}\n  expected: {repo_url}"
        )
    if not existing_origin:
        run_checked(["git", "-C", str(dest), "remote", "add", "origin", repo_url])
    print(f"[1/7] Updating existing repository at {dest}")
    run_checked(git_command_with_github_auth(repo_url, "-C", str(dest), "fetch", "--prune", "origin"))


def checkout_branch(dest: Path, branch: str) -> None:
    if not branch:
        return
    print(f"[2/7] Ensuring branch '{branch}' is checked out")
    run_checked(["git", "-C", str(dest), "checkout", branch])
    run_checked(["git", "-C", str(dest), "reset", "--hard", f"origin/{branch}"])


def current_branch(dest: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "--abbrev-ref", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    return (result.stdout or "").strip()


def repo_basename(repo_url: str) -> str:
    if repo_url.startswith("git@"):
        return Path(repo_url.rsplit(":", 1)[-1]).name.removesuffix(".git")
    parsed = urllib.parse.urlparse(repo_url)
    return Path(parsed.path).name.removesuffix(".git")


def default_site_name(repo_url: str, dest: Path) -> str:
    for candidate in (repo_basename(repo_url), dest.name):
        cleaned = re.sub(r"[^a-z0-9.-]+", "-", candidate.strip().lower()).strip(".-")
        if cleaned:
            return cleaned
    return "app"


def prompt_int(prompt: str, default: int, minimum: int = 0) -> int:
    while True:
        raw = prompt_text(prompt, default=str(default), required=True)
        try:
            value = int(raw)
        except ValueError:
            continue
        if value >= minimum:
            return value


def discover_site_entry(root: Path, work_dest: Path) -> dict:
    discover_tmp = Path(tempfile.mkstemp()[1])
    try:
        run_checked(
            [
                "python3",
                str(root / "scripts/discover_sites.py"),
                "--base-glob",
                str(work_dest),
                "--output",
                str(discover_tmp),
            ],
            cwd=root,
        )
        sites = json.loads(discover_tmp.read_text(encoding="utf-8"))
    finally:
        discover_tmp.unlink(missing_ok=True)
    if len(sites) != 1:
        raise SystemExit(f"Expected exactly one site in discovered output, got {len(sites)}")
    return sites[0]


def build_site_entry_interactively(repo_url: str, work_dest: Path, branch_override: str) -> dict:
    name = prompt_text("Site name", default=default_site_name(repo_url, work_dest), required=True)
    domain = prompt_text("Domain", required=True)
    runtime_mode = "service" if prompt_bool("Run as a long-lived service?", default=False) else "static"
    build_output_default = "." if runtime_mode == "service" else "public"
    build_output = prompt_text(
        "Build output path (relative to repo, use '.' for repo root)",
        default=build_output_default,
        required=True,
    )
    build_cmd = prompt_text("Build command", default="")
    site_url = prompt_text("Public site URL", default=f"https://{domain}", required=True)
    www_redirect = prompt_bool(f"Redirect www.{domain} to {domain}?", default=False)
    branch = branch_override or current_branch(work_dest) or "main"
    checkout_path = str(work_dest.resolve())

    site_entry: dict[str, object] = {
        "name": name,
        "repo": repo_url,
        "branch": branch,
        "domain": domain,
        "site_url": site_url,
        "workdir": checkout_path,
        "deploy_mode": "checkout",
        "web_root": None,
        "build_output": build_output,
        "deploy_script": None,
        "pre_deploy_cmd": None,
        "build_cmd": build_cmd or None,
        "post_deploy_cmd": None,
        "git_ssh_command": None,
        "repo_auth": {
            "github_token": None,
            "github_username": None,
        },
        "unlighthouse_server_url": None,
        "unlighthouse_server_token": None,
        "unlighthouse_cmd": None,
        "managed_via": "onboard",
        "nginx": {
            "www_redirect": www_redirect,
            "tls_hostnames": [domain, f"www.{domain}"] if www_redirect else [domain],
        },
    }

    if runtime_mode == "service":
        runtime_command = prompt_text("Runtime command", required=True)
        runtime_port = prompt_int("Runtime port", default=3000, minimum=1)
        runtime_working_dir = prompt_text(
            "Runtime working dir (relative to release)",
            default=".",
            required=True,
        )
        runtime_user = prompt_text("Runtime user", default="root", required=True)
        runtime_env_file = prompt_text("Runtime env file", default="")
        health_endpoint = prompt_text("Health endpoint", default="/health", required=True)
        health_retries = prompt_int("Health retries", default=20, minimum=1)
        health_interval_seconds = prompt_int("Health interval seconds", default=2, minimum=1)
        service_name = prompt_text("Systemd service name", default=f"{name}.service", required=True)
        runtime: dict[str, object] = {
            "mode": "service",
            "command": runtime_command,
            "working_dir": runtime_working_dir,
            "user": runtime_user,
            "port": runtime_port,
            "health_endpoint": health_endpoint,
            "health_retries": health_retries,
            "health_interval_seconds": health_interval_seconds,
        }
        if runtime_env_file:
            runtime["env_file"] = runtime_env_file
        site_entry["runtime"] = runtime
        site_entry["service"] = {"name": service_name}
    else:
        site_entry["runtime"] = {
            "mode": "static",
            "working_dir": ".",
            "user": "root",
            "health_endpoint": "/health",
            "health_retries": 20,
            "health_interval_seconds": 2,
        }
        site_entry["service"] = {"name": f"{name}.service"}

    return site_entry


def maybe_write_summary(summary_output: str, site_entry: dict) -> None:
    if not summary_output:
        return
    summary_path = Path(summary_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(site_entry, indent=2) + "\n", encoding="utf-8")


def register_site_entry(discovered_json: dict, config_path: Path) -> None:
    site_name = discovered_json["name"]
    site_domain = discovered_json["domain"]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text("[]\n", encoding="utf-8")
    current = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(current, list):
        raise SystemExit(f"Existing config must be a JSON array: {config_path}")
    conflicting = next((entry["name"] for entry in current if entry.get("domain") == site_domain and entry.get("name") != site_name), "")
    if conflicting:
        raise SystemExit(f"Refusing to register '{site_name}': domain '{site_domain}' is already used by '{conflicting}'.")
    updated = sorted([entry for entry in current if entry.get("name") != site_name] + [discovered_json], key=lambda item: item["name"])
    config_path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    print(f"[4/7] Registered/updated app '{site_name}' in {config_path}")


def run_tls_step(repo_dir: Path, email: str, domain: str, include_www: bool, dry_run: bool, skip_tls: bool) -> None:
    if dry_run:
        print("[6/7] TLS step skipped by --dry-run")
        return
    if skip_tls:
        print("[6/7] TLS step skipped by --skip-tls")
        return
    print("[6/7] Acquiring/updating TLS certificates")
    cmd = ["python3", str(repo_dir / "scripts/setup_letsencrypt.py"), "--domain", domain, "--email", email]
    if include_www:
        cmd.append("--www")
    run_checked(cmd, cwd=repo_dir)


def check_dns_status(domain: str, target_ip: str) -> str:
    try:
        resolved = {entry[4][0] for entry in socket.getaddrinfo(domain, None)}
    except socket.gaierror:
        return "not-resolved"
    if target_ip:
        return "ok" if target_ip in resolved else "mismatch"
    return "resolved"


def main() -> None:
    args = parse_args()
    collect_missing(args)
    root = repo_root()
    interactive = args.interactive or sys.stdin.isatty()
    work_dest = Path(args.dest)
    temp_checkout: Path | None = None
    temp_config: Path | None = None

    if not args.dry_run and os.geteuid() != 0:
        run_command(maybe_sudo(["python3", str(root / "scripts/onboard_app.py"), *sys.argv[1:]]))
        return

    try:
        if args.dry_run:
            temp_checkout = Path(tempfile.mkdtemp()) / "repo"
            print("[1/7] Cloning repository into temporary dry-run checkout")
            run_checked(git_command_with_github_auth(args.repo_url, "clone", args.repo_url, str(temp_checkout)))
            work_dest = temp_checkout
        else:
            repo_checkout(args.repo_url, work_dest)

        if args.branch:
            checkout_branch(work_dest, args.branch)

        conf_path = work_dest / "server.conf"
        use_repo_config = conf_path.is_file()
        if conf_path.is_file() and interactive:
            use_repo_config = prompt_bool("Use repository server.conf for deploy config?", default=True)

        if use_repo_config:
            json.loads(conf_path.read_text(encoding="utf-8"))
            print("[3/7] Validating server.conf via discover-sites")
            site_entry = discover_site_entry(root, work_dest)
        else:
            if not interactive:
                raise SystemExit(
                    f"Missing required file: {conf_path}. Re-run in an interactive terminal to create deploy config in {args.config}."
                )
            print("[3/7] Collecting deploy config interactively")
            site_entry = build_site_entry_interactively(args.repo_url, work_dest, args.branch)
        maybe_write_summary(args.summary_output, site_entry)

        site_name = site_entry["name"]
        site_domain = site_entry["domain"]
        runtime_mode = (site_entry.get("runtime") or {}).get("mode", "static")
        deploy_mode = str(site_entry.get("deploy_mode") or "release")
        current_symlink = site_entry.get("current_symlink", "")
        www_redirect = bool((site_entry.get("nginx") or {}).get("www_redirect", False))
        tls_hostnames = (site_entry.get("nginx") or {}).get("tls_hostnames", []) or []
        tls_has_www = f"www.{site_domain}" in tls_hostnames

        if use_repo_config and not args.branch:
            conf_branch = json.loads(conf_path.read_text(encoding="utf-8")).get("branch", "")
            if conf_branch:
                checkout_branch(work_dest, conf_branch)

        if args.dry_run:
            temp_config = Path(tempfile.mkstemp()[1])
            temp_config.write_text(json.dumps([site_entry], indent=2) + "\n", encoding="utf-8")
            print("[4/7] Running deploy preflight")
            run_checked(["python3", str(root / "scripts/sync_github_sites.py"), "--config", str(temp_config), "--site", site_name, "--preflight-only"], cwd=root)
        else:
            register_site_entry(site_entry, root / args.config)
            print(f"[5/7] Deploying '{site_name}'")
            run_checked(["python3", str(root / "scripts/sync_github_sites.py"), "--config", str(root / args.config), "--site", site_name], cwd=root)

        include_www = www_redirect or tls_has_www
        run_tls_step(root, args.email or "", site_domain, include_www, args.dry_run, args.skip_tls)

        print("[7/7] Post-run summary")
        active_release = "<missing>"
        if deploy_mode == "checkout":
            active_release = str(work_dest.resolve())
        elif current_symlink and (Path(current_symlink).exists() or Path(current_symlink).is_symlink()):
            try:
                active_release = str(Path(current_symlink).resolve())
            except FileNotFoundError:
                active_release = current_symlink
        if args.dry_run:
            service_status = "n/a (dry-run)"
        elif runtime_mode == "service":
            service_name = (site_entry.get("service") or {}).get("name") or f"{site_name}.service"
            result = subprocess.run(["systemctl", "is-active", service_name], text=True, capture_output=True, check=False)
            service_status = result.stdout.strip() or "unknown"
        else:
            service_status = "n/a (static mode)"
        public_ip = subprocess.run(["curl", "-fsS", "--max-time", "5", "https://api.ipify.org"], text=True, capture_output=True, check=False).stdout.strip()
        dns_status = check_dns_status(site_domain, public_ip)
        manual_action = "none"
        if args.dry_run:
            manual_action = "Dry-run only; no deploy or TLS changes were applied"
        elif args.skip_tls:
            manual_action = "TLS step skipped; run scripts/setup_letsencrypt.py when DNS is ready"
        elif dns_status in {"not-resolved", "mismatch"}:
            manual_action = f"DNS may not be fully propagated for {site_domain}; verify A/AAAA records and re-run onboarding"
        print(
            f"\nOnboarding complete.\n- Domain: {site_domain}\n- Service status: {service_status}\n"
            f"- Active release path: {active_release}\n- DNS status: {dns_status}\n- Manual action required: {manual_action}"
        )
    finally:
        if temp_checkout:
            shutil.rmtree(temp_checkout.parent, ignore_errors=True)
        if temp_config:
            temp_config.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
