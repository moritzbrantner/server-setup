"""Contract implemented by server-setup host-management modules."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from server_setup.config import ServerSetupConfig
from server_setup.plan import Change, ValidationResult


class ModuleApplyError(RuntimeError):
    """Raised when a planned host change cannot be safely applied."""


@runtime_checkable
class ServerModule(Protocol):
    name: str
    def inspect(self) -> object: ...
    def desired(self, config: ServerSetupConfig) -> object: ...
    def plan(self, current: object, desired: object) -> Sequence[Change]: ...
    def apply(self, changes: Sequence[Change]) -> None: ...
    def validate(self, desired: object) -> Sequence[ValidationResult]: ...
