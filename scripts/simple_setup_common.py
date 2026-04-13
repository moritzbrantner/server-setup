#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import urllib.parse
from pathlib import Path


AUTOMATION_ENV_FILE = Path(os.environ.get("SITE_AUTOMATION_ENV_FILE", "/etc/default/site-automation"))
WEBHOOK_PATH = "/github/push"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This script must be run as root (use sudo).")


def run_checked(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    allow_fail: bool = False,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout and not capture:
        sys.stdout.write(result.stdout)
    if result.stderr and not capture:
        sys.stderr.write(result.stderr)
    if result.returncode != 0 and not allow_fail:
        raise SystemExit(result.returncode)
    return result


def setup_automation_units(root_dir: Path, *, start_webhook: bool) -> Path:
    systemd_dir = root_dir / "ops/systemd"
    env_file = AUTOMATION_ENV_FILE
    if not systemd_dir.is_dir():
        raise SystemExit(f"Automation units directory not found: {systemd_dir}")

    for name in (
        "site-discovery-deploy.service",
        "site-apps-watcher.service",
        "site-webhook-receiver.service",
        "site-discovery-deploy.timer",
    ):
        shutil.copyfile(systemd_dir / name, Path("/etc/systemd/system") / name)

    if not env_file.exists():
        template = systemd_dir / "site-automation.env.example"
        shutil.copyfile(template, env_file)
        update_env_file(
            env_file,
            {
                "REPO_ROOT": str(root_dir),
                "CONFIG_PATH": str(root_dir / "deploy/sites.json"),
            },
        )

    run_checked(["systemctl", "daemon-reload"])
    run_checked(["systemctl", "enable", "--now", "site-discovery-deploy.timer"])
    run_checked(["systemctl", "enable", "--now", "site-apps-watcher.service"])
    if start_webhook:
        run_checked(["systemctl", "enable", "--now", "site-webhook-receiver.service"])
    else:
        run_checked(["systemctl", "enable", "site-webhook-receiver.service"], allow_fail=True)
    return env_file


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    rendered: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rendered.append(line)
            continue
        key, _ = line.split("=", 1)
        clean_key = key.strip()
        if clean_key in pending:
            rendered.append(f"{clean_key}={pending.pop(clean_key)}")
        else:
            rendered.append(line)

    if pending and rendered and rendered[-1] != "":
        rendered.append("")
    for key, value in pending.items():
        rendered.append(f"{key}={value}")

    body = "\n".join(rendered).rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def csv_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def merge_csv_values(existing: str, additions: list[str]) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*csv_items(existing), *additions]:
        if item in seen:
            continue
        merged.append(item)
        seen.add(item)
    return ",".join(merged)


def github_repo_full_name(repo_url: str) -> str:
    if repo_url.startswith("git@github.com:"):
        path = repo_url.removeprefix("git@github.com:").removesuffix(".git")
        parts = [part for part in path.split("/") if part]
        return "/".join(parts[:2]) if len(parts) >= 2 else ""

    parsed = urllib.parse.urlparse(repo_url)
    if parsed.hostname != "github.com":
        return ""
    parts = [part for part in parsed.path.removesuffix(".git").split("/") if part]
    return "/".join(parts[:2]) if len(parts) >= 2 else ""


def github_auth_token_from_gh() -> str:
    if shutil.which("gh") is None:
        return ""

    candidates: list[list[str]] = [["gh", "auth", "token", "--hostname", "github.com"]]
    sudo_user = os.environ.get("SUDO_USER", "").strip()
    current_user = os.environ.get("USER", "").strip()
    if sudo_user and sudo_user != current_user:
        if shutil.which("sudo") is not None:
            candidates.append(["sudo", "-u", sudo_user, "-H", "gh", "auth", "token", "--hostname", "github.com"])
        elif shutil.which("runuser") is not None:
            candidates.append(["runuser", "-u", sudo_user, "--", "gh", "auth", "token", "--hostname", "github.com"])

    seen: set[tuple[str, ...]] = set()
    for cmd in candidates:
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        result = subprocess.run(cmd, text=True, capture_output=True, check=False)
        token = (result.stdout or "").strip()
        if result.returncode == 0 and token:
            return token
    return ""


