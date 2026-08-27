"""Core types for the server-setup host bootstrap tool."""

from .config import CONFIG_VERSION, DEFAULT_CONFIG_PATH, ServerSetupConfig, load_config, parse_config, render_config
from .core import CoreError, ServerSetupCore
from .plan import Change, ChangeKind, Plan, ValidationReport, ValidationResult, ValidationStatus

__all__ = [
    "CONFIG_VERSION",
    "DEFAULT_CONFIG_PATH",
    "Change",
    "ChangeKind",
    "CoreError",
    "Plan",
    "ServerSetupConfig",
    "ServerSetupCore",
    "ValidationReport",
    "ValidationResult",
    "ValidationStatus",
    "load_config",
    "parse_config",
    "render_config",
]
