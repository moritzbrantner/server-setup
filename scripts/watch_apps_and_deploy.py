#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def default_apps_dir() -> str:
    try:
        if Path("/root/apps").is_dir():
            return "/root/apps"
    except PermissionError:
        pass
    return "/srv/apps"


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prune_logs(log_dir: Path, retention_days: int) -> None:
    if not log_dir.is_dir():
        return
    cutoff = retention_days * 24 * 60 * 60
    now = time.time()
    for path in log_dir.glob("*.log"):
        try:
            if now - path.stat().st_mtime > cutoff:
                path.unlink()
        except FileNotFoundError:
            continue


def main() -> None:
    apps_dir = Path(os.environ.get("APPS_DIR", default_apps_dir()))
    debounce_seconds = int(os.environ.get("DEBOUNCE_SECONDS", "5"))
    runner_service = os.environ.get("RUNNER_SERVICE", "site-discovery-deploy.service")
    log_dir = Path(os.environ.get("LOG_DIR", "/var/log/server-setup"))
    retention_days = int(os.environ.get("LOG_RETENTION_DAYS", "14"))

    if shutil.which("inotifywait") is None:
        raise SystemExit("inotifywait is required (install inotify-tools).")
    if not apps_dir.is_dir():
        raise SystemExit(f"Apps directory not found: {apps_dir}")

    log_dir.mkdir(parents=True, exist_ok=True)
    prune_logs(log_dir, retention_days)
    log_path = log_dir / "watcher.log"

    print(f"Watching {apps_dir} for change events...")
    proc = subprocess.Popen(
        [
            "inotifywait",
            "-m",
            "-r",
            "-e",
            "close_write",
            "-e",
            "moved_to",
            "-e",
            "create",
            "-e",
            "delete",
            "--format",
            "%w%f %e",
            str(apps_dir),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )

    last_trigger_epoch = 0
    pending = False
    assert proc.stdout is not None
    for line in proc.stdout:
        changed = line.strip()
        if not changed:
            continue
        changed_path, _, changed_event = changed.partition(" ")
        now_epoch = int(time.time())
        if now_epoch - last_trigger_epoch < debounce_seconds:
            pending = True
            continue
        last_trigger_epoch = now_epoch
        with log_path.open("a", encoding="utf-8") as log_handle:
            log_handle.write(f"[{timestamp()}] Change detected ({changed_event}) at {changed_path}\n")
        subprocess.run(["systemctl", "start", runner_service], check=False)
        if pending:
            with log_path.open("a", encoding="utf-8") as log_handle:
                log_handle.write(f"[{timestamp()}] Collapsed burst of app changes into a single deploy trigger\n")
            pending = False


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
