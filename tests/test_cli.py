from __future__ import annotations

import unittest
from pathlib import Path

from server_setup.cli import run
from server_setup.config import parse_config
from tests.fake_system import FakeSystem

SAFE_CONFIG = '''version = 1
[host]
timezone = "UTC"
unattended_upgrades = false
[security]
firewall = false
fail2ban = false
ssh_hardening = false
[dokploy]
enabled = false
version = "v0.30.2"
[dns]
enabled = false
[monitoring]
uptime_kuma = false
beszel = false
'''


class CliTests(unittest.TestCase):
    def _config(self, system: FakeSystem, name: str = "config.toml", text: str = SAFE_CONFIG) -> Path:
        path = Path(f"/tmp/{name}")
        system.files[str(path)] = text
        return path

    def test_noninteractive_setup_writes_versioned_config_without_applying(self) -> None:
        system = FakeSystem()
        path = Path("/tmp/test-server-setup-config.toml")
        code = run(
            ["setup", "--config", str(path), "--non-interactive", "--no-apply"],
            system=system,
        )
        self.assertEqual(code, 0)
        self.assertEqual(system.modes[str(path)], 0o600)
        self.assertEqual(parse_config(system.files[str(path)]).version, 1)
        self.assertFalse(system.installed)

    def test_noninteractive_setup_requires_explicit_yes_before_mutation(self) -> None:
        system = FakeSystem()
        path = self._config(system, "test-server-setup-consent.toml")

        code = run(["setup", "--config", str(path), "--non-interactive"], system=system)

        self.assertEqual(code, 2)
        self.assertFalse(system.installed)

    def test_noninteractive_setup_applies_with_explicit_yes(self) -> None:
        system = FakeSystem()
        path = self._config(system, "test-server-setup-consent.toml")

        code = run(["setup", "--config", str(path), "--non-interactive", "--yes"], system=system)

        self.assertEqual(code, 0)

    def test_apply_and_second_plan_are_idempotent_for_safe_config(self) -> None:
        system = FakeSystem()
        path = self._config(system)

        self.assertEqual(run(["apply", "--config", str(path), "--yes"], system=system), 0)
        first_count = len(system.calls)
        self.assertEqual(run(["plan", "--config", str(path)], system=system), 0)
        self.assertGreater(len(system.calls), first_count)

    def test_apply_requires_root(self) -> None:
        system = FakeSystem()
        system.euid = 1000
        path = self._config(system)

        self.assertEqual(run(["apply", "--config", str(path), "--yes"], system=system), 2)

    def test_dangerous_firewall_change_requires_explicit_flag(self) -> None:
        system = FakeSystem()
        dangerous = SAFE_CONFIG.replace("firewall = false", "firewall = true")
        path = self._config(system, text=dangerous)

        self.assertEqual(run(["apply", "--config", str(path), "--yes"], system=system), 2)
        self.assertFalse(system.ufw_active)
        self.assertEqual(
            run(["apply", "--config", str(path), "--yes", "--allow-dangerous"], system=system),
            0,
        )
        self.assertTrue(system.ufw_active)


if __name__ == "__main__":
    unittest.main()
