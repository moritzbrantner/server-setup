from __future__ import annotations

import tempfile
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
    def test_noninteractive_setup_writes_versioned_config_without_applying(self) -> None:
        system = FakeSystem(); path = Path("/tmp/test-server-setup-config.toml"); code = run(["setup", "--config", str(path), "--non-interactive", "--no-apply"], system=system); self.assertEqual(code, 0); self.assertEqual(system.modes[str(path)], 0o600); self.assertEqual(parse_config(system.files[str(path)]).version, 1); self.assertFalse(system.installed)
    def test_apply_and_second_plan_are_idempotent_for_safe_config(self) -> None:
        system = FakeSystem()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"; path.write_text(SAFE_CONFIG, encoding="utf-8"); self.assertEqual(run(["apply", "--config", str(path), "--yes"], system=system), 0); first_count = len(system.calls); self.assertEqual(run(["plan", "--config", str(path)], system=system), 0); self.assertGreater(len(system.calls), first_count)
    def test_apply_requires_root(self) -> None:
        system = FakeSystem(); system.euid = 1000
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"; path.write_text(SAFE_CONFIG, encoding="utf-8"); self.assertEqual(run(["apply", "--config", str(path), "--yes"], system=system), 2)
    def test_dangerous_firewall_change_requires_explicit_flag(self) -> None:
        system = FakeSystem(); dangerous = SAFE_CONFIG.replace("firewall = false", "firewall = true")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"; path.write_text(dangerous, encoding="utf-8"); self.assertEqual(run(["apply", "--config", str(path), "--yes"], system=system), 2); self.assertFalse(system.ufw_active); self.assertEqual(run(["apply", "--config", str(path), "--yes", "--allow-dangerous"], system=system), 0); self.assertTrue(system.ufw_active)

if __name__ == "__main__": unittest.main()
