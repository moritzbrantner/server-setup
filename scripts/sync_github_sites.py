#!/usr/bin/env python3
from __future__ import annotations

import argparse
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

from simple_setup_common import git_command_with_github_auth


def utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def usage_description() -> str:
    return (
        "Pulls website repos from Git, deploys into timestamped releases, "
        "runs preflight validation, and persists deployment state."
    )


class SyncError(RuntimeError):
    pass


def first_config_value(*values: object) -> object:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value.strip()
            continue
        return value
    return ""


class SyncContext:
    def __init__(self, args: argparse.Namespace) -> None:
        self.script_dir = Path(__file__).resolve().parent
        self.config_path = Path(args.config)
        self.discover_base = args.discover_base or ""
        self.only_site = args.site or ""
        self.rollback_site = args.rollback or ""
        self.dry_run = args.dry_run
        self.preflight_only = args.preflight_only
        self.json_status = args.json_status

        self.state_dir = Path(os.environ.get("STATE_DIR", "/var/lib/server-setup/state"))
        self.lock_dir = Path(os.environ.get("LOCK_DIR", "/var/lock/server-setup"))
        self.log_dir = Path(os.environ.get("LOG_DIR", "/var/log/server-setup"))
        self.log_retention_days = int(os.environ.get("LOG_RETENTION_DAYS", "14"))
        self.nginx_site_available_dir = Path(
            os.environ.get("NGINX_SITE_AVAILABLE_DIR", "/etc/nginx/sites-available")
        )
        self.nginx_site_enabled_dir = Path(
            os.environ.get("NGINX_SITE_ENABLED_DIR", "/etc/nginx/sites-enabled")
        )
        self.nginx_default_site_link = Path(
            os.environ.get("NGINX_DEFAULT_SITE_LINK", "/etc/nginx/sites-enabled/default")
        )
        self.systemd_unit_dir = Path(os.environ.get("SYSTEMD_UNIT_DIR", "/etc/systemd/system"))
        self.letsencrypt_live_dir = Path(
            os.environ.get("LETSENCRYPT_LIVE_DIR", "/etc/letsencrypt/live")
        )
        self.letsencrypt_options_path = Path(
            os.environ.get("LETSENCRYPT_OPTIONS_PATH", "/etc/letsencrypt/options-ssl-nginx.conf")
        )
        self.letsencrypt_dhparam_path = Path(
            os.environ.get("LETSENCRYPT_DHPARAM_PATH", "/etc/letsencrypt/ssl-dhparams.pem")
        )
        self.log_file = self.log_dir / f"deploy-{dt.datetime.now().strftime('%Y%m%d')}.log"

    def require_cmd(self, name: str) -> None:
        if shutil.which(name) is None:
            raise SyncError(f"Missing required command: {name}")

    def init_runtime_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - self.log_retention_days * 24 * 60 * 60
        for path in self.log_dir.glob("*.log"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                continue

    def log_event(self, site: str, action: str, result: str, message: str, level: str = "info") -> None:
        line = json.dumps(
            {
                "timestamp": utc_timestamp(),
                "site": site or None,
                "action": action,
                "result": result,
                "level": level,
                "message": message,
            },
            separators=(",", ":"),
        )
        print(line)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def state_file(self, site_name: str) -> Path:
        return self.state_dir / f"{site_name}.json"

    def state_asset_path(self, site_name: str, asset_name: str) -> Path:
        return self.state_dir / f"{site_name}-{asset_name}"

    def read_state_json(self, site_name: str) -> dict:
        path = self.state_file(site_name)
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"site": site_name, "last_deploy_status": "never-run"}

    def write_state_json(self, site_name: str, body: dict) -> None:
        path = self.state_file(site_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        try:
            json.dump(body, tmp, indent=2, sort_keys=False)
            tmp.write("\n")
            tmp.close()
            os.replace(tmp.name, path)
        finally:
            try:
                os.unlink(tmp.name)
            except FileNotFoundError:
                pass


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=capture,
        check=False,
    )


