#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))


def load_module():
    path = ROOT_DIR / "scripts" / "setup_status_webapp.py"
    spec = importlib.util.spec_from_file_location("setup_status_webapp", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SetupStatusWebappTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_write_status_webapp_env_preserves_existing_admin_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = pathlib.Path(tmp) / "server-setup-status-webapp"
            env_file.write_text(
                "SERVER_SETUP_ROOT=/old/root\n"
                "BUN_INSTALL=/old/bun\n"
                "STATUS_WEBAPP_HOST=127.0.0.1\n"
                "STATUS_WEBAPP_PORT=9999\n"
                "STATUS_WEBAPP_ADMIN_TOKEN=keep-me\n",
                encoding="utf-8",
            )

            self.module.write_status_webapp_env(env_file, "/new/root", "0.0.0.0", "4000")

            body = env_file.read_text(encoding="utf-8")

        self.assertIn("SERVER_SETUP_ROOT=/new/root\n", body)
        self.assertIn("BUN_INSTALL=/root/.bun\n", body)
        self.assertIn("STATUS_WEBAPP_HOST=0.0.0.0\n", body)
        self.assertIn("STATUS_WEBAPP_PORT=4000\n", body)
        self.assertIn("STATUS_WEBAPP_ADMIN_TOKEN=keep-me\n", body)

    def test_write_status_webapp_env_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = pathlib.Path(tmp) / "server-setup-status-webapp"

            self.module.write_status_webapp_env(env_file, "/srv/server-setup", "0.0.0.0", "4000")
            first = env_file.read_text(encoding="utf-8")

            self.module.write_status_webapp_env(env_file, "/srv/server-setup", "0.0.0.0", "4000")
            second = env_file.read_text(encoding="utf-8")

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
