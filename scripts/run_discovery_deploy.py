#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def default_apps_glob() -> str:
    try:
        if Path("/root/apps").is_dir():
            return "/root/apps/*"
    except PermissionError:
        pass
    return "/srv/apps/*"


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prune_logs(log_dir: Path, retention_days: int) -> None:
    if not log_dir.is_dir():
        return
    cutoff = retention_days * 24 * 60 * 60
    now = datetime.now(timezone.utc).timestamp()
    for path in log_dir.glob("*.log"):
        try:
            if now - path.stat().st_mtime > cutoff:
                path.unlink()
        except FileNotFoundError:
            continue


def main() -> None:
    root_dir = Path(__file__).resolve().parent.parent
    apps_glob = os.environ.get("APPS_GLOB", default_apps_glob())
    config_path = Path(os.environ.get("CONFIG_PATH", str(root_dir / "deploy/sites.json")))
    lock_file = Path(os.environ.get("LOCK_FILE", "/var/lock/site-discovery-deploy.lock"))
    log_dir = Path(os.environ.get("LOG_DIR", "/var/log/server-setup"))
    retention_days = int(os.environ.get("LOG_RETENTION_DAYS", "14"))

    lock_file.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    prune_logs(log_dir, retention_days)

    with lock_file.open("w") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        log_path = log_dir / "discovery-deploy.log"
        with log_path.open("a", encoding="utf-8") as log_handle:
            log_handle.write(f"[{timestamp()}] Starting discovery + deploy run\n")
            log_handle.flush()
            subprocess.run(
                ["python3", str(root_dir / "scripts/discover_sites.py"), "--base-glob", apps_glob, "--output", str(config_path)],
                check=True,
                cwd=root_dir,
            )
            subprocess.run(
                ["bash", str(root_dir / "scripts/sync-github-sites.sh"), "--config", str(config_path)],
                check=True,
                cwd=root_dir,
            )
            log_handle.write(f"[{timestamp()}] Discovery + deploy run complete\n")


if __name__ == "__main__":
    main()
