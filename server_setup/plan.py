"""Shared planning and validation result types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ChangeKind(str, Enum):
    NOOP = "noop"
    CREATE = "create"
    UPDATE = "update"
    RESTART = "restart"
    DANGEROUS = "dangerous"


@dataclass(frozen=True, slots=True)
class Change:
    module: str
    kind: ChangeKind
    summary: str
    details: str | None = None
    action: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        if not self.module.strip():
            raise ValueError("change.module must not be empty")
        if not self.summary.strip():
            raise ValueError("change.summary must not be empty")


@dataclass(frozen=True, slots=True)
class Plan:
    changes: tuple[Change, ...] = ()

    @property
    def has_changes(self) -> bool:
        return any(change.kind is not ChangeKind.NOOP for change in self.changes)

    @property
    def has_dangerous_changes(self) -> bool:
        return any(change.kind is ChangeKind.DANGEROUS for change in self.changes)

    def for_module(self, module: str) -> tuple[Change, ...]:
        return tuple(change for change in self.changes if change.module == module)


class ValidationStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    module: str
    status: ValidationStatus
    summary: str
    details: str | None = None

    def __post_init__(self) -> None:
        if not self.module.strip():
            raise ValueError("validation.module must not be empty")
        if not self.summary.strip():
            raise ValueError("validation.summary must not be empty")


@dataclass(frozen=True, slots=True)
class ValidationReport:
    results: tuple[ValidationResult, ...] = ()

    @property
    def ok(self) -> bool:
        return all(result.status is not ValidationStatus.FAIL for result in self.results)
