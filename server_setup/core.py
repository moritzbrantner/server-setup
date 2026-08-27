"""Shared orchestration core used by future CLI and UI frontends."""

from __future__ import annotations

from collections.abc import Iterable

from server_setup.config import ServerSetupConfig
from server_setup.modules import ServerModule
from server_setup.plan import ChangeKind, Plan, ValidationReport


class CoreError(RuntimeError):
    """Raised when a module violates the server-setup core contract."""


class ServerSetupCore:
    """Coordinate planning, application, and validation across host modules."""

    def __init__(self, config: ServerSetupConfig, modules: Iterable[ServerModule]) -> None:
        self.config = config
        self.modules = tuple(modules)

        names = [module.name for module in self.modules]
        if any(not name.strip() for name in names):
            raise CoreError("Module names must not be empty")
        if len(names) != len(set(names)):
            raise CoreError("Module names must be unique")

    def plan(self) -> Plan:
        changes = []
        for module in self.modules:
            current = module.inspect()
            desired = module.desired(self.config)
            module_changes = tuple(module.plan(current, desired))
            for change in module_changes:
                if change.module != module.name:
                    raise CoreError(f"Module {module.name!r} returned a change owned by {change.module!r}")
            changes.extend(module_changes)
        return Plan(tuple(changes))

    def apply(self, plan: Plan | None = None) -> Plan:
        selected_plan = plan if plan is not None else self.plan()
        known_modules = {module.name for module in self.modules}
        unknown_modules = {change.module for change in selected_plan.changes} - known_modules
        if unknown_modules:
            names = ", ".join(sorted(unknown_modules))
            raise CoreError(f"Plan contains changes for unknown module(s): {names}")

        for module in self.modules:
            changes = tuple(change for change in selected_plan.for_module(module.name) if change.kind is not ChangeKind.NOOP)
            if changes:
                module.apply(changes)

        return selected_plan

    def validate(self) -> ValidationReport:
        results = []
        for module in self.modules:
            desired = module.desired(self.config)
            module_results = tuple(module.validate(desired))
            for result in module_results:
                if result.module != module.name:
                    raise CoreError(f"Module {module.name!r} returned validation owned by {result.module!r}")
            results.extend(module_results)
        return ValidationReport(tuple(results))
