#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

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
                "STATUS_WEBAPP_ADMIN_TOKEN=keep-me\n"
                "PORKBUN_API_KEY=keep-porkbun\n",
                encoding="utf-8",
            )

            self.module.write_status_webapp_env(env_file, "/new/root", "0.0.0.0", "4000")

            body = env_file.read_text(encoding="utf-8")

        self.assertIn("SERVER_SETUP_ROOT=/new/root\n", body)
        self.assertIn("BUN_INSTALL=/root/.bun\n", body)
        self.assertIn("STATUS_WEBAPP_HOST=0.0.0.0\n", body)
        self.assertIn("STATUS_WEBAPP_PORT=4000\n", body)
        self.assertIn("STATUS_WEBAPP_ADMIN_TOKEN=keep-me\n", body)
        self.assertIn("STATUS_CONFIG_PATH=/new/root/deploy/registry.json\n", body)
        self.assertIn("STATUS_STATE_DIR=/var/lib/server-setup/state\n", body)
        self.assertIn("STATUS_WEBAPP_GITHUB_TOKEN=\n", body)
        self.assertIn("PORKBUN_API_KEY=keep-porkbun\n", body)

    def test_write_status_webapp_env_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = pathlib.Path(tmp) / "server-setup-status-webapp"

            self.module.write_status_webapp_env(env_file, "/srv/server-setup", "0.0.0.0", "4000")
            first = env_file.read_text(encoding="utf-8")

            self.module.write_status_webapp_env(env_file, "/srv/server-setup", "0.0.0.0", "4000")
            second = env_file.read_text(encoding="utf-8")

        self.assertEqual(first, second)

    def test_render_status_webapp_nginx_uses_server_name_and_proxy_port(self) -> None:
        body = self.module.render_status_webapp_nginx("status.example.com", "4100")

        self.assertIn("server_name status.example.com;", body)
        self.assertIn("proxy_pass http://127.0.0.1:4100;", body)

    def test_install_status_webapp_nginx_writes_site_enables_it_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            available_path = root / "sites-available" / "status.conf"
            enabled_path = root / "sites-enabled" / "status.conf"

            with mock.patch.object(self.module.subprocess, "run") as run:
                self.module.install_status_webapp_nginx(
                    "monitor.localhost",
                    "4000",
                    available_path=available_path,
                    enabled_path=enabled_path,
                )

            body = available_path.read_text(encoding="utf-8")
            self.assertTrue(enabled_path.is_symlink())
            self.assertEqual(enabled_path.resolve(), available_path)

        self.assertIn("server_name monitor.localhost;", body)
        run.assert_has_calls(
            [
                mock.call(["nginx", "-t"], check=True),
                mock.call(["systemctl", "reload", "nginx"], check=True),
            ]
        )


if __name__ == "__main__":
    unittest.main()
