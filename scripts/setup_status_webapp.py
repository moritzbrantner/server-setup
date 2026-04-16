#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from simple_setup_common import load_env_file, update_env_file

DEFAULT_BUN_INSTALL = "/root/.bun"


def log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {message}")


def have_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This script must run as root.")


def install_pkgs(packages: list[str]) -> None:
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    result = subprocess.run(["apt-get", "install", "-y", *packages], text=True, capture_output=True, env=env, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def bun_env() -> dict[str, str]:
    env = os.environ.copy()
    bun_install = env.get("BUN_INSTALL", DEFAULT_BUN_INSTALL)
    bun_bin = f"{bun_install}/bin"
    env["BUN_INSTALL"] = bun_install
    path_entries = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    if bun_bin not in path_entries:
        env["PATH"] = f"{bun_bin}{os.pathsep}{env['PATH']}" if env.get("PATH") else bun_bin
    return env


def bun_binary(env: dict[str, str]) -> Path:
    return Path(env["BUN_INSTALL"]) / "bin" / "bun"


def sync_bun_env(env: dict[str, str]) -> None:
    os.environ["BUN_INSTALL"] = env["BUN_INSTALL"]
    os.environ["PATH"] = env["PATH"]


def ensure_bun_symlink(env: dict[str, str]) -> None:
    link_path = Path("/usr/local/bin/bun")
    target = bun_binary(env)
    if not target.is_file() or not link_path.parent.is_dir():
        return
    try:
        if link_path.is_symlink():
            if link_path.resolve() != target.resolve():
                link_path.unlink()
                link_path.symlink_to(target)
            return
        if not link_path.exists():
            link_path.symlink_to(target)
    except OSError:
        pass


def ensure_bun() -> None:
    env = bun_env()
    sync_bun_env(env)
    bun_path = bun_binary(env)

    if bun_path.is_file():
        log(f"Bun already present: {subprocess.check_output([str(bun_path), '--version'], env=env, text=True).strip()}")
    elif have_cmd("bun"):
        log(f"Bun already present: {subprocess.check_output(['bun', '--version'], env=env, text=True).strip()}")
    else:
        log("Installing Bun")
        installer = subprocess.run(["curl", "-fsSL", "https://bun.sh/install"], capture_output=True, check=True)
        subprocess.run(["bash"], input=installer.stdout, env=env, check=True)
    sync_bun_env(env)
    ensure_bun_symlink(env)
    if not bun_path.is_file() and shutil.which("bun", path=env["PATH"]) is None:
        raise SystemExit("Bun is still unavailable after installation.")


def render_status_webapp_env(root_dir: str, host: str, port: str, admin_token: str = "") -> str:
    return (
        f"SERVER_SETUP_ROOT={root_dir}\n"
        f"BUN_INSTALL={DEFAULT_BUN_INSTALL}\n"
        f"STATUS_WEBAPP_HOST={host}\n"
        f"STATUS_WEBAPP_PORT={port}\n"
        f"STATUS_WEBAPP_ADMIN_TOKEN={admin_token}\n"
        "STATUS_WEBAPP_GITHUB_TOKEN=\n"
    )


def render_status_webapp_service(root_dir: str, env_file: str) -> str:
    return (
        "[Unit]\n"
        "Description=Server Setup status webapp\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"EnvironmentFile=-{env_file}\n"
        f"Environment=SERVER_SETUP_ROOT={root_dir}\n"
        f"Environment=BUN_INSTALL={DEFAULT_BUN_INSTALL}\n"
        "Environment=STATUS_WEBAPP_HOST=0.0.0.0\n"
        "Environment=STATUS_WEBAPP_PORT=4000\n"
        f"WorkingDirectory={root_dir}/monitor/webapp\n"
        f"ExecStart=/usr/bin/env python3 {root_dir}/scripts/start_status_webapp.py\n"
        "Restart=always\n"
        "RestartSec=2\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def build_status_webapp(webapp_dir: Path) -> None:
    if not webapp_dir.is_dir():
        raise SystemExit(f"Monitoring webapp directory not found at {webapp_dir}")
    env = bun_env()
    sync_bun_env(env)
    log("Installing monitoring webapp dependencies with Bun")
    subprocess.run(["bun", "install"], cwd=webapp_dir, env=env, check=True)
    log("Building monitoring webapp with Bun")
    subprocess.run(["bun", "run", "build"], cwd=webapp_dir, env=env, check=True)


def enable_service(name: str) -> None:
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", name], check=True)
    if subprocess.run(["systemctl", "is-active", "--quiet", name], check=False).returncode != 0:
        subprocess.run(["systemctl", "status", name, "--no-pager"], check=False)
        raise SystemExit(f"Service '{name}' failed to start.")


def wait_for_status_webapp(port: str) -> None:
    if not have_cmd("curl"):
        return
    for _ in range(20):
        if subprocess.run(["curl", "-fsS", f"http://127.0.0.1:{port}/"], check=False).returncode == 0:
            return
        time.sleep(1)
    raise SystemExit(f"Monitoring webapp did not answer on port {port} within the expected time.")


def write_status_webapp_env(env_file: Path, root_dir: str, host: str, port: str) -> None:
    existing = load_env_file(env_file)
    update_env_file(
        env_file,
        {
            "SERVER_SETUP_ROOT": root_dir,
            "BUN_INSTALL": DEFAULT_BUN_INSTALL,
            "STATUS_WEBAPP_HOST": host,
            "STATUS_WEBAPP_PORT": port,
            "STATUS_WEBAPP_ADMIN_TOKEN": existing.get("STATUS_WEBAPP_ADMIN_TOKEN", ""),
            "STATUS_WEBAPP_GITHUB_TOKEN": existing.get("STATUS_WEBAPP_GITHUB_TOKEN", ""),
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and install the status webapp service.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--render-env", action="store_true")
    parser.add_argument("--render-service", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root_dir = str(Path(args.root).resolve())
    env_file = Path("/etc/default/server-setup-status-webapp")
    service_name = "server-setup-status-webapp.service"
    service_path = Path("/etc/systemd/system") / service_name
    webapp_dir = Path(root_dir) / "monitor/webapp"
    status_host = "0.0.0.0"
    status_port = "4000"

    if args.render_env:
        print(render_status_webapp_env(root_dir, status_host, status_port), end="")
        return
    if args.render_service:
        print(render_status_webapp_service(root_dir, str(env_file)), end="")
        return

    require_root()
    if not have_cmd("apt-get"):
        raise SystemExit("This script supports Ubuntu/Debian hosts with apt.")
    install_pkgs(["ca-certificates", "curl", "unzip"])
    ensure_bun()
    build_status_webapp(webapp_dir)
    write_status_webapp_env(env_file, root_dir, status_host, status_port)
    service_path.write_text(render_status_webapp_service(root_dir, str(env_file)), encoding="utf-8")
    enable_service(service_name)
    wait_for_status_webapp(status_port)


if __name__ == "__main__":
    main()
