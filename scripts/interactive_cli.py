#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def repo_root() -> Path:
    return script_dir().parent


def shell_script(name: str) -> Path:
    return script_dir() / name


def prompt_text(prompt: str, default: str | None = None, required: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""


def prompt_bool(prompt: str, default: bool = False) -> bool:
    default_hint = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{default_hint}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False


def ensure_interactive(args: argparse.Namespace, required: list[str]) -> None:
    missing = [name for name in required if not getattr(args, name)]
    if missing and not (args.interactive or sys.stdin.isatty()):
        names = ", ".join(f"--{name.replace('_', '-')}" for name in missing)
        raise SystemExit(f"Missing required arguments: {names}")


def run_command(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, cwd=repo_root())


def maybe_sudo(cmd: list[str]) -> list[str]:
    if os.geteuid() == 0:
        return cmd
    return ["sudo", *cmd]
