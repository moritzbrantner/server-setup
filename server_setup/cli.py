"""Command-line interface for bootstrap, planning, application, validation, and maintenance."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from server_setup.config import ConfigError, DEFAULT_CONFIG_PATH, ServerSetupConfig, parse_config, render_config
from server_setup.core import ServerSetupCore
from server_setup.maintenance import (
    DEFAULT_SNAPSHOT_PATH,
    SnapshotError,
    capture_snapshot,
    current_dokploy_version,
    package_version_drift,
    parse_snapshot,
    render_snapshot,
    rollback_package_versions,
    upgrade_managed_packages,
)
from server_setup.modules import ModuleApplyError, default_modules
from server_setup.plan import ChangeKind, Plan, ValidationReport, ValidationStatus
from server_setup.system import CommandError, LocalSystem, System
from server_setup.ui import UiError, serve_ui

STATUS_MARKERS = {
    ValidationStatus.PASS: "PASS",
    ValidationStatus.WARN: "WARN",
    ValidationStatus.FAIL: "FAIL",
    ValidationStatus.SKIP: "SKIP",
}


def _load_text(system: System, path: Path) -> str:
    text = system.read_text(path)
    if text is None:
        raise ConfigError(f"Unable to read configuration {path}: file does not exist")
    parse_config(text)
    return text


def _load(system: System, path: Path) -> ServerSetupConfig:
    return parse_config(_load_text(system, path))


def _core(config: ServerSetupConfig, system: System) -> ServerSetupCore:
    return ServerSetupCore(config, default_modules(system))


def _print_plan(plan: Plan) -> None:
    if not plan.has_changes:
        print("No changes.")
        return
    for change in plan.changes:
        print(f"[{change.kind.value.upper():9}] {change.module}: {change.summary}")
        if change.details:
            print(f"            {change.details}")


def _print_validation(report: ValidationReport) -> None:
    for result in report.results:
        print(f"[{STATUS_MARKERS[result.status]:4}] {result.module}: {result.summary}")
        if result.details:
            print(f"       {result.details}")
    print("Result: healthy" if report.ok else "Result: unhealthy")


def _prompt_bool(prompt: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{prompt} [{hint}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def _prompt_text(prompt: str, default: str) -> str:
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def _wizard(config: ServerSetupConfig) -> ServerSetupConfig:
    print("Configure the host. Application deployments remain managed in Dokploy.\n")
    timezone = _prompt_text("Host timezone", config.host.timezone)
    unattended = _prompt_bool("Enable unattended security upgrades?", config.host.unattended_upgrades)
    firewall = _prompt_bool("Manage UFW (allow SSH/HTTP/HTTPS, deny other incoming traffic)?", config.security.firewall)
    fail2ban = _prompt_bool("Manage fail2ban for SSH?", config.security.fail2ban)
    ssh_hardening = _prompt_bool(
        "Harden SSH (disable password auth and root login; requires key access)?",
        config.security.ssh_hardening,
    )
    dokploy_enabled = _prompt_bool("Install/manage Dokploy?", config.dokploy.enabled)
    dokploy_version = config.dokploy.version
    if dokploy_enabled:
        dokploy_version = _prompt_text("Pinned Dokploy release", config.dokploy.version)

    return replace(
        config,
        host=replace(config.host, timezone=timezone, unattended_upgrades=unattended),
        security=replace(
            config.security,
            firewall=firewall,
            fail2ban=fail2ban,
            ssh_hardening=ssh_hardening,
        ),
        dokploy=replace(config.dokploy, enabled=dokploy_enabled, version=dokploy_version),
    )


def _write_config(system: System, path: Path, config: ServerSetupConfig) -> None:
    rendered = render_config(config)
    parse_config(rendered)
    system.write_text(path, rendered, mode=0o600)
    print(f"Configuration written to {path}")


def _require_root(system: System) -> None:
    if system.geteuid() != 0:
        raise ModuleApplyError("Applying host changes requires root; run with sudo.")


def _confirm_apply(plan: Plan, *, yes: bool, allow_dangerous: bool) -> None:
    if plan.has_dangerous_changes and not allow_dangerous:
        dangerous = [change.summary for change in plan.changes if change.kind is ChangeKind.DANGEROUS]
        if sys.stdin.isatty() and not yes:
            print("\nDangerous changes require explicit confirmation:")
            for summary in dangerous:
                print(f"  - {summary}")
            if _prompt_bool("Allow these dangerous changes?", False):
                return
        raise ModuleApplyError("Plan contains dangerous changes; re-run with --allow-dangerous after reviewing the plan.")
    if yes or not plan.has_changes:
        return
    if not sys.stdin.isatty():
        raise ModuleApplyError("Refusing non-interactive apply without --yes")
    if not _prompt_bool("Apply these changes?", False):
        raise ModuleApplyError("Apply cancelled")


def _confirm_mutation(prompt: str, *, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise ModuleApplyError("Refusing non-interactive maintenance without --yes")
    if not _prompt_bool(prompt, False):
        raise ModuleApplyError("Maintenance cancelled")


def _apply(config: ServerSetupConfig, system: System, *, yes: bool, allow_dangerous: bool) -> int:
    _require_root(system)
    core = _core(config, system)
    plan = core.plan()
    _print_plan(plan)
    if not plan.has_changes:
        report = core.validate()
        _print_validation(report)
        return 0 if report.ok else 1
    _confirm_apply(plan, yes=yes, allow_dangerous=allow_dangerous)
    core.apply(plan)
    report = core.validate()
    _print_validation(report)
    return 0 if report.ok else 1


def _drift_payload(plan: Plan, report: ValidationReport) -> dict[str, object]:
    drift = plan.has_changes or not report.ok
    return {
        "changes": [
            {
                "action": change.action,
                "details": change.details,
                "kind": change.kind.value,
                "module": change.module,
                "summary": change.summary,
                "target": change.target,
            }
            for change in plan.changes
        ],
        "drift": drift,
        "validation": [
            {
                "details": result.details,
                "module": result.module,
                "status": result.status.value,
                "summary": result.summary,
            }
            for result in report.results
        ],
    }


def _drift(config: ServerSetupConfig, system: System, *, as_json: bool) -> int:
    core = _core(config, system)
    plan = core.plan()
    report = core.validate()
    payload = _drift_payload(plan, report)
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["drift"]:
        print("Drift detected.")
        _print_plan(plan)
        _print_validation(report)
    else:
        print("No drift.")
    return 1 if payload["drift"] else 0


def _snapshot(config_text: str, system: System, output: Path) -> int:
    snapshot = capture_snapshot(system, config_text)
    system.write_text(output, render_snapshot(snapshot), mode=0o600)
    print(f"Maintenance snapshot written to {output}")
    return 0


def _upgrade(
    config: ServerSetupConfig,
    config_text: str,
    system: System,
    *,
    snapshot_path: Path,
    yes: bool,
) -> int:
    _require_root(system)
    core = _core(config, system)
    plan = core.plan()
    report = core.validate()
    if plan.has_changes or not report.ok:
        print("Refusing upgrade because the host has unreconciled drift.")
        _print_plan(plan)
        _print_validation(report)
        raise ModuleApplyError("Reconcile drift with plan/apply before upgrading managed host packages.")

    _confirm_mutation("Upgrade server-setup-managed host packages?", yes=yes)
    snapshot = capture_snapshot(system, config_text)
    system.write_text(snapshot_path, render_snapshot(snapshot), mode=0o600)
    packages = upgrade_managed_packages(system, snapshot.packages)

    post_plan = core.plan()
    post_report = core.validate()
    if post_plan.has_changes or not post_report.ok:
        print("Upgrade completed, but post-upgrade validation detected drift.")
        _print_plan(post_plan)
        _print_validation(post_report)
        print(f"Rollback snapshot: {snapshot_path}")
        return 1

    print(f"Upgrade complete for {len(packages)} managed package(s).")
    print(f"Rollback snapshot: {snapshot_path}")
    return 0


def _rollback(
    config: ServerSetupConfig,
    config_text: str,
    system: System,
    *,
    snapshot_path: Path,
    yes: bool,
) -> int:
    _require_root(system)
    snapshot_text = system.read_text(snapshot_path)
    if snapshot_text is None:
        raise SnapshotError(f"Rollback snapshot {snapshot_path} does not exist")
    snapshot = parse_snapshot(snapshot_text)
    if snapshot.config_text != config_text:
        raise SnapshotError("Current configuration differs from the rollback snapshot; refusing to combine config changes with package rollback.")

    current_dokploy = current_dokploy_version(system)
    if current_dokploy != snapshot.dokploy_version:
        raise SnapshotError(
            "Dokploy state changed since the snapshot; this rollback command only restores managed host package versions."
        )

    _confirm_mutation("Restore managed host packages to the snapshot versions?", yes=yes)
    restored = rollback_package_versions(system, snapshot)
    version_drift = package_version_drift(system, snapshot)

    core = _core(config, system)
    post_plan = core.plan()
    post_report = core.validate()
    if version_drift or post_plan.has_changes or not post_report.ok:
        if version_drift:
            for package, (current, expected) in sorted(version_drift.items()):
                print(f"[FAIL] rollback: {package} is {current or '<missing>'}, expected {expected}")
        _print_plan(post_plan)
        _print_validation(post_report)
        return 1

    print(f"Rollback complete for {len(restored)} managed package(s).")
    print(f"Snapshot retained at {snapshot_path}")
    return 0


def _doctor(config: ServerSetupConfig, system: System) -> int:
    print("Host diagnostics")
    print("----------------")
    report = _core(config, system).validate()
    _print_validation(report)

    warnings = 0
    if config.dokploy.enabled:
        meminfo = system.read_text("/proc/meminfo") or ""
        memory_kib = 0
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                try:
                    memory_kib = int(line.split()[1])
                except (IndexError, ValueError):
                    pass
                break
        if memory_kib and memory_kib < 2 * 1024 * 1024:
            warnings += 1
            print("[WARN] doctor: less than 2 GiB RAM; Dokploy recommends at least 2 GiB.")

        disk = system.run(["df", "-Pk", "/"])
        if disk.returncode == 0:
            lines = [line for line in disk.stdout.splitlines() if line.strip()]
            if len(lines) >= 2:
                fields = lines[-1].split()
                try:
                    available_kib = int(fields[3])
                except (IndexError, ValueError):
                    available_kib = 0
                if available_kib and available_kib < 30 * 1024 * 1024:
                    warnings += 1
                    print("[WARN] doctor: less than 30 GiB free disk; Dokploy recommends at least 30 GiB.")
    if warnings == 0:
        print("[PASS] doctor: no additional capacity warnings")
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="server-setup", description="Opinionated, idempotent Dokploy host bootstrap.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Create/review configuration and optionally apply it")
    setup.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    setup.add_argument("--non-interactive", action="store_true", help="Use the existing config or secure defaults without prompting")
    setup.add_argument("--no-apply", action="store_true", help="Write configuration but do not change the host")
    setup.add_argument("--yes", action="store_true", help="Skip the ordinary apply confirmation")
    setup.add_argument("--allow-dangerous", action="store_true", help="Allow changes classified as dangerous")

    plan = subparsers.add_parser("plan", help="Show desired host changes without mutation")
    plan.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    apply = subparsers.add_parser("apply", help="Apply the current desired host state")
    apply.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    apply.add_argument("--yes", action="store_true")
    apply.add_argument("--allow-dangerous", action="store_true")

    validate = subparsers.add_parser("validate", help="Verify the configured host state")
    validate.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    drift = subparsers.add_parser("drift", help="Detect read-only desired-state drift")
    drift.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    drift.add_argument("--json", action="store_true", help="Emit deterministic machine-readable evidence")

    snapshot = subparsers.add_parser("snapshot", help="Capture config and exact managed package versions")
    snapshot.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    snapshot.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT_PATH)

    upgrade = subparsers.add_parser("upgrade", help="Safely upgrade only server-setup-managed apt packages")
    upgrade.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    upgrade.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    upgrade.add_argument("--yes", action="store_true")

    rollback = subparsers.add_parser("rollback", help="Restore exact managed package versions from the last snapshot")
    rollback.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    rollback.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    rollback.add_argument("--yes", action="store_true")

    doctor = subparsers.add_parser("doctor", help="Run host validation plus capacity diagnostics")
    doctor.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    ui = subparsers.add_parser("ui", help="Serve the read-only local host status UI")
    ui.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    ui.add_argument("--host", default="127.0.0.1", help="Loopback bind address only")
    ui.add_argument("--port", type=int, default=8765)
    return parser


def run(argv: list[str] | None = None, *, system: System | None = None) -> int:
    args = build_parser().parse_args(argv)
    host_system = system or LocalSystem()
    try:
        if args.command == "setup":
            config = _load(host_system, args.config) if host_system.exists(args.config) else ServerSetupConfig()
            if not args.non_interactive:
                config = _wizard(config)
            _write_config(host_system, args.config, config)
            if args.no_apply:
                return 0
            return _apply(config, host_system, yes=args.yes, allow_dangerous=args.allow_dangerous)

        config_text = _load_text(host_system, args.config)
        config = parse_config(config_text)
        core = _core(config, host_system)
        if args.command == "plan":
            _print_plan(core.plan())
            return 0
        if args.command == "apply":
            return _apply(config, host_system, yes=args.yes, allow_dangerous=args.allow_dangerous)
        if args.command == "validate":
            report = core.validate()
            _print_validation(report)
            return 0 if report.ok else 1
        if args.command == "drift":
            return _drift(config, host_system, as_json=args.json)
        if args.command == "snapshot":
            return _snapshot(config_text, host_system, args.output)
        if args.command == "upgrade":
            return _upgrade(config, config_text, host_system, snapshot_path=args.snapshot, yes=args.yes)
        if args.command == "rollback":
            return _rollback(config, config_text, host_system, snapshot_path=args.snapshot, yes=args.yes)
        if args.command == "doctor":
            return _doctor(config, host_system)
        if args.command == "ui":
            serve_ui(config, host_system, host=args.host, port=args.port)
            return 0
    except (ConfigError, SnapshotError, UiError, ModuleApplyError, CommandError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