def run_checked(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    result = run(cmd, cwd=cwd, env=env, capture=True)
    if result.returncode != 0:
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        raise SyncError(f"Command failed: {' '.join(cmd)}")
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


def load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_config_value(site_name: str, field_name: str, raw_value: object) -> object:
    if raw_value in (None, ""):
        return raw_value
    if not isinstance(raw_value, str):
        return raw_value
    resolved = raw_value
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
    while True:
        match = pattern.search(resolved)
        if not match:
            break
        env_name = match.group(1)
        env_value = os.environ.get(env_name, "")
        if not env_value:
            raise SyncError(
                f"Missing required environment variable '{env_name}' for site '{site_name}' field '{field_name}'."
            )
        resolved = resolved.replace(f"${{{env_name}}}", env_value)
    return resolved


def site_runtime_bin(runtime_command: str) -> str:
    for token in shlex.split(runtime_command):
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", token):
            continue
        return token
    return ""


def path_is_writable_or_creatable(path: Path) -> bool:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return os.access(probe, os.W_OK)
    except PermissionError:
        return False


def write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
    try:
        tmp.write(content)
        if not content.endswith("\n"):
            tmp.write("\n")
        tmp.close()
        shutil.copyfile(tmp.name, path)
    finally:
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass
    return True


def state_mark_attempt(ctx: SyncContext, site_name: str, release_dir: Path) -> None:
    body = ctx.read_state_json(site_name)
    body["last_attempted_release"] = str(release_dir)
    body["last_deploy_timestamp"] = utc_timestamp()
    body["last_deploy_status"] = "running"
    body["last_failure_reason"] = None
    ctx.write_state_json(site_name, body)


def state_mark_health(ctx: SyncContext, site_name: str, status: str, url: str, message: str) -> None:
    body = ctx.read_state_json(site_name)
    body["last_health_check"] = {
        "status": status,
        "url": url,
        "message": message,
        "checked_at": utc_timestamp(),
    }
    ctx.write_state_json(site_name, body)


def state_mark_failure(ctx: SyncContext, site_name: str, reason: str) -> None:
    body = ctx.read_state_json(site_name)
    body["last_deploy_status"] = "failed"
    body["last_failure_reason"] = reason
    body["last_failure_at"] = utc_timestamp()
    ctx.write_state_json(site_name, body)


def state_mark_success(ctx: SyncContext, site_name: str, release_dir: Path) -> None:
    body = ctx.read_state_json(site_name)
    body["previous_successful_release"] = body.get("last_successful_release")
    body["last_successful_release"] = str(release_dir)
    body["current_release"] = str(release_dir)
    body["last_deploy_status"] = "success"
    body["last_failure_reason"] = None
    body["last_success_at"] = utc_timestamp()
    ctx.write_state_json(site_name, body)


def state_mark_rollback(ctx: SyncContext, site_name: str, release_dir: Path) -> None:
    body = ctx.read_state_json(site_name)
    body["current_release"] = str(release_dir)
    body["last_rollback_timestamp"] = utc_timestamp()
    body["last_deploy_status"] = "rolled-back"
    ctx.write_state_json(site_name, body)


def shell_env_with_git_ssh(git_ssh_command: str) -> dict[str, str] | None:
    if not git_ssh_command or git_ssh_command == "null":
        return None
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = git_ssh_command
    return env


def github_repo_url_with_token(repo: str, username: str, token: str) -> str:
    if not token:
        return repo

    safe_username = urllib.parse.quote(username or "x-access-token", safe="")
    safe_token = urllib.parse.quote(token, safe="")

    if repo.startswith("git@github.com:"):
        repo = f"https://github.com/{repo.removeprefix('git@github.com:')}"

    parsed = urllib.parse.urlparse(repo)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "github.com":
        raise SyncError("repo_auth.github_token only supports GitHub HTTPS or git@github.com repository URLs.")

    netloc = f"{safe_username}:{safe_token}@{parsed.hostname}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.parse.urlunparse(parsed._replace(netloc=netloc))


def run_optional(ctx: SyncContext, cmd: str, where: str, site_name: str, cwd: Path) -> None:
    if not cmd or cmd == "null":
        return
    ctx.log_event(site_name, where, "running", cmd)
    result = run(["bash", "-lc", cmd], cwd=cwd, capture=True)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise SyncError(f"Optional command failed: {cmd}")
    ctx.log_event(site_name, where, "success", cmd)


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


def runtime_service_name(site_json: dict, site_name: str) -> str:
    service = site_json.get("service") or {}
    configured_name = service.get("name")
    if isinstance(configured_name, str) and configured_name.strip():
        return configured_name.strip()
    top_level_name = site_json.get("service_name")
    if isinstance(top_level_name, str) and top_level_name.strip():
        return top_level_name.strip()
    if any(
        key in site_json
        for key in (
            "command",
            "port",
            "build",
            "pre_deploy",
            "post_deploy",
            "www_redirect",
            "tls_hostnames",
        )
    ):
        return f"{site_name}.service"
    # Legacy fallback for older configs before service.name became canonical.
    return f"app-{site_name}.service"


def ensure_runtime_service(
    ctx: SyncContext,
    site_name: str,
    service_name: str,
    runtime_mode: str,
    runtime_command: str,
    runtime_working_dir: str,
    runtime_user: str,
    runtime_env_file: str,
) -> None:
    if runtime_mode != "service":
        return
    unit_path = ctx.systemd_unit_dir / service_name
    unit_content = render_systemd_unit(
        site_name, runtime_command, runtime_working_dir, runtime_user, runtime_env_file
    )
    changed = write_if_changed(unit_path, unit_content)
    if changed:
        ctx.log_event(site_name, "systemd-unit", "updated", str(unit_path))
        run_checked(["systemctl", "daemon-reload"])
        run_checked(["systemctl", "enable", service_name])
    else:
        ctx.log_event(site_name, "systemd-unit", "unchanged", str(unit_path))
    shutil.copyfile(unit_path, ctx.state_asset_path(site_name, "last-good-unit.service"))
    run_checked(["systemctl", "restart", service_name])


def render_nginx_site_config(
    ctx: SyncContext,
    site_name: str,
    domain: str,
    runtime_mode: str,
    static_root: str,
    runtime_port: str,
    www_redirect: bool,
    tls_hostnames_csv: str,
) -> str:
    server_names = tls_hostnames_csv or domain
    if www_redirect:
        server_names = " ".join(
            [name for name in server_names.split() if name and name != f"www.{domain}"]
        ) or domain

    cert_dir = ctx.letsencrypt_live_dir / domain
    cert_fullchain = cert_dir / "fullchain.pem"
    cert_privkey = cert_dir / "privkey.pem"
    has_tls = (
        cert_fullchain.is_file()
        and cert_privkey.is_file()
        and ctx.letsencrypt_options_path.is_file()
        and ctx.letsencrypt_dhparam_path.is_file()
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

    if runtime_mode == "service":
        location_block = (
            "    location / {\n"
            "        proxy_http_version 1.1;\n"
            "        proxy_set_header Host $host;\n"
            "        proxy_set_header X-Real-IP $remote_addr;\n"
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "        proxy_set_header X-Forwarded-Proto $scheme;\n"
            "        proxy_set_header X-Forwarded-Host $host;\n"
            "        proxy_set_header X-Forwarded-Port $server_port;\n"
            "        proxy_set_header Upgrade $http_upgrade;\n"
            '        proxy_set_header Connection "upgrade";\n'
            f"        proxy_pass http://127.0.0.1:{runtime_port};\n"
            "        proxy_read_timeout 60s;\n"
            "        proxy_send_timeout 60s;\n"
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

    if has_tls:
        return (
            "server {\n"
            "    listen 80;\n"
            "    listen [::]:80;\n"
            f"    server_name {server_names};\n\n"
            f"    return 301 https://{domain}$request_uri;\n"
            "}\n\n"
            "server {\n"
            "    listen 443 ssl;\n"
            "    listen [::]:443 ssl;\n"
            f"    server_name {server_names};\n\n"
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
        f"    server_name {server_names};\n\n"
        f"{location_block}\n"
        f"    access_log /var/log/nginx/{site_name}.access.log;\n"
        f"    error_log  /var/log/nginx/{site_name}.error.log;\n"
        "}\n"
        f"{redirect_block}"
    )


def apply_nginx_site_config(
    ctx: SyncContext,
    site_name: str,
    domain: str,
    runtime_mode: str,
    release_dir: Path,
    static_relative_root: str,
    runtime_port: str,
    www_redirect: bool,
    tls_hostnames_csv: str,
) -> None:
    site_conf = ctx.nginx_site_available_dir / f"{site_name}.conf"
    site_link = ctx.nginx_site_enabled_dir / f"{site_name}.conf"
    backup_conf = site_conf.with_suffix(site_conf.suffix + ".last-good")

    static_root = ""
    if runtime_mode == "static":
        if not static_relative_root:
            raise SyncError(f"Missing static root for site '{site_name}'.")
        static_root = str(release_dir) if static_relative_root == "/" else str(release_dir / static_relative_root)
        if not Path(static_root).is_dir():
            raise SyncError(f"Static root does not exist for site '{site_name}': {static_root}")

    conf_content = render_nginx_site_config(
        ctx, site_name, domain, runtime_mode, static_root, runtime_port, www_redirect, tls_hostnames_csv
    )
    ctx.nginx_site_available_dir.mkdir(parents=True, exist_ok=True)
    ctx.nginx_site_enabled_dir.mkdir(parents=True, exist_ok=True)
    if site_conf.is_file():
        shutil.copyfile(site_conf, backup_conf)
    write_if_changed(site_conf, conf_content)
    if site_link.exists() or site_link.is_symlink():
        site_link.unlink()
    os.symlink(site_conf, site_link)
    if ctx.nginx_default_site_link.is_symlink():
        ctx.nginx_default_site_link.unlink()

    if run(["nginx", "-t"]).returncode == 0:
        run_checked(["systemctl", "reload", "nginx"])
        shutil.copyfile(site_conf, backup_conf)
        shutil.copyfile(site_conf, ctx.state_asset_path(site_name, "last-good-nginx.conf"))
        ctx.log_event(site_name, "nginx-config", "success", str(site_conf))
        return

    ctx.log_event(site_name, "nginx-config", "failed", str(site_conf), "error")
    if backup_conf.is_file():
        shutil.copyfile(backup_conf, site_conf)
    else:
        if site_conf.exists():
            site_conf.unlink()
        if site_link.exists() or site_link.is_symlink():
            site_link.unlink()
    run(["nginx", "-t"])
    raise SyncError(f"Deploy aborted for '{site_name}' because Nginx config validation failed.")


def wait_for_service_health(
    ctx: SyncContext,
    site_name: str,
    runtime_mode: str,
    port: str,
    endpoint: str,
    attempts: int,
    delay: int,
) -> None:
    if runtime_mode != "service":
        state_mark_health(ctx, site_name, "not-applicable", "", "static deployment")
        return
    url = f"http://127.0.0.1:{port}{endpoint}"
    ctx.log_event(site_name, "health-check", "running", url)
    for _ in range(attempts):
        if run(["curl", "--silent", "--show-error", "--fail", "--max-time", "2", url]).returncode == 0:
            state_mark_health(ctx, site_name, "passing", url, "health check passed")
            ctx.log_event(site_name, "health-check", "success", url)
            return
        time.sleep(delay)
    state_mark_health(ctx, site_name, "failing", url, "health check failed")
    ctx.log_event(site_name, "health-check", "failed", url, "error")
    raise SyncError("Deploy aborted before traffic switch due to failing health check.")


def atomic_switch_symlink(symlink_path: Path, new_target: Path) -> None:
    tmp = Path(f"{symlink_path}.next")
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    os.symlink(new_target, tmp)
    os.replace(tmp, symlink_path)


def capture_current_target(symlink_path: Path) -> str:
    if symlink_path.is_symlink() or symlink_path.exists():
        try:
            return str(symlink_path.resolve())
        except FileNotFoundError:
            return ""
    return ""


def cleanup_old_releases(releases_dir: Path, keep_releases: int, current_target: str, previous_target: str) -> None:
    releases = sorted(path for path in releases_dir.iterdir() if path.is_dir()) if releases_dir.is_dir() else []
    if len(releases) <= keep_releases:
        return
    to_remove = len(releases) - keep_releases
    removed = 0
    for candidate in releases:
        if removed >= to_remove:
            break
        if str(candidate) in {current_target, previous_target}:
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        removed += 1


def preflight_site(
    ctx: SyncContext,
    site_name: str,
    deploy_mode: str,
    service_name: str,
    runtime_mode: str,
    runtime_command: str,
    workdir: Path,
    releases_dir: Path,
    current_symlink: Path,
    runtime_port: str,
    runtime_working_dir: str,
) -> None:
    checks = [
        (workdir, f"workdir is not writable/creatable: {workdir}"),
        (ctx.nginx_site_available_dir / f"{site_name}.conf", "nginx site directory is not writable"),
    ]
    if deploy_mode != "checkout":
        checks.extend(
            [
                (releases_dir, f"releases_dir is not writable/creatable: {releases_dir}"),
                (current_symlink, f"current_symlink is not writable/creatable: {current_symlink}"),
            ]
        )
    for path, message in checks:
        if not path_is_writable_or_creatable(path):
            raise SyncError(f"Preflight failed for '{site_name}': {message}")

    if runtime_mode == "service":
        if not runtime_working_dir:
            raise SyncError(f"Preflight failed for '{site_name}': runtime.working_dir cannot be empty")
        if runtime_working_dir.startswith("/"):
            raise SyncError(
                f"Preflight failed for '{site_name}': runtime.working_dir must be relative to the deployed release, got '{runtime_working_dir}'"
            )
        if not path_is_writable_or_creatable(ctx.systemd_unit_dir / service_name):
            raise SyncError(f"Preflight failed for '{site_name}': systemd unit directory is not writable")
        runtime_bin = site_runtime_bin(runtime_command)
        if not runtime_bin or shutil.which(runtime_bin) is None:
            raise SyncError(f"Preflight failed for '{site_name}': missing runtime binary '{runtime_bin}'")
        if not runtime_port:
            raise SyncError(f"Preflight failed for '{site_name}': runtime.port is required for service mode")

    if shutil.which("nginx") is None:
        raise SyncError(f"Preflight failed for '{site_name}': missing nginx command")
    if run(["nginx", "-t"]).returncode != 0:
        raise SyncError(f"Preflight failed for '{site_name}': nginx -t failed")
    if shutil.which("systemctl") is None:
        raise SyncError(f"Preflight failed for '{site_name}': missing systemctl command")

    ctx.log_event(site_name, "preflight", "success", "validated deploy prerequisites")


def restore_last_good_files(ctx: SyncContext, site_name: str, service_name: str) -> None:
    nginx_backup = ctx.state_asset_path(site_name, "last-good-nginx.conf")
    unit_backup = ctx.state_asset_path(site_name, "last-good-unit.service")
    site_conf = ctx.nginx_site_available_dir / f"{site_name}.conf"
    site_link = ctx.nginx_site_enabled_dir / f"{site_name}.conf"

    if nginx_backup.is_file():
        ctx.nginx_site_available_dir.mkdir(parents=True, exist_ok=True)
        ctx.nginx_site_enabled_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(nginx_backup, site_conf)
        if site_link.exists() or site_link.is_symlink():
            site_link.unlink()
        os.symlink(site_conf, site_link)
        if run(["nginx", "-t"]).returncode == 0:
            run(["systemctl", "reload", "nginx"])

    if unit_backup.is_file():
        ctx.systemd_unit_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(unit_backup, ctx.systemd_unit_dir / service_name)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "restart", service_name])


def run_unlighthouse(
    ctx: SyncContext,
    site_name: str,
    site_url: str,
    unlighthouse_cmd: str,
    unlighthouse_server_url: str,
    unlighthouse_server_token: str,
) -> None:
    if os.environ.get("SKIP_UNLIGHTHOUSE", "0") == "1":
        ctx.log_event(site_name, "unlighthouse", "skipped", "SKIP_UNLIGHTHOUSE=1")
        return
    if unlighthouse_cmd and unlighthouse_cmd != "null":
        ctx.log_event(site_name, "unlighthouse", "running", unlighthouse_cmd)
        run_checked(["bash", "-lc", unlighthouse_cmd])
        ctx.log_event(site_name, "unlighthouse", "success", unlighthouse_cmd)
        return
    if not site_url or site_url == "null":
        ctx.log_event(site_name, "unlighthouse", "skipped", "site_url not configured")
        return
    ctx.require_cmd("bunx")
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = Path(f"/var/log/unlighthouse/{site_name}/{ts}")
    report_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["bunx", "--yes", "unlighthouse-ci@latest", "--site", site_url, "--output-path", str(report_dir)]
    if unlighthouse_server_url and unlighthouse_server_url != "null":
        cmd.extend(["--server", unlighthouse_server_url, "--build-name", site_name])
        if unlighthouse_server_token and unlighthouse_server_token != "null":
            cmd.extend(["--auth", unlighthouse_server_token])
    ctx.log_event(site_name, "unlighthouse", "running", f"site={site_url}")
    run_checked(cmd)
    ctx.log_event(site_name, "unlighthouse", "success", f"site={site_url}")


def resolve_site_fields(site_json: dict) -> dict:
    name = str(resolve_config_value("site", "name", site_json.get("name", "")))
    repo_auth = site_json.get("repo_auth") or {}
    runtime = site_json.get("runtime") or {}
    nginx = site_json.get("nginx") or {}
    domain = resolve_config_value(name, "domain", site_json.get("domain", ""))
    inferred_runtime_mode = first_config_value(
        runtime.get("mode"),
        runtime.get("type"),
        site_json.get("runtime_mode"),
        site_json.get("mode"),
    )
    if not inferred_runtime_mode:
        inferred_runtime_mode = (
            "service"
            if first_config_value(runtime.get("command"), site_json.get("command"), runtime.get("port"), site_json.get("port"))
            else "static"
        )
    fields = {
        "name": name,
        "deploy_mode": resolve_config_value(name, "deploy_mode", site_json.get("deploy_mode", "release")),
        "service_name": resolve_config_value(name, "service.name", runtime_service_name(site_json, name)),
        "repo": resolve_config_value(name, "repo", site_json.get("repo", "")),
        "branch": resolve_config_value(name, "branch", site_json.get("branch", "main")),
        "workdir": resolve_config_value(
            name,
            "workdir",
            first_config_value(site_json.get("workdir"), f"/srv/github-sites/{name}"),
        ),
        "releases_dir": resolve_config_value(name, "releases_dir", site_json.get("releases_dir", "")),
        "current_symlink": resolve_config_value(name, "current_symlink", site_json.get("current_symlink", "")),
        "keep_releases": int(resolve_config_value(name, "keep_releases", site_json.get("keep_releases", 5))),
        "site_url": resolve_config_value(name, "site_url", site_json.get("site_url", "")),
        "git_ssh_command": resolve_config_value(name, "git_ssh_command", site_json.get("git_ssh_command", "")),
        "deploy_script": resolve_config_value(name, "deploy_script", site_json.get("deploy_script", "")),
        "pre_deploy_cmd": resolve_config_value(
            name,
            "pre_deploy_cmd",
            first_config_value(site_json.get("pre_deploy_cmd"), site_json.get("pre_deploy")),
        ),
        "build_cmd": resolve_config_value(
            name,
            "build_cmd",
            first_config_value(site_json.get("build_cmd"), site_json.get("build")),
        ),
        "post_deploy_cmd": resolve_config_value(
            name,
            "post_deploy_cmd",
            first_config_value(site_json.get("post_deploy_cmd"), site_json.get("post_deploy"), site_json.get("reload_cmd")),
        ),
        "unlighthouse_cmd": resolve_config_value(name, "unlighthouse_cmd", site_json.get("unlighthouse_cmd", "")),
        "unlighthouse_server_url": resolve_config_value(
            name, "unlighthouse_server_url", site_json.get("unlighthouse_server_url", "")
        ),
        "unlighthouse_server_token": resolve_config_value(
            name, "unlighthouse_server_token", site_json.get("unlighthouse_server_token", "")
        ),
        "domain": domain,
        "web_root": resolve_config_value(
            name,
            "web_root",
            first_config_value(site_json.get("web_root"), site_json.get("root")),
        ),
        "build_output": resolve_config_value(
            name,
            "build_output",
            first_config_value(site_json.get("build_output"), site_json.get("output_dir")),
        ),
        "repo_auth_github_token": resolve_config_value(
            name, "repo_auth.github_token", repo_auth.get("github_token", "")
        ),
        "repo_auth_github_username": resolve_config_value(
            name, "repo_auth.github_username", repo_auth.get("github_username", "x-access-token")
        ),
    }
    fields.update(
        {
            "runtime_mode": resolve_config_value(name, "runtime.mode", inferred_runtime_mode),
            "runtime_command": resolve_config_value(
                name,
                "runtime.command",
                first_config_value(runtime.get("command"), site_json.get("command")),
            ),
            "runtime_working_dir": resolve_config_value(
                name,
                "runtime.working_dir",
                first_config_value(runtime.get("working_dir"), site_json.get("working_dir"), "."),
            ),
            "runtime_user": resolve_config_value(
                name,
                "runtime.user",
                first_config_value(runtime.get("user"), site_json.get("user"), os.environ.get("USER", "")),
            ),
            "runtime_env_file": resolve_config_value(
                name,
                "runtime.env_file",
                first_config_value(runtime.get("env_file"), site_json.get("env_file")),
            ),
            "runtime_port": str(
                resolve_config_value(name, "runtime.port", first_config_value(runtime.get("port"), site_json.get("port"))) or ""
            ),
            "runtime_health_endpoint": resolve_config_value(
                name,
                "runtime.health_endpoint",
                first_config_value(runtime.get("health_endpoint"), site_json.get("health_endpoint"), "/health"),
            ),
            "health_retries": int(
                resolve_config_value(
                    name,
                    "runtime.health_retries",
                    first_config_value(runtime.get("health_retries"), site_json.get("health_retries"), 20),
                )
            ),
            "health_interval_seconds": int(
                resolve_config_value(
                    name,
                    "runtime.health_interval_seconds",
                    first_config_value(runtime.get("health_interval_seconds"), site_json.get("health_interval_seconds"), 2),
                )
            ),
            "nginx_www_redirect": bool(first_config_value(nginx.get("www_redirect"), site_json.get("www_redirect"), False)),
            "nginx_tls_hostnames_csv": " ".join(
                first_config_value(nginx.get("tls_hostnames"), site_json.get("tls_hostnames"), []) or []
            ),
        }
    )
    if not fields["unlighthouse_server_url"]:
        fields["unlighthouse_server_url"] = os.environ.get("UNLIGHTHOUSE_SERVER_URL", "")
    if not fields["unlighthouse_server_token"]:
        fields["unlighthouse_server_token"] = os.environ.get("UNLIGHTHOUSE_SERVER_TOKEN", "")
    if not fields["releases_dir"]:
        fields["releases_dir"] = f"{fields['workdir']}/releases"
    if not fields["current_symlink"]:
        fields["current_symlink"] = f"{fields['workdir']}/current"
    if not fields["runtime_user"]:
        fields["runtime_user"] = os.environ.get("USER") or subprocess.check_output(["id", "-un"], text=True).strip()
    if fields["repo_auth_github_token"]:
        fields["repo"] = github_repo_url_with_token(
            str(fields["repo"]),
            str(fields["repo_auth_github_username"]),
            str(fields["repo_auth_github_token"]),
        )
    return fields


def deploy_site(ctx: SyncContext, site_json: dict) -> None:
    site = resolve_site_fields(site_json)
    name = site["name"]
    if ctx.only_site and ctx.only_site != name:
        return

    deploy_mode = str(site["deploy_mode"] or "release")
    workdir = Path(str(site["workdir"]))
    releases_dir = Path(str(site["releases_dir"]))
    current_symlink = Path(str(site["current_symlink"]))
    preflight_site(
        ctx,
        name,
        deploy_mode,
        str(site["service_name"]),
        str(site["runtime_mode"]),
        str(site["runtime_command"]),
        workdir,
        releases_dir,
        current_symlink,
        str(site["runtime_port"]),
        str(site["runtime_working_dir"]),
    )
    if ctx.dry_run or ctx.preflight_only:
        ctx.log_event(name, "deploy", "dry-run", "preflight completed")
        return

    deployment_dir = workdir
    if deploy_mode == "checkout":
        if not workdir.is_dir():
            raise SyncError(f"Checkout workdir does not exist for '{name}': {workdir}")
    else:
        workdir.mkdir(parents=True, exist_ok=True)
        releases_dir.mkdir(parents=True, exist_ok=True)
        release_ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        deployment_dir = releases_dir / release_ts
        while deployment_dir.exists():
            deployment_dir = releases_dir / f"{release_ts}-{int(time.time() * 1000) % 10000:04d}"

    state_mark_attempt(ctx, name, deployment_dir)
    ctx.log_event(name, "deploy", "running", f"release={deployment_dir}")

    git_env = shell_env_with_git_ssh(str(site["git_ssh_command"]))
    if deploy_mode == "checkout":
        run_checked(git_command_with_github_auth(str(site["repo"]), "fetch", "--prune", "origin"), cwd=deployment_dir, env=git_env)
        run_checked(["git", "checkout", str(site["branch"])], cwd=deployment_dir, env=git_env)
        run_checked(["git", "reset", "--hard", f"origin/{site['branch']}"], cwd=deployment_dir, env=git_env)
    else:
        run_checked(
            git_command_with_github_auth(str(site["repo"]), "clone", str(site["repo"]), str(deployment_dir)),
            env=git_env,
        )
        run_checked(git_command_with_github_auth(str(site["repo"]), "fetch", "--prune", "origin"), cwd=deployment_dir, env=git_env)
        run_checked(["git", "checkout", str(site["branch"])], cwd=deployment_dir, env=git_env)
        run_checked(["git", "reset", "--hard", f"origin/{site['branch']}"], cwd=deployment_dir, env=git_env)
    run_optional(ctx, str(site["pre_deploy_cmd"]), "pre-deploy", name, deployment_dir)
    run_optional(ctx, str(site["build_cmd"]), "build", name, deployment_dir)

    deploy_script = str(site["deploy_script"])
    if deploy_script and deploy_script != "null":
        deploy_path = deployment_dir / deploy_script
        if not deploy_path.is_file():
            raise SyncError(f"deploy_script not found: {deploy_script}")
        deploy_path.chmod(0o755)
        ctx.log_event(name, "deploy-script", "running", deploy_script)
        run_checked([str(deploy_path)], cwd=deployment_dir)
        ctx.log_event(name, "deploy-script", "success", deploy_script)

    if site["runtime_mode"] == "service":
        runtime_working_dir = str(site["runtime_working_dir"])
        if runtime_working_dir == ".":
            runtime_working_dir = str(deployment_dir)
        elif not runtime_working_dir.startswith("/"):
            runtime_working_dir = str(deployment_dir / runtime_working_dir)
        ensure_runtime_service(
            ctx,
            name,
            str(site["service_name"]),
            str(site["runtime_mode"]),
            str(site["runtime_command"]),
            runtime_working_dir,
            str(site["runtime_user"]),
            str(site["runtime_env_file"]),
        )
        try:
            wait_for_service_health(
                ctx,
                name,
                str(site["runtime_mode"]),
                str(site["runtime_port"]),
                str(site["runtime_health_endpoint"]),
                int(site["health_retries"]),
                int(site["health_interval_seconds"]),
            )
        except Exception:
            if deploy_mode != "checkout":
                shutil.rmtree(deployment_dir, ignore_errors=True)
            raise

    static_root_candidate = str(site["build_output"] or "")
    if not static_root_candidate or static_root_candidate == "null":
        static_root_candidate = str(site["web_root"] or "")

    try:
        apply_nginx_site_config(
            ctx,
            name,
            str(site["domain"]),
            str(site["runtime_mode"]),
            deployment_dir,
            static_root_candidate,
            str(site["runtime_port"]),
            bool(site["nginx_www_redirect"]),
            str(site["nginx_tls_hostnames_csv"]),
        )
    except Exception:
        if deploy_mode != "checkout":
            shutil.rmtree(deployment_dir, ignore_errors=True)
        raise

    previous_target = ""
    previous_successful = ctx.read_state_json(name).get("last_successful_release", "") or ""
    if deploy_mode != "checkout":
        previous_target = capture_current_target(current_symlink)
        atomic_switch_symlink(current_symlink, deployment_dir)
    state_mark_success(ctx, name, deployment_dir)
    body = ctx.read_state_json(name)
    if deploy_mode != "checkout":
        body["current_symlink"] = str(current_symlink)
    ctx.write_state_json(name, body)

    run_optional(ctx, str(site["post_deploy_cmd"]), "post-deploy", name, deployment_dir)
    run_unlighthouse(
        ctx,
        name,
        str(site["site_url"]),
        str(site["unlighthouse_cmd"]),
        str(site["unlighthouse_server_url"]),
        str(site["unlighthouse_server_token"]),
    )
    if deploy_mode != "checkout":
        cleanup_old_releases(releases_dir, int(site["keep_releases"]), str(deployment_dir), previous_successful or previous_target)
    ctx.log_event(name, "deploy", "success", f"release={deployment_dir}")


def emit_status_json(ctx: SyncContext, config: list[dict]) -> None:
    output: list[dict] = []
    for site in config:
        site_name = site.get("name", "")
        if ctx.only_site and ctx.only_site != site_name:
            continue
        output.append(
            {
                "name": site.get("name"),
                "domain": site.get("domain"),
                "site_url": site.get("site_url"),
                "runtime": site.get("runtime", {}),
                "deploy": ctx.read_state_json(site_name),
            }
        )
    print(json.dumps(output))


def rollback_site(ctx: SyncContext, config: list[dict], site_name: str) -> None:
    state_json = ctx.read_state_json(site_name)
    previous_target = state_json.get("previous_successful_release", "") or ""
    if not previous_target:
        raise SyncError(f"No previous successful release recorded for '{site_name}'.")
    if not Path(previous_target).is_dir():
        raise SyncError(f"Rollback target is invalid for '{site_name}': {previous_target}")
    current_symlink = ""
    for site in config:
        if site.get("name") == site_name:
            if str(site.get("deploy_mode") or "release") == "checkout":
                raise SyncError(f"Rollback is not supported for checkout deploy mode ('{site_name}').")
            current_symlink = site.get("current_symlink", "")
            break
    restore_last_good_files(ctx, site_name, runtime_service_name(site, site_name))
    atomic_switch_symlink(Path(current_symlink), Path(previous_target))
    state_mark_rollback(ctx, site_name, Path(previous_target))
    ctx.log_event(site_name, "rollback", "success", previous_target)


def load_config(ctx: SyncContext) -> list[dict]:
    if ctx.discover_base:
        run_checked(
            [
                "python3",
                str(ctx.script_dir / "discover_sites.py"),
                "--base-glob",
                ctx.discover_base,
                "--output",
                str(ctx.config_path),
            ]
        )
    if not ctx.config_path.is_file():
        raise SyncError(f"Config file not found: {ctx.config_path}")
    return load_json(ctx.config_path)


def process_site_with_lock(ctx: SyncContext, site_name: str, site_json: dict) -> None:
    lock_path = ctx.lock_dir / f"{site_name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        deploy_site(ctx, site_json)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=usage_description())
    parser.add_argument("--config", default="deploy/sites.json")
    parser.add_argument("--discover-base")
    parser.add_argument("--site")
    parser.add_argument("--rollback")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--json-status", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.rollback and args.site:
        raise SystemExit("--site and --rollback are mutually exclusive.")
    ctx = SyncContext(args)
    for command in ("git", "curl", "python3"):
        ctx.require_cmd(command)
    ctx.init_runtime_dirs()
    config = load_config(ctx)

    if ctx.json_status:
        emit_status_json(ctx, config)
        return
    if not config:
        print(f"No sites defined in {ctx.config_path}")
        return
    if ctx.rollback_site:
        rollback_site(ctx, config, ctx.rollback_site)
        return

    for site in config:
        site_name = site.get("name", "")
        if not site_name:
            continue
        try:
            process_site_with_lock(ctx, site_name, site)
        except Exception:
            state_mark_failure(ctx, site_name, "deployment failed")
            restore_last_good_files(ctx, site_name, runtime_service_name(site, site_name))
            ctx.log_event(site_name, "deploy", "failed", "deployment failed", "error")
            raise
    print("Done syncing configured GitHub sites.")


if __name__ == "__main__":
    try:
        main()
    except SyncError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
