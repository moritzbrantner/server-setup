#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from site_cleanup_common import AUTOMATION_UNITS, load_managed_sites


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stop repo-managed websites and deployment triggers without deleting configuration."
    )
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "deploy/sites.json"))
    parser.add_argument("--skip-nginx", action="store_true", help="Leave nginx running.")
    parser.add_argument(
        "--skip-automation",
        action="store_true",
        help="Leave watcher/webhook/timer services running.",
    )
    parser.add_argument(
        "--skip-status-webapp",
        action="store_true",
        help="Leave the status dashboard service running.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without executing them.")
    return parser.parse_args()


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This script must run as root. Re-run with sudo or use --dry-run.")


def log_action(cmd: list[str]) -> None:
    print(f"+ {shlex.join(cmd)}")


def print_process_output(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


def run_systemctl(args: list[str], *, dry_run: bool, missing_ok: bool = False) -> None:
    cmd = ["systemctl", *args]
    log_action(cmd)
    if dry_run:
        return
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    print_process_output(result)
    if result.returncode == 0:
        return
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if missing_ok and ("not loaded" in combined or "could not be found" in combined or "does not exist" in combined):
        return
    raise SystemExit(result.returncode)


def managed_units(args: argparse.Namespace) -> list[str]:
    sites = load_managed_sites(Path(args.config))
    units: list[str] = []
    if not args.skip_automation:
        units.extend(AUTOMATION_UNITS)
    if not args.skip_status_webapp:
        units.append("server-setup-status-webapp.service")
    units.extend(site.runtime_service for site in sites if site.runtime_service)
    if not args.skip_nginx:
        units.append("nginx.service")

    deduped: list[str] = []
    seen: set[str] = set()
    for unit in units:
        if unit not in seen:
            deduped.append(unit)
            seen.add(unit)
    return deduped


def main() -> None:
    args = parse_args()
    units = managed_units(args)

    if not units:
        print("No managed services found to stop.")
        return

    if not args.dry_run:
        require_root()
        if shutil.which("systemctl") is None:
            raise SystemExit("systemctl is required to stop managed services.")

    for unit in units:
        run_systemctl(["stop", unit], dry_run=args.dry_run, missing_ok=True)


if __name__ == "__main__":
    main()
