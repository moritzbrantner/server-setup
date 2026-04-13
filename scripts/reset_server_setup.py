#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from site_cleanup_common import RESET_UNITS, ManagedSite, load_managed_sites


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Disable repo-managed services and remove generated config/state for a clean reset."
    )
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "deploy/sites.json"))
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without executing them.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply destructive changes without an extra confirmation gate.",
    )
    parser.add_argument(
        "--stop-nginx",
        action="store_true",
        help="Stop nginx after removing managed site configs instead of reloading it.",
    )
    return parser.parse_args()


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This script must run as root. Re-run with sudo or use --dry-run.")


def systemd_unit_dir() -> Path:
    return Path(os.environ.get("SYSTEMD_UNIT_DIR", "/etc/systemd/system"))


def nginx_site_available_dir() -> Path:
    return Path(os.environ.get("NGINX_SITE_AVAILABLE_DIR", "/etc/nginx/sites-available"))


def nginx_site_enabled_dir() -> Path:
    return Path(os.environ.get("NGINX_SITE_ENABLED_DIR", "/etc/nginx/sites-enabled"))


def state_dir() -> Path:
    return Path(os.environ.get("STATE_DIR", "/var/lib/server-setup/state"))


def log_dir() -> Path:
    return Path(os.environ.get("LOG_DIR", "/var/log/server-setup"))


def lock_file() -> Path:
    return Path(os.environ.get("LOCK_FILE", "/var/lock/site-discovery-deploy.lock"))


def lock_dir() -> Path:
    return Path(os.environ.get("LOCK_DIR", "/var/lock/server-setup"))


def automation_env_file() -> Path:
    return Path(os.environ.get("SITE_AUTOMATION_ENV_FILE", "/etc/default/site-automation"))


def status_webapp_env_file() -> Path:
    return Path(os.environ.get("STATUS_WEBAPP_ENV_FILE", "/etc/default/server-setup-status-webapp"))


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


def remove_path(path: Path, *, dry_run: bool) -> None:
    print(f"- remove {path}")
    if dry_run or not path.exists():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    shutil.rmtree(path)


def remove_site_state(site: ManagedSite, *, dry_run: bool) -> None:
    site_state_dir = state_dir()
    remove_path(site_state_dir / f"{site.name}.json", dry_run=dry_run)
    for extra in sorted(site_state_dir.glob(f"{site.name}-*")):
        remove_path(extra, dry_run=dry_run)


def remove_site_nginx(site: ManagedSite, *, dry_run: bool) -> bool:
    for path in (
        nginx_site_enabled_dir() / f"{site.name}.conf",
        nginx_site_available_dir() / f"{site.name}.conf",
        nginx_site_available_dir() / f"{site.name}.conf.last-good",
    ):
        remove_path(path, dry_run=dry_run)
    return True


def remove_installed_units(sites: list[ManagedSite], *, dry_run: bool) -> None:
    unit_dir = systemd_unit_dir()
    for unit in RESET_UNITS:
        remove_path(unit_dir / unit, dry_run=dry_run)
    for site in sites:
        if site.runtime_service:
            remove_path(unit_dir / site.runtime_service, dry_run=dry_run)


def run_nginx_follow_up(*, dry_run: bool, nginx_changed: bool, stop_nginx: bool) -> None:
    if not nginx_changed:
        return
    if stop_nginx:
        run_systemctl(["stop", "nginx.service"], dry_run=dry_run, missing_ok=True)
        return
    cmd = ["nginx", "-t"]
    log_action(cmd)
    if dry_run:
        run_systemctl(["reload", "nginx.service"], dry_run=True, missing_ok=True)
        return
    if shutil.which("nginx") is None:
        return
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    print_process_output(result)
    if result.returncode == 0:
        run_systemctl(["reload", "nginx.service"], dry_run=False, missing_ok=True)


def main() -> None:
    args = parse_args()
    sites = load_managed_sites(Path(args.config))
    runtime_units = list(dict.fromkeys(site.runtime_service for site in sites if site.runtime_service))

    if not args.dry_run:
        if not args.yes:
            raise SystemExit("Refusing destructive reset without --yes. Re-run with --dry-run to inspect the plan.")
        require_root()
        if shutil.which("systemctl") is None:
            raise SystemExit("systemctl is required to reset managed services.")

    for unit in [*RESET_UNITS, *runtime_units]:
        run_systemctl(["stop", unit], dry_run=args.dry_run, missing_ok=True)
    for unit in [*RESET_UNITS, *runtime_units]:
        run_systemctl(["disable", unit], dry_run=args.dry_run, missing_ok=True)
    for unit in [*RESET_UNITS, *runtime_units]:
        run_systemctl(["reset-failed", unit], dry_run=args.dry_run, missing_ok=True)

    nginx_changed = False
    for site in sites:
        nginx_changed = remove_site_nginx(site, dry_run=args.dry_run) or nginx_changed
        remove_site_state(site, dry_run=args.dry_run)

    remove_installed_units(sites, dry_run=args.dry_run)
    remove_path(automation_env_file(), dry_run=args.dry_run)
    remove_path(status_webapp_env_file(), dry_run=args.dry_run)
    remove_path(lock_file(), dry_run=args.dry_run)
    remove_path(lock_dir(), dry_run=args.dry_run)
    remove_path(log_dir(), dry_run=args.dry_run)

    run_systemctl(["daemon-reload"], dry_run=args.dry_run, missing_ok=False)
    run_nginx_follow_up(dry_run=args.dry_run, nginx_changed=nginx_changed, stop_nginx=args.stop_nginx)


if __name__ == "__main__":
    main()