def git_command_with_github_auth(repo_url: str, *git_args: str) -> list[str]:
    cmd = ["git"]
    parsed = urllib.parse.urlparse(repo_url)
    if (
        parsed.scheme in {"http", "https"}
        and parsed.hostname == "github.com"
        and parsed.username is None
        and parsed.password is None
    ):
        token = github_auth_token_from_gh()
        if token:
            basic_auth = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
            cmd.extend(
                [
                    "-c",
                    f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic_auth}",
                ]
            )
    cmd.extend(git_args)
    return cmd


def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


def detect_public_ip() -> str:
    for url in ("https://api.ipify.org", "https://ifconfig.me"):
        result = subprocess.run(
            ["curl", "-4fsS", "--max-time", "5", url],
            text=True,
            capture_output=True,
            check=False,
        )
        candidate = result.stdout.strip()
        if candidate:
            return candidate
    return ""


def detect_webhook_url(explicit_url: str) -> str:
    if explicit_url:
        return explicit_url
    public_ip = detect_public_ip()
    if not public_ip:
        return ""
    return f"http://{public_ip}:9001{WEBHOOK_PATH}"


def maybe_allow_ufw_port(rule: str) -> None:
    if shutil.which("ufw") is None:
        return
    status = subprocess.run(["ufw", "status"], text=True, capture_output=True, check=False)
    if "Status: active" not in status.stdout:
        return
    run_checked(["ufw", "allow", rule], allow_fail=True)


def maybe_configure_github_webhook(repo_full_name: str, payload_url: str, secret: str) -> tuple[str, str]:
    if not repo_full_name:
        return ("skipped", "repository is not hosted on github.com")
    if not payload_url:
        return ("skipped", "webhook URL could not be determined")
    if shutil.which("gh") is None:
        return ("skipped", "GitHub CLI is not installed")

    auth = run_checked(["gh", "auth", "status", "--hostname", "github.com"], allow_fail=True, capture=True)
    if auth.returncode != 0:
        return ("skipped", "run 'gh auth login' to let this script create the GitHub webhook automatically")

    hooks_response = run_checked(
        ["gh", "api", f"repos/{repo_full_name}/hooks"],
        allow_fail=True,
        capture=True,
    )
    if hooks_response.returncode != 0:
        return ("skipped", "unable to query existing GitHub hooks with gh")

    try:
        hooks = json.loads(hooks_response.stdout or "[]")
    except json.JSONDecodeError:
        hooks = []

    matching = next(
        (
            hook
            for hook in hooks
            if isinstance(hook, dict)
            and (hook.get("config") or {}).get("url") == payload_url
        ),
        None,
    )
    cmd = [
        "gh",
        "api",
        f"repos/{repo_full_name}/hooks/{matching['id']}" if matching else f"repos/{repo_full_name}/hooks",
        "--method",
        "PATCH" if matching else "POST",
    ]
    if not matching:
        cmd.extend(["-f", "name=web"])
    cmd.extend(
        [
            "-f",
            f"config[url]={payload_url}",
            "-f",
            "config[content_type]=json",
            "-f",
            f"config[secret]={secret}",
            "-f",
            "events[]=push",
            "-f",
            "active=true",
        ]
    )
    result = run_checked(cmd, allow_fail=True, capture=True)
    if result.returncode != 0:
        return ("skipped", "GitHub webhook was not created automatically; create it manually with the printed URL and secret")
    return ("ok", "GitHub webhook created or updated")


def collect_local_ips() -> set[str]:
    ips: set[str] = set()
    hostname_result = subprocess.run(["hostname", "-I"], text=True, capture_output=True, check=False)
    ips.update(ip for ip in hostname_result.stdout.split() if ip)
    public_ip = detect_public_ip()
    if public_ip:
        ips.add(public_ip)
    return ips


def collect_domain_ips(name: str) -> set[str]:
    try:
        return {entry[4][0] for entry in socket.getaddrinfo(name, None)}
    except socket.gaierror:
        return set()


def ensure_dns_points_here(domain: str, *, include_www: bool) -> None:
    local_ips = collect_local_ips()
    hosts = [domain]
    if include_www:
        hosts.append(f"www.{domain}")

    problems: list[str] = []
    for host in hosts:
        resolved = collect_domain_ips(host)
        if not resolved:
            problems.append(f"{host} does not resolve yet")
            continue
        if local_ips and not (resolved & local_ips):
            problems.append(f"{host} resolves to {', '.join(sorted(resolved))}, not this server")

    if problems:
        detail = "\n".join(f"- {problem}" for problem in problems)
        raise SystemExit(
            "DNS is not ready for certificate issuance.\n"
            f"{detail}\n"
            "Fix the DNS records and run this command again."
        )
