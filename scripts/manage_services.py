#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from registry_contract import DEFAULT_REGISTRY_PATH
from site_cleanup_common import RESET_UNITS, load_managed_sites


SYSTEM_SERVICE_METADATA: dict[str, tuple[str, str]] = {
    "site-webhook-receiver.service": ("automation", "automation"),
    "server-setup-status-webapp.service": ("support", "status-webapp"),
    "server-setup-example-apps.service": ("support", "example-apps"),
}


@dataclass(frozen=True)
class ManagedUnit:
    name: str
    kind: str
    app: str


@dataclass(frozen=True)
class UnitState:
    load_state: str
    active_state: str
    sub_state: str
    unit_file_state: str

    @property
    def exists(self) -> bool:
        return self.load_state != "not-found"

    def as_dict(self) -> dict[str, object]:
        return {
            "exists": self.exists,
            "load_state": self.load_state,
            "active_state": self.active_state,
            "sub_state": self.sub_state,
            "unit_file_state": self.unit_file_state,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List and manage the systemd services created by this repository."
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=("status", "list", "start", "stop", "restart"),
        help="Show service inventory or run a systemctl action against the selected managed units.",
    )
    parser.add_argument("--config", default=str(DEFAULT_REGISTRY_PATH))
    parser.add_argument(
        "--app",
        action="append",
        default=[],
        help="Filter by app owner. Repeat for multiple apps, for example --app sample-service.",
    )
    parser.add_argument(
        "--service",
        action="append",
        default=[],
        help="Filter by exact systemd unit name. Repeat for multiple units.",
    )
    parser.add_argument("--json", action="store_true", help="Render service inventory as JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned systemctl commands without executing.")
    return parser.parse_args()


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This script must run as root for start/stop/restart actions. Re-run with sudo or use --dry-run.")


def base_units() -> list[ManagedUnit]:
    units: list[ManagedUnit] = []
    for unit in RESET_UNITS:
        kind, app = SYSTEM_SERVICE_METADATA.get(unit, ("support", "system"))
        units.append(ManagedUnit(name=unit, kind=kind, app=app))
    return units


def configured_units(config_path: Path) -> list[ManagedUnit]:
    units = base_units()
    units.extend(
        ManagedUnit(name=site.runtime_service, kind="app", app=site.name)
        for site in load_managed_sites(config_path)
        if site.runtime_service
    )

    deduped: list[ManagedUnit] = []
    seen: set[str] = set()
    for unit in units:
        if unit.name in seen:
            continue
        deduped.append(unit)
        seen.add(unit.name)
    return deduped


def select_units(units: list[ManagedUnit], args: argparse.Namespace) -> list[ManagedUnit]:
    app_filters = {value.strip() for value in args.app if value.strip()}
    service_filters = {value.strip() for value in args.service if value.strip()}
    selected = [
        unit
        for unit in units
        if (not app_filters or unit.app in app_filters)
        and (not service_filters or unit.name in service_filters)
    ]
    return selected


def inspect_unit(unit_name: str) -> UnitState:
    if shutil.which("systemctl") is None:
        return UnitState(
            load_state="unknown",
            active_state="unknown",
            sub_state="unknown",
            unit_file_state="unknown",
        )

    result = subprocess.run(
        [
            "systemctl",
            "show",
            unit_name,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=SubState",
            "--property=UnitFileState",
            "--no-pager",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    state = {
        "LoadState": "unknown",
        "ActiveState": "unknown",
        "SubState": "unknown",
        "UnitFileState": "unknown",
    }
    for line in (result.stdout or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in state:
            state[key] = value.strip() or "unknown"

    if result.returncode != 0 and state["LoadState"] == "unknown":
        stderr = (result.stderr or "").lower()
        if "could not be found" in stderr or "not loaded" in stderr:
            state["LoadState"] = "not-found"
            state["ActiveState"] = "inactive"
            state["SubState"] = "dead"
            state["UnitFileState"] = "disabled"

    return UnitState(
        load_state=state["LoadState"],
        active_state=state["ActiveState"],
        sub_state=state["SubState"],
        unit_file_state=state["UnitFileState"],
    )


def inventory_rows(units: list[ManagedUnit]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for unit in units:
        state = inspect_unit(unit.name)
        rows.append(
            {
                "service": unit.name,
                "kind": unit.kind,
                "app": unit.app,
                **state.as_dict(),
            }
        )
    return rows


def render_table(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "No managed services matched the requested filters."

    table_rows = [
        {
            "SERVICE": str(row["service"]),
            "KIND": str(row["kind"]),
            "APP": str(row["app"]),
            "EXISTS": "yes" if row["exists"] else "no",
            "ACTIVE": str(row["active_state"]),
            "ENABLED": str(row["unit_file_state"]),
        }
        for row in rows
    ]
    columns = ("SERVICE", "KIND", "APP", "EXISTS", "ACTIVE", "ENABLED")
    widths = {
        column: max(len(column), *(len(item[column]) for item in table_rows))
        for column in columns
    }

    rendered = [
        " ".join(column.ljust(widths[column]) for column in columns),
        " ".join("-" * widths[column] for column in columns),
    ]
    rendered.extend(
        " ".join(item[column].ljust(widths[column]) for column in columns)
        for item in table_rows
    )
    return "\n".join(rendered)


def log_action(cmd: list[str]) -> None:
    print(f"+ {shlex.join(cmd)}")


def run_action(action: str, units: list[ManagedUnit], *, dry_run: bool) -> None:
    if not units:
        print("No managed services matched the requested filters.")
        return
    if shutil.which("systemctl") is None:
        raise SystemExit("systemctl is required to manage services on this host.")

    for unit in units:
        cmd = ["systemctl", action, unit.name]
        log_action(cmd)
        if dry_run:
            continue
        result = subprocess.run(cmd, text=True, capture_output=True, check=False)
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        if result.returncode != 0:
            raise SystemExit(result.returncode)


def main() -> None:
    args = parse_args()
    units = select_units(configured_units(Path(args.config)), args)

    if args.action in {"status", "list"}:
        rows = inventory_rows(units)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(render_table(rows))
        return

    if args.json:
        raise SystemExit("--json is only supported for status/list output.")
    if not units:
        print("No managed services matched the requested filters.")
        return
    if not args.dry_run:
        require_root()
    run_action(args.action, units, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
