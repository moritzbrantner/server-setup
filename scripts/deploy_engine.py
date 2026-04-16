#!/usr/bin/env python3
from __future__ import annotations

import dataclasses
import datetime as dt
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from registry_contract import upsert_registry_entry
from server_conf_contract import normalize_server_conf
from simple_setup_common import (
    AUTOMATION_ENV_FILE,
    detect_webhook_url,
    ensure_dns_points_here,
    generate_webhook_secret,
    github_repo_full_name,
    git_command_with_github_auth,
    load_env_file,
    maybe_allow_ufw_port,
    maybe_configure_github_webhook,
    merge_csv_values,
    update_env_file,
)


class DeployError(RuntimeError):
    """Raised when a deploy fails."""


DEFAULT_BUN_INSTALL = "/root/.bun"


@dataclasses.dataclass(frozen=True)
class DeployResult:
    name: str
    domain: str
    service_name: str
    checkout_path: str
    branch: str
    webhook_repo: str
    webhook_url: str
    webhook_secret: str
    hook_status: tuple[str, str]


@dataclasses.dataclass
class DeployContext:
    state_dir: Path = dataclasses.field(default_factory=lambda: Path(os.environ.get("STATE_DIR", "/var/lib/server-setup/state")))
    lock_dir: Path = dataclasses.field(default_factory=lambda: Path(os.environ.get("LOCK_DIR", "/var/lock/server-setup")))
    log_dir: Path = dataclasses.field(default_factory=lambda: Path(os.environ.get("LOG_DIR", "/var/log/server-setup")))
    nginx_site_available_dir: Path = dataclasses.field(
        default_factory=lambda: Path(os.environ.get("NGINX_SITE_AVAILABLE_DIR", "/etc/nginx/sites-available"))
    )
    nginx_site_enabled_dir: Path = dataclasses.field(
        default_factory=lambda: Path(os.environ.get("NGINX_SITE_ENABLED_DIR", "/etc/nginx/sites-enabled"))
    )
    nginx_default_site_link: Path = dataclasses.field(
        default_factory=lambda: Path(os.environ.get("NGINX_DEFAULT_SITE_LINK", "/etc/nginx/sites-enabled/default"))
    )
    systemd_unit_dir: Path = dataclasses.field(
        default_factory=lambda: Path(os.environ.get("SYSTEMD_UNIT_DIR", "/etc/systemd/system"))
    )
    letsencrypt_live_dir: Path = dataclasses.field(
        default_factory=lambda: Path(os.environ.get("LETSENCRYPT_LIVE_DIR", "/etc/letsencrypt/live"))
    )
    letsencrypt_options_path: Path = dataclasses.field(
        default_factory=lambda: Path(os.environ.get("LETSENCRYPT_OPTIONS_PATH", "/etc/letsencrypt/options-ssl-nginx.conf"))
    )
    letsencrypt_dhparam_path: Path = dataclasses.field(
        default_factory=lambda: Path(os.environ.get("LETSENCRYPT_DHPARAM_PATH", "/etc/letsencrypt/ssl-dhparams.pem"))
    )

    def init_runtime_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def state_file(self, site_name: str) -> Path:
        return self.state_dir / f"{site_name}.json"

    def log_event(self, site: str, action: str, result: str, message: str, level: str = "info") -> None:
        line = json.dumps(
            {
                "timestamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "site": site,
                "action": action,
                "result": result,
                "level": level,
                "message": message,
            },
            separators=(",", ":"),
        )
        print(line)
        with (self.log_dir / f"deploy-{dt.datetime.now():%Y%m%d}.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")


def repo_basename(repo_url: str) -> str:
    if repo_url.startswith("git@"):
        return Path(repo_url.rsplit(":", 1)[-1]).name.removesuffix(".git")
    parsed = urllib.parse.urlparse(repo_url)
    return Path(parsed.path).name.removesuffix(".git")


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=capture,
        check=False,
    )


