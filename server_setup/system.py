"""Host IO boundary used by server-setup modules."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandError(RuntimeError):
    def __init__(self, args: Sequence[str], result: CommandResult) -> None:
        self.args_list = tuple(args)
        self.result = result
        command = " ".join(args)
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        super().__init__(f"Command failed: {command}: {detail}")


@runtime_checkable
class System(Protocol):
    def run(self, args: Sequence[str], *, check: bool = False, env: Mapping[str, str] | None = None) -> CommandResult: ...
    def read_text(self, path: Path | str) -> str | None: ...
    def write_text(self, path: Path | str, content: str, *, mode: int | None = None) -> None: ...
    def remove(self, path: Path | str) -> None: ...
    def exists(self, path: Path | str) -> bool: ...
    def command_exists(self, name: str) -> bool: ...
    def getenv(self, name: str, default: str = "") -> str: ...
    def geteuid(self) -> int: ...
    def sleep(self, seconds: float) -> None: ...


class LocalSystem:
    """Real host implementation. Tests replace this with a deterministic fake."""

    def run(self, args: Sequence[str], *, check: bool = False, env: Mapping[str, str] | None = None) -> CommandResult:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        try:
            completed = subprocess.run(list(args), check=False, capture_output=True, text=True, env=merged_env)
            result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
        except FileNotFoundError:
            result = CommandResult(127, stderr=f"command not found: {args[0] if args else '<empty>'}")
        if check and result.returncode != 0:
            raise CommandError(args, result)
        return result

    def read_text(self, path: Path | str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write_text(self, path: Path | str, content: str, *, mode: int | None = None) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        current_mode = mode
        if current_mode is None and target.exists():
            current_mode = target.stat().st_mode & 0o777
        if current_mode is None:
            current_mode = 0o644
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, current_mode)
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def remove(self, path: Path | str) -> None:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass

    def exists(self, path: Path | str) -> bool:
        return Path(path).exists()

    def command_exists(self, name: str) -> bool:
        return shutil.which(name) is not None

    def getenv(self, name: str, default: str = "") -> str:
        return os.environ.get(name, default)

    def geteuid(self) -> int:
        return os.geteuid()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def package_installed(system: System, package: str) -> bool:
    result = system.run(["dpkg-query", "-W", "-f=${Status}", package])
    return result.returncode == 0 and result.stdout.strip() == "install ok installed"


def service_active(system: System, service: str) -> bool:
    if not system.command_exists("systemctl"):
        return False
    return system.run(["systemctl", "is-active", "--quiet", service]).returncode == 0


def service_enabled(system: System, service: str) -> bool:
    if not system.command_exists("systemctl"):
        return False
    return system.run(["systemctl", "is-enabled", "--quiet", service]).returncode == 0
