#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT_DIR / "scripts" / "setup_letsencrypt.py"
    spec = importlib.util.spec_from_file_location("setup_letsencrypt", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SetupLetsEncryptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_existing_certificate_path_uses_idempotent_certbot_flags(self) -> None:
        commands: list[list[str]] = []

        def capture_run_checked(cmd: list[str], env=None, allow_fail: bool = False):
            commands.append(cmd)

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        with patch.object(sys, "argv", ["setup_letsencrypt.py", "--domain", "example.com", "--email", "ops@example.com", "--www"]):
            with patch.object(self.module, "require_root"), patch.object(self.module, "run_checked", side_effect=capture_run_checked):
                self.module.main()

        certbot_cmd = next(cmd for cmd in commands if cmd and cmd[0] == "certbot" and "--nginx" in cmd)
        self.assertIn("--keep-until-expiring", certbot_cmd)
        self.assertIn("--expand", certbot_cmd)
        self.assertIn("--cert-name", certbot_cmd)
        self.assertIn("example.com", certbot_cmd)


if __name__ == "__main__":
    unittest.main()