def run_checked(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = run(cmd, cwd=cwd, env=env, capture=True)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise DeployError(f"Command failed: {' '.join(cmd)}")
    return result


def utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_bun_install(env: dict[str, str]) -> str:
    configured = (env.get("BUN_INSTALL") or "").strip()
    if configured:
        return configured
    if os.geteuid() == 0:
        return DEFAULT_BUN_INSTALL
    home = (env.get("HOME") or str(Path.home())).strip()
    return f"{home}/.bun" if home else DEFAULT_BUN_INSTALL


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    bun_install = default_bun_install(env)
    bun_bin = f"{bun_install}/bin"
    env["BUN_INSTALL"] = bun_install
    path_entries = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    if bun_bin not in path_entries:
        env["PATH"] = f"{bun_bin}{os.pathsep}{env['PATH']}" if env.get("PATH") else bun_bin
    return env


def _command_mentions_install(cmd: str | None) -> bool:
    if not cmd:
        return False
    lowered = cmd.lower()
    install_markers = (
        "bun install",
        "npm ci",
        "npm install",
        "pnpm install",
        "yarn install",
    )
    return any(marker in lowered for marker in install_markers)


def _command_needs_node_modules(cmd: str | None) -> bool:
    if not cmd:
        return False
    lowered = cmd.lower()
    needs_markers = (
        "bun run",
        "npm run",
        "npx ",
        "pnpm ",
        "yarn ",
        " next ",
        "next build",
        "next start",
    )
    return any(marker in lowered for marker in needs_markers)


def _detect_install_command(checkout_path: Path, hint_cmd: str | None) -> list[str] | None:
    if (checkout_path / "bun.lock").exists() or (checkout_path / "bun.lockb").exists():
        return ["bun", "install"]
    if (checkout_path / "package-lock.json").exists():
        return ["npm", "ci"]
    if (checkout_path / "pnpm-lock.yaml").exists():
        return ["pnpm", "install", "--frozen-lockfile"]
    if (checkout_path / "yarn.lock").exists():
        return ["yarn", "install", "--frozen-lockfile"]

    lowered = (hint_cmd or "").lower()
    if "bun" in lowered:
        return ["bun", "install"]
    if "pnpm" in lowered:
        return ["pnpm", "install"]
    if "yarn" in lowered:
        return ["yarn", "install"]
    if "npm" in lowered or "next " in lowered:
        return ["npm", "install"]
    return None


def maybe_install_node_dependencies(checkout_path: Path, *commands: str | None) -> None:
    if not (checkout_path / "package.json").is_file():
        return
    if (checkout_path / "node_modules").is_dir():
        return
    if any(_command_mentions_install(cmd) for cmd in commands):
        return
    hint_cmd = next((cmd for cmd in commands if _command_needs_node_modules(cmd)), None)
    if not hint_cmd:
        return
    install_cmd = _detect_install_command(checkout_path, hint_cmd)
    if not install_cmd:
        return
    run_checked(install_cmd, cwd=checkout_path, env=command_env())


def write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
    try:
        tmp.write(content)
        if not content.endswith("\n"):
            tmp.write("\n")
        tmp.close()
        shutil.copyfile(tmp.name, path)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    return True


def detect_current_branch(checkout_path: Path) -> str:
    head = run(["git", "-C", str(checkout_path), "symbolic-ref", "refs/remotes/origin/HEAD"], capture=True)
    if head.returncode == 0:
        ref = (head.stdout or "").strip()
        if ref.startswith("refs/remotes/origin/"):
            return ref.removeprefix("refs/remotes/origin/")
    current = run(["git", "-C", str(checkout_path), "rev-parse", "--abbrev-ref", "HEAD"], capture=True)
    if current.returncode == 0:
        branch = (current.stdout or "").strip()
        if branch and branch != "HEAD":
            return branch
    return "main"


def clone_or_update_checkout(repo_url: str, checkout_path: Path, branch_override: str) -> str:
    local_repo_url = False
    repo_candidate = Path(repo_url).expanduser()
    if "://" not in repo_url and not repo_url.startswith("git@"):
        try:
            local_repo_url = repo_candidate.resolve() == checkout_path.resolve()
        except FileNotFoundError:
            local_repo_url = False

    if checkout_path.exists() and not (checkout_path / ".git").is_dir():
        raise DeployError(f"Destination exists but is not a git repository: {checkout_path}")

    if not checkout_path.exists():
        checkout_path.parent.mkdir(parents=True, exist_ok=True)
        run_checked(git_command_with_github_auth(repo_url, "clone", repo_url, str(checkout_path)))
    else:
        existing_origin = run(["git", "-C", str(checkout_path), "remote", "get-url", "origin"], capture=True)
        origin = (existing_origin.stdout or "").strip()
        if origin and origin != repo_url and not local_repo_url:
            raise DeployError(
                f"Destination repository origin mismatch.\n  existing: {origin}\n  expected: {repo_url}"
            )
        if not origin and not local_repo_url:
            run_checked(["git", "-C", str(checkout_path), "remote", "add", "origin", repo_url])
        if not local_repo_url:
            run_checked(git_command_with_github_auth(repo_url, "-C", str(checkout_path), "fetch", "--prune", "origin"))

    branch = branch_override or detect_current_branch(checkout_path)
    run_checked(["git", "-C", str(checkout_path), "checkout", branch])
    if not local_repo_url:
        run_checked(["git", "-C", str(checkout_path), "reset", "--hard", f"origin/{branch}"])
    return branch


def render_systemd_unit(name: str, command: str, working_dir: str, run_user: str, env_file: str) -> str:
    shell_payload = shlex.quote(
        'set -euo pipefail; export BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"; '
        'export PATH="$BUN_INSTALL/bin:$PATH"; ' + command
    )
    env_line = f"EnvironmentFile={env_file}\n" if env_file else ""
    return (
        "[Unit]\n"
        f"Description=Runtime service for {name}\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={working_dir}\n"
        f"ExecStart=/usr/bin/env bash -lc {shell_payload}\n"
        "Restart=always\n"
        "RestartSec=3\n"
        f"User={run_user}\n"
        f"{env_line}"
        "\n[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def render_nginx_site_config(
    ctx: DeployContext,
    site_name: str,
    domain: str,
    runtime_mode: str,
    static_root: str,
    runtime_port: str,
    www_redirect: bool,
    tls_hostnames: list[str],
) -> str:
    server_names = tls_hostnames or [domain]
    apex_names = [name for name in server_names if name != f"www.{domain}"] if www_redirect else server_names
    if not apex_names:
        apex_names = [domain]

    cert_dir = ctx.letsencrypt_live_dir / domain
    cert_fullchain = cert_dir / "fullchain.pem"
    cert_privkey = cert_dir / "privkey.pem"
    has_tls = (
        cert_fullchain.is_file()
        and cert_privkey.is_file()
        and ctx.letsencrypt_options_path.is_file()
        and ctx.letsencrypt_dhparam_path.is_file()
    )

    if runtime_mode == "service":
        location_block = (
            "    location / {\n"
            "        proxy_http_version 1.1;\n"
            "        proxy_set_header Host $host;\n"
            "        proxy_set_header X-Real-IP $remote_addr;\n"
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "        proxy_set_header X-Forwarded-Proto $scheme;\n"
            "        proxy_set_header Upgrade $http_upgrade;\n"
            '        proxy_set_header Connection "upgrade";\n'
            f"        proxy_pass http://127.0.0.1:{runtime_port};\n"
            "    }\n"
        )
    else:
        location_block = (
            f"    root {static_root};\n"
            "    index index.html;\n\n"
            "    location / {\n"
            "        try_files $uri $uri/ /index.html;\n"
            "    }\n"
        )

    redirect_block = ""
    if www_redirect:
        redirect_block += (
            "\nserver {\n"
            "    listen 80;\n"
            "    listen [::]:80;\n"
            f"    server_name www.{domain};\n"
            f"    return 301 https://{domain}$request_uri;\n"
            "}\n"
        )
        if has_tls:
            redirect_block += (
                "server {\n"
                "    listen 443 ssl;\n"
                "    listen [::]:443 ssl;\n"
                f"    server_name www.{domain};\n\n"
                f"    ssl_certificate {cert_fullchain};\n"
                f"    ssl_certificate_key {cert_privkey};\n"
                f"    include {ctx.letsencrypt_options_path};\n"
                f"    ssl_dhparam {ctx.letsencrypt_dhparam_path};\n\n"
                f"    return 301 https://{domain}$request_uri;\n"
                "}\n"
            )

    if has_tls:
        return (
            "server {\n"
            "    listen 80;\n"
            "    listen [::]:80;\n"
            f"    server_name {' '.join(apex_names)};\n\n"
            f"    return 301 https://{domain}$request_uri;\n"
            "}\n\n"
            "server {\n"
            "    listen 443 ssl;\n"
            "    listen [::]:443 ssl;\n"
            f"    server_name {' '.join(apex_names)};\n\n"
            f"    ssl_certificate {cert_fullchain};\n"
            f"    ssl_certificate_key {cert_privkey};\n"
            f"    include {ctx.letsencrypt_options_path};\n"
            f"    ssl_dhparam {ctx.letsencrypt_dhparam_path};\n\n"
            f"{location_block}\n"
            f"    access_log /var/log/nginx/{site_name}.access.log;\n"
            f"    error_log  /var/log/nginx/{site_name}.error.log;\n"
            "}\n"
            f"{redirect_block}"
        )

    return (
        "server {\n"
        "    listen 80;\n"
        "    listen [::]:80;\n"
        f"    server_name {' '.join(apex_names)};\n\n"
        f"{location_block}\n"
        f"    access_log /var/log/nginx/{site_name}.access.log;\n"
        f"    error_log  /var/log/nginx/{site_name}.error.log;\n"
        "}\n"
        f"{redirect_block}"
    )


def ensure_runtime_service(ctx: DeployContext, site_name: str, deploy_config: dict, checkout_path: Path) -> None:
    runtime = deploy_config["runtime"]
    if runtime["mode"] != "service":
        return
    unit_path = ctx.systemd_unit_dir / deploy_config["service"]["name"]
    working_dir = checkout_path if runtime["working_dir"] == "." else checkout_path / runtime["working_dir"]
    content = render_systemd_unit(
        site_name,
        runtime["command"],
        str(working_dir),
        runtime["user"],
        runtime.get("env_file", ""),
    )
    changed = write_if_changed(unit_path, content)
    if changed:
        run_checked(["systemctl", "daemon-reload"])
        run_checked(["systemctl", "enable", deploy_config["service"]["name"]])
    run_checked(["systemctl", "restart", deploy_config["service"]["name"]])


def wait_for_service_health(deploy_config: dict) -> None:
    runtime = deploy_config["runtime"]
    if runtime["mode"] != "service":
        return
    url = f"http://127.0.0.1:{runtime['port']}{runtime['health_endpoint']}"
    for _ in range(runtime["health_retries"]):
        if run(["curl", "--silent", "--show-error", "--fail", "--max-time", "2", url]).returncode == 0:
            return
        time.sleep(runtime["health_interval_seconds"])
    raise DeployError(f"Health check failed for {deploy_config['name']}: {url}")


def apply_nginx_site_config(ctx: DeployContext, site_name: str, deploy_config: dict, checkout_path: Path) -> None:
    runtime = deploy_config["runtime"]
    if runtime["mode"] == "static":
        static_root = deploy_config.get("build_output") or deploy_config.get("web_root") or ""
        site_root = checkout_path / static_root
        if not site_root.is_dir():
            raise DeployError(f"Static root does not exist for {site_name}: {site_root}")
        root_value = str(site_root)
        port_value = ""
    else:
        root_value = ""
        port_value = str(runtime["port"])

    conf_path = ctx.nginx_site_available_dir / f"{site_name}.conf"
    link_path = ctx.nginx_site_enabled_dir / f"{site_name}.conf"
    backup_path = conf_path.with_suffix(".conf.last-good")
    ctx.nginx_site_available_dir.mkdir(parents=True, exist_ok=True)
    ctx.nginx_site_enabled_dir.mkdir(parents=True, exist_ok=True)
    if conf_path.exists():
        shutil.copyfile(conf_path, backup_path)
    content = render_nginx_site_config(
        ctx,
        site_name,
        deploy_config["domain"],
        runtime["mode"],
        root_value,
        port_value,
        deploy_config["nginx"]["www_redirect"],
        deploy_config["nginx"]["tls_hostnames"],
    )
    write_if_changed(conf_path, content)
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()
    os.symlink(conf_path, link_path)
    if ctx.nginx_default_site_link.is_symlink():
        ctx.nginx_default_site_link.unlink()

    if run(["nginx", "-t"]).returncode != 0:
        if backup_path.exists():
            shutil.copyfile(backup_path, conf_path)
        raise DeployError(f"Nginx validation failed for {site_name}")
    run_checked(["systemctl", "reload", "nginx"])


def run_optional(cmd: str | None, *, cwd: Path) -> None:
    if not cmd:
        return
    result = run(["bash", "-lc", cmd], cwd=cwd, env=command_env(), capture=True)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise DeployError(f"Optional command failed: {cmd}")


def write_state(ctx: DeployContext, site_name: str, **updates: object) -> None:
    path = ctx.state_file(site_name)
    body = {}
    if path.exists():
        body = json.loads(path.read_text(encoding="utf-8"))
    body.update(updates)
    body.setdefault("site", site_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def deploy_registry_entry(
    entry: dict,
    *,
    tls_email: str,
    configure_webhook: bool,
    webhook_secret: str = "",
    webhook_url: str = "",
) -> DeployResult:
    ctx = DeployContext()
    ctx.init_runtime_dirs()
    site_name = str(entry["name"])
    lock_path = ctx.lock_dir / f"{site_name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        checkout_path = Path(str(entry["checkout_path"]))
        deploy_config = dict(entry["deploy_config"])
        deploy_started_at = utc_timestamp()
        write_state(
            ctx,
            site_name,
            last_deploy_timestamp=deploy_started_at,
            last_deploy_status="running",
            current_release=str(checkout_path),
            checkout_path=str(checkout_path),
            last_attempted_release=str(checkout_path),
        )
        ctx.log_event(site_name, "deploy", "running", str(checkout_path))
        stage = "pre_deploy hook"
        try:
            run_optional(deploy_config["deploy_hooks"].get("pre_deploy"), cwd=checkout_path)
            stage = "dependency install"
            maybe_install_node_dependencies(
                checkout_path,
                deploy_config["deploy_hooks"].get("build"),
                deploy_config["runtime"].get("command"),
            )
            stage = "build hook"
            run_optional(deploy_config["deploy_hooks"].get("build"), cwd=checkout_path)
            stage = "runtime service"
            ensure_runtime_service(ctx, site_name, deploy_config, checkout_path)
            stage = "health check"
            wait_for_service_health(deploy_config)
            stage = "nginx config"
            apply_nginx_site_config(ctx, site_name, deploy_config, checkout_path)
            include_www = deploy_config["nginx"]["www_redirect"] or f"www.{deploy_config['domain']}" in deploy_config["nginx"]["tls_hostnames"]
            stage = "dns verification"
            ensure_dns_points_here(deploy_config["domain"], include_www=include_www)
            stage = "tls setup"
            run_checked(
                [
                    "python3",
                    str(Path(__file__).resolve().parent / "setup_letsencrypt.py"),
                    "--domain",
                    deploy_config["domain"],
                    "--email",
                    tls_email,
                    *(["--www"] if include_www else []),
                ]
            )
            stage = "post_deploy hook"
            run_optional(deploy_config["deploy_hooks"].get("post_deploy"), cwd=checkout_path)
        except Exception as exc:
            failure_reason = str(exc).strip()
            failure_message = f"{stage}: {failure_reason}" if failure_reason else stage
            write_state(
                ctx,
                site_name,
                last_deploy_status="failed",
                current_release=str(checkout_path),
                checkout_path=str(checkout_path),
                last_attempted_release=str(checkout_path),
                last_failure_reason=failure_message,
                last_failure_at=utc_timestamp(),
            )
            ctx.log_event(site_name, "deploy", "failed", failure_message, level="error")
            raise

        write_state(
            ctx,
            site_name,
            last_deploy_status="success",
            current_release=str(checkout_path),
            last_successful_release=str(checkout_path),
            checkout_path=str(checkout_path),
            last_success_at=utc_timestamp(),
            last_failure_reason=None,
            last_failure_at=None,
        )

        hook_status = ("skipped", "webhook setup was skipped")
        secret = webhook_secret or load_env_file(AUTOMATION_ENV_FILE).get("WEBHOOK_SECRET") or generate_webhook_secret()
        detected_webhook_url = detect_webhook_url(webhook_url)
        if configure_webhook:
            hook_status = maybe_configure_github_webhook(
                str(entry.get("webhook_repo") or github_repo_full_name(str(entry["repo_url"]))),
                detected_webhook_url,
                secret,
            )
            maybe_allow_ufw_port("9001/tcp")
        ctx.log_event(site_name, "deploy", "success", str(checkout_path))
        return DeployResult(
            name=site_name,
            domain=str(entry["domain"]),
            service_name=str(entry["service_name"]),
            checkout_path=str(checkout_path),
            branch=str(entry["branch"]),
            webhook_repo=str(entry.get("webhook_repo") or ""),
            webhook_url=detected_webhook_url,
            webhook_secret=secret,
            hook_status=hook_status,
        )


def build_registry_entry(
    registry_path: str | Path,
    repo_url: str,
    branch: str,
    checkout_path: str | Path,
) -> dict:
    normalized = normalize_server_conf(checkout_path)
    return upsert_registry_entry(registry_path, repo_url, branch, checkout_path, normalized)


def update_automation_env(
    *,
    repo_root: Path,
    registry_path: Path,
    webhook_secret: str,
    repo_url: str,
    branch: str,
    checkout_path: Path,
    default_tls_email: str,
) -> tuple[str, str]:
    env_before = load_env_file(AUTOMATION_ENV_FILE)
    repo_full_name = github_repo_full_name(repo_url)
    webhook_url = detect_webhook_url("")
    updates = {
        "REPO_ROOT": str(repo_root),
        "REGISTRY_PATH": str(registry_path),
        "WEBHOOK_SECRET": webhook_secret,
        "WEBHOOK_ALLOW_INSECURE": "false",
        "DEFAULT_TLS_EMAIL": default_tls_email,
    }
    if repo_full_name:
        updates["WEBHOOK_ALLOWED_REPOS"] = merge_csv_values(env_before.get("WEBHOOK_ALLOWED_REPOS", ""), [repo_full_name])
    if branch:
        updates["WEBHOOK_ALLOWED_BRANCHES"] = merge_csv_values(env_before.get("WEBHOOK_ALLOWED_BRANCHES", ""), [branch])
    updates["LAST_CHECKOUT_PATH"] = str(checkout_path)
    update_env_file(AUTOMATION_ENV_FILE, updates)
    return repo_full_name, webhook_url
