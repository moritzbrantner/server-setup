"""Contract implemented by server-setup host-management modules."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from server_setup.config import ServerSetupConfig
from server_setup.plan import Change, ValidationResult


@runtime_checkable
class ServerModule(Protocol):
    """One independently plannable host-management responsibility.

    Modules inspect real host state, derive desired state from the shared config,
    produce a read-only plan, apply only their own planned changes, and validate
    the resulting host state. Application deployment is intentionally outside
    this contract and remains Dokploy's responsibility.
    """

    name: str

    def inspect(self) -> object:
        """Return the current state required for planning."""
        ...

    def desired(self, config: ServerSetupConfig) -> object:
        """Return this module's desired state for the supplied configuration."""
        ...

    def plan(self, current: object, desired: object) -> Sequence[Change]:
        """Describe required changes without mutating the host."""
        ...

    def apply(self, changes: Sequence[Change]) -> None:
        """Apply previously planned changes owned by this module."""
        ...

    def validate(self, desired: object) -> Sequence[ValidationResult]:
        """Read host state and report whether the desired state is satisfied."""
        ...
