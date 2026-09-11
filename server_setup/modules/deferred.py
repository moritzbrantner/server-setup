"""Explicit placeholders for optional v1 features not reconciled by PR2 yet."""

from __future__ import annotations
from collections.abc import Callable
from server_setup.config import ServerSetupConfig
from server_setup.modules.base import ModuleApplyError
from server_setup.plan import Change, ChangeKind, ValidationResult, ValidationStatus

class DeferredFeatureModule:
    def __init__(self, name: str, enabled: Callable[[ServerSetupConfig], bool]) -> None:
        self.name = name
        self._enabled = enabled
    def inspect(self) -> bool: return False
    def desired(self, config: ServerSetupConfig) -> bool: return self._enabled(config)
    def plan(self, current: object, desired: object) -> tuple[Change, ...]:
        if desired is True: return (Change(self.name, ChangeKind.DANGEROUS, f"{self.name} management is not available in this migration slice", "Leave this feature disabled until its existing implementation is evaluated in the optional-services cleanup.", action="deferred"),)
        return ()
    def apply(self, changes: tuple[Change, ...]) -> None:
        if changes: raise ModuleApplyError(f"{self.name} management is deferred")
    def validate(self, desired: object) -> tuple[ValidationResult, ...]:
        if desired is True: return (ValidationResult(self.name, ValidationStatus.FAIL, f"{self.name} management is enabled but not implemented by the new core yet"),)
        return (ValidationResult(self.name, ValidationStatus.SKIP, f"{self.name} is not managed"),)
