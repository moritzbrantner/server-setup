from __future__ import annotations

import unittest
from pathlib import Path

from server_setup.cli import run
from server_setup.config import parse_config
from server_setup.ui import UiError, collect_state, render_html, validate_loopback_host, validate_port
from tests.fake_system import FakeSystem
from tests.test_cli import SAFE_CONFIG


class UiTests(unittest.TestCase):
    def test_loopback_validation_accepts_only_local_addresses(self) -> None:
        self.assertEqual(validate_loopback_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(validate_loopback_host("localhost"), "localhost")
        self.assertEqual(validate_loopback_host("::1"), "::1")
        for host in ("0.0.0.0", "192.168.1.10", "example.com", ""):
            with self.subTest(host=host), self.assertRaises(UiError):
                validate_loopback_host(host)

    def test_port_validation_fails_closed(self) -> None:
        self.assertEqual(validate_port(8765), 8765)
        for port in (0, 65536, -1):
            with self.subTest(port=port), self.assertRaises(UiError):
                validate_port(port)

    def test_collect_state_is_read_only_and_uses_core_drift_semantics(self) -> None:
        system = FakeSystem()
        config = parse_config(SAFE_CONFIG)

        state = collect_state(config, system)

        self.assertTrue(state["drift"])
        self.assertFalse(system.installed)
        self.assertFalse(any(call[:1] == ("apt-get",) for call in system.calls))

    def test_collect_state_reports_converged_after_normal_apply(self) -> None:
        system = FakeSystem()
        path = Path("/tmp/test-server-setup-ui.toml")
        system.files[str(path)] = SAFE_CONFIG
        self.assertEqual(run(["apply", "--config", str(path), "--yes"], system=system), 0)

        state = collect_state(parse_config(SAFE_CONFIG), system)

        self.assertFalse(state["drift"])
        self.assertEqual(state["changes"], [])

    def test_html_escapes_host_configuration_and_has_no_mutation_controls(self) -> None:
        state = {
            "drift": False,
            "config": {"host": {"timezone": "<script>alert(1)</script>"}},
            "changes": [],
            "validation": [],
        }

        rendered = render_html(state)

        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertNotIn("<form", rendered)
        self.assertNotIn("<button", rendered)

    def test_cli_refuses_non_loopback_ui_before_binding(self) -> None:
        system = FakeSystem()
        path = Path("/tmp/test-server-setup-ui.toml")
        system.files[str(path)] = SAFE_CONFIG

        code = run(["ui", "--config", str(path), "--host", "0.0.0.0"], system=system)

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
