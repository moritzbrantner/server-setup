from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server_setup.config import (
    CONFIG_VERSION,
    ConfigError,
    ServerSetupConfig,
    load_config,
    parse_config,
    render_config,
)


class ServerSetupConfigTests(unittest.TestCase):
    def test_minimal_config_uses_secure_progressive_defaults(self) -> None:
        config = parse_config("version = 1\n")

        self.assertEqual(config.version, CONFIG_VERSION)
        self.assertEqual(config.host.timezone, "UTC")
        self.assertTrue(config.host.unattended_upgrades)
        self.assertTrue(config.security.firewall)
        self.assertTrue(config.security.fail2ban)
        self.assertFalse(config.security.ssh_hardening)
        self.assertTrue(config.dokploy.enabled)
        self.assertFalse(config.dns.enabled)
        self.assertFalse(config.monitoring.uptime_kuma)
        self.assertFalse(config.monitoring.beszel)

    def test_full_config_parses(self) -> None:
        config = parse_config(
            """
version = 1

[host]
timezone = "Europe/Berlin"
unattended_upgrades = false

[security]
firewall = false
fail2ban = false
ssh_hardening = true

[dokploy]
enabled = false
version = "v9.9.9"

[dns]
enabled = true

[monitoring]
uptime_kuma = true
beszel = true
"""
        )

        self.assertEqual(config.host.timezone, "Europe/Berlin")
        self.assertFalse(config.host.unattended_upgrades)
        self.assertFalse(config.security.firewall)
        self.assertFalse(config.security.fail2ban)
        self.assertTrue(config.security.ssh_hardening)
        self.assertFalse(config.dokploy.enabled)
        self.assertEqual(config.dokploy.version, "v9.9.9")
        self.assertTrue(config.dns.enabled)
        self.assertTrue(config.monitoring.uptime_kuma)
        self.assertTrue(config.monitoring.beszel)

    def test_unknown_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "Unknown key"):
            parse_config("version = 1\nmagic = true\n")

        with self.assertRaisesRegex(ConfigError, "Unknown key"):
            parse_config("version = 1\n[security]\nmagic = true\n")

    def test_missing_or_unsupported_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "version must be an integer"):
            parse_config("")

        with self.assertRaisesRegex(ConfigError, "Unsupported config version"):
            parse_config("version = 2\n")

    def test_invalid_field_types_are_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "security.firewall must be a boolean"):
            parse_config('version = 1\n[security]\nfirewall = "yes"\n')

        with self.assertRaisesRegex(ConfigError, "host.timezone must be a non-empty string"):
            parse_config('version = 1\n[host]\ntimezone = ""\n')

    def test_render_round_trips(self) -> None:
        config = ServerSetupConfig()
        rendered = render_config(config)

        self.assertEqual(parse_config(rendered), config)

    def test_load_config_wraps_file_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.toml"
            with self.assertRaisesRegex(ConfigError, "Unable to read configuration"):
                load_config(missing)

    def test_load_config_reads_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text("version = 1\n", encoding="utf-8")
            self.assertEqual(load_config(path), ServerSetupConfig())


if __name__ == "__main__":
    unittest.main()
