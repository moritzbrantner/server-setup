"""Command-line interface for bootstrap, planning, application, and validation."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from server_setup.config import ConfigError, DEFAULT_CONFIG_PATH, ServerSetupConfig, parse_config, render_config
from server_setup.core import ServerSetupCore
from server_setup.modules import ModuleApplyError, default_modules
from server_setup.plan import ChangeKind, Plan, ValidationReport, ValidationStatus
from server_setup.system import LocalSystem, System

STATUS_MARKERS = {
    ValidationStatus.PASS: "PASS",
    ValidationStatus.WARN: "WARN",
    ValidationStatus.FAIL: "FAIL",
    ValidationStatus.SKIP: "SKIP",
}


def _load(system: System, path: Path) -> ServerSetupConfig:
    text = system.read_text(path)
    if text is None:
        raise ConfigError(f"Unable to read configuration {path}: file does not exist")
    return parse_config(text)


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
    # Reparse before writing so wizard/programmatic callers cannot persist an invalid model.
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

    doctor = subparsers.add_parser("doctor", help="Run host validation plus capacity diagnostics")
    doctor.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
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
        config = _load(host_system, args.config)
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
        if args.command == "doctor":
            return _doctor(config, host_system)
    except (ConfigError, ModuleApplyError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
