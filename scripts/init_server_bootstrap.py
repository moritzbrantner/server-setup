#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {message}")


def die(message: str) -> None:
    log(f"ERROR: {message}")
    raise SystemExit(1)


def require_root() -> None:
    if os.geteuid() != 0:
        die("This script must be run as root (use sudo).")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Canonical one-command server bootstrap.")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--web-root", default="")
    parser.add_argument("--port", default="")
    parser.add_argument("--email", required=True)
    parser.add_argument("--www", action="store_true")
    parser.add_argument("--skip-certbot", action="store_true")
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--skip-hardening", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--skip-automation", action="store_true")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not args.web_root and not args.port:
        die("One of --web-root or --port is required.")
    if args.web_root and args.port:
        die("--web-root and --port are mutually exclusive.")
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for ch in args.domain):
        die(f"Invalid domain '{args.domain}'.")
    if "@" not in args.email or "." not in args.email.split("@")[-1]:
        die(f"Invalid email '{args.email}'.")
    if args.port and (not args.port.isdigit() or not (1 <= int(args.port) <= 65535)):
        die(f"Invalid port '{args.port}'. Must be between 1 and 65535.")


def run_checked(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def collect_local_ips() -> set[str]:
    ips: set[str] = set()
    if shutil.which("hostname"):
        result = subprocess.run(["hostname", "-I"], text=True, capture_output=True, check=False)
        ips.update(ip for ip in result.stdout.split() if ip)
    if shutil.which("curl"):
        for url in ("https://ifconfig.me", "https://api.ipify.org"):
            result = subprocess.run(
                ["curl", "-4fsS", "--max-time", "3", url],
                text=True,
                capture_output=True,
                check=False,
            )
            ip = result.stdout.strip()
            if ip:
                ips.add(ip)
    return ips


def collect_domain_ips(name: str) -> set[str]:
    ips: set[str] = set()
    try:
        for entry in socket.getaddrinfo(name, None):
            addr = entry[4][0]
            if addr:
                ips.add(addr)
    except socket.gaierror:
        return set()
    return ips


def dns_preflight_or_die(args: argparse.Namespace, statuses: dict[str, str]) -> None:
    hosts = [args.domain]
    if args.www:
        hosts.append(f"www.{args.domain}")

    local_ips = collect_local_ips()
    any_missing = False
    any_mismatch = False

    for host in hosts:
        host_ips = collect_domain_ips(host)
        if not host_ips:
            log(f"DNS preflight: '{host}' does not resolve yet.")
            any_missing = True
            continue
        if local_ips and not (host_ips & local_ips):
            log(
                f"DNS preflight: '{host}' resolves to [{', '.join(sorted(host_ips))}], "
                f"but local server IPs appear to be [{', '.join(sorted(local_ips))}]"
            )
            any_mismatch = True

    if any_missing or any_mismatch:
        statuses["dns"] = "failed"
        print(
            "DNS check indicates records are not ready for certbot.\n"
            "Action items:\n"
            f"  1) Point A/AAAA records for {args.domain}"
            f"{' and www.' + args.domain if args.www else ''} to this server.\n"
            "  2) Wait for propagation.\n"
            "  3) Re-run init with --skip-certbot, then run setup-letsencrypt.sh later."
        )
        if args.non_interactive:
            die("Aborting certbot step because --non-interactive was supplied and DNS is not ready.")
        reply = input("Continue anyway and attempt certbot? [y/N] ").strip().lower()
        if reply not in {"y", "yes"}:
            die("Aborting before certbot due to DNS readiness check.")

    statuses["dns"] = "ok"


def setup_automation_units(root_dir: Path) -> None:
    systemd_dir = root_dir / "ops/systemd"
    env_file = Path("/etc/default/site-automation")
    if not systemd_dir.is_dir():
        log(f"Automation units directory not found at {systemd_dir}; skipping.")
        return
    log("Installing systemd automation units")
    for name in (
        "site-discovery-deploy.service",
        "site-apps-watcher.service",
        "site-webhook-receiver.service",
        "site-discovery-deploy.timer",
    ):
        shutil.copyfile(systemd_dir / name, Path("/etc/systemd/system") / name)
    if not env_file.exists():
        shutil.copyfile(systemd_dir / "site-automation.env.example", env_file)
        env_file.write_text(
            env_file.read_text(encoding="utf-8").replace("REPO_ROOT=/opt/server-setup", f"REPO_ROOT={root_dir}"),
            encoding="utf-8",
        )
        log(f"Wrote default automation environment to {env_file}")
    run_checked(["systemctl", "daemon-reload"], root_dir)
    run_checked(["systemctl", "enable", "--now", "site-discovery-deploy.timer"], root_dir)
    run_checked(["systemctl", "enable", "--now", "site-apps-watcher.service"], root_dir)
    run_checked(["systemctl", "enable", "--now", "site-webhook-receiver.service"], root_dir)
    log("Automation services enabled: watcher + webhook + fallback timer")


def print_summary(args: argparse.Namespace, statuses: dict[str, str]) -> None:
    site_target = args.web_root if args.web_root else f"http://127.0.0.1:{args.port}"
    print(
        "\n========== init-server summary ==========\n"
        f"Domain:        {args.domain}\n"
        f"Site target:   {site_target}\n"
        f"Email:         {args.email}\n"
        f"www enabled:   {'yes' if args.www else 'no'}\n\n"
        "Step status:\n"
        f"- ensure-server-tools: {statuses['tools']}\n"
        f"- docker check/install: {statuses['docker']}\n"
        f"- harden-server:       {statuses['hardening']}\n"
        f"- status-webapp:       {statuses['monitor']}\n"
        f"- install-nginx-site:  {statuses['nginx']}\n"
        f"- dns preflight:       {statuses['dns']}\n"
        f"- setup-letsencrypt:   {statuses['certbot']}\n"
        "========================================"
    )


def main() -> None:
    args = parse_args()
    validate_args(args)
    require_root()

    root_dir = Path(__file__).resolve().parent.parent
    statuses = {
        "tools": "not-run",
        "docker": "not-run",
        "hardening": "not-run",
        "monitor": "not-run",
        "nginx": "not-run",
        "dns": "not-run",
        "certbot": "not-run",
    }

    try:
        log("[1/6] Ensuring baseline server tools are present")
        cmd = ["bash", str(root_dir / "scripts/ensure-server-tools.sh")]
        if args.skip_docker:
            cmd.append("--skip-docker")
        run_checked(cmd, root_dir)
        statuses["tools"] = "ok"
        statuses["docker"] = "skipped" if args.skip_docker else "ok"

        if args.skip_hardening:
            statuses["hardening"] = "skipped"
            log("[2/6] Hardening step skipped by --skip-hardening")
        else:
            log("[2/6] Applying server hardening defaults")
            run_checked(["bash", str(root_dir / "scripts/harden-server.sh")], root_dir)
            statuses["hardening"] = "ok"

        log("[3/6] Installing/updating status webapp service")
        run_checked(["bash", str(root_dir / "scripts/setup-status-webapp.sh"), "--root", str(root_dir)], root_dir)
        statuses["monitor"] = "ok"

        log("[4/6] Installing/updating Nginx site configuration")
        nginx_cmd = [
            "python3",
            str(root_dir / "scripts/install_nginx_site.py"),
            "--domain",
            args.domain,
            "--email",
            args.email,
        ]
        if args.web_root:
            nginx_cmd.extend(["--root", args.web_root])
        else:
            nginx_cmd.extend(["--port", args.port])
        if args.www:
            nginx_cmd.append("--www-redirect")
        run_checked(nginx_cmd, root_dir)
        statuses["nginx"] = "ok"

        if args.skip_certbot:
            statuses["dns"] = "skipped"
            statuses["certbot"] = "skipped"
            log("[5/6] Certbot step skipped by --skip-certbot")
            if not args.skip_automation:
                log("[extra] Installing and enabling automation services")
                setup_automation_units(root_dir)
            else:
                log("[extra] Automation services skipped by --skip-automation")
            return

        log("[5/6] Checking DNS readiness before certbot")
        dns_preflight_or_die(args, statuses)

        log("[6/6] Provisioning TLS certificate with certbot")
        certbot_cmd = [
            "bash",
            str(root_dir / "scripts/setup-letsencrypt.sh"),
            "--domain",
            args.domain,
            "--email",
            args.email,
        ]
        if args.www:
            certbot_cmd.append("--www")
        run_checked(certbot_cmd, root_dir)
        statuses["certbot"] = "ok"

        if not args.skip_automation:
            log("[extra] Installing and enabling automation services")
            setup_automation_units(root_dir)
        else:
            log("[extra] Automation services skipped by --skip-automation")
    finally:
        print_summary(args, statuses)


if __name__ == "__main__":
    main()
