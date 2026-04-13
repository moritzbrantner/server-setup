#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
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


def load_site_entries(config_path: Path) -> list[dict]:
    if not config_path.is_file():
        return []
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"Existing config must be a JSON array: {config_path}")
    return [entry for entry in data if isinstance(entry, dict)]


def merge_site_entries(existing: list[dict], discovered: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for entry in existing:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        if entry.get("managed_via") == "onboard" or not entry.get("source_server_conf"):
            merged[name] = entry
    for entry in discovered:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        merged[name] = entry
    return [merged[name] for name in sorted(merged)]


def run_discovery(root_dir: Path, apps_glob: str, output_path: Path) -> list[dict]:
    result = subprocess.run(
        ["python3", str(root_dir / "scripts/discover_sites.py"), "--base-glob", apps_glob, "--output", str(output_path)],
        text=True,
        capture_output=True,
        check=False,
        cwd=root_dir,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode == 0:
        return load_site_entries(output_path)

    stderr = result.stderr or ""
    if "No valid server.conf files found under base glob" in stderr or "No directories matched base glob" in stderr:
        output_path.unlink(missing_ok=True)
        return []
    raise subprocess.CalledProcessError(result.returncode, result.args)


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
            existing_entries = load_site_entries(config_path)
            discovered_tmp = config_path.with_suffix(".discovered.tmp")
            discovered_entries = run_discovery(root_dir, apps_glob, discovered_tmp)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(merge_site_entries(existing_entries, discovered_entries), indent=2) + "\n",
                encoding="utf-8",
            )
            discovered_tmp.unlink(missing_ok=True)
            subprocess.run(
                ["bash", str(root_dir / "scripts/sync-github-sites.sh"), "--config", str(config_path)],
                check=True,
                cwd=root_dir,
            )
            log_handle.write(f"[{timestamp()}] Discovery + deploy run complete\n")


if __name__ == "__main__":
    main()
