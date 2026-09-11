from __future__ import annotations

import unittest
from pathlib import Path
from typing import Mapping, Sequence

from server_setup.cli import run
from server_setup.system import CommandError, CommandResult
from tests.fake_system import FakeSystem
from tests.test_cli import SAFE_CONFIG


class FailingApplySystem(FakeSystem):
    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        if tuple(args) == ("apt-get", "update", "-y") and check:
            result = CommandResult(1, stderr="simulated apt failure")
            raise CommandError(args, result)
        return super().run(args, check=check, env=env)


class CliCommandErrorTests(unittest.TestCase):
    def test_checked_command_failure_returns_controlled_cli_error(self) -> None:
        system = FailingApplySystem()
        path = Path("/tmp/test-server-setup-command-error.toml")
        system.files[str(path)] = SAFE_CONFIG

        code = run(["apply", "--config", str(path), "--yes"], system=system)

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
