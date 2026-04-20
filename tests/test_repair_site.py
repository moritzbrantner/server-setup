#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))


def load_module():
    path = ROOT_DIR / "scripts" / "repair_site.py"
    spec = importlib.util.spec_from_file_location("repair_site", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RepairSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def _registry_entry(self, checkout: pathlib.Path) -> dict:
        return {
            "name": "app",
            "repo_url": "https://github.com/example/app.git",
            "branch": "main",
            "checkout_path": str(checkout),
            "domain": "app.example.com",
            "service_name": "app.service",
            "deploy_config": {
                "name": "app",
                "domain": "app.example.com",
                "build_output": "public",
                "deploy_hooks": {"pre_deploy": None, "build": "npm run build", "post_deploy": None},
                "runtime": {
                    "mode": "static",
                    "working_dir": ".",
                    "user": "root",
                    "health_endpoint": "/health",
                    "health_retries": 20,
                    "health_interval_seconds": 2,
                },
                "service": {"name": "app.service"},
                "nginx": {"www_redirect": False, "tls_hostnames": ["app.example.com"]},
                "dns": None,
            },
        }

    def test_write_missing_server_conf_from_registry_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp) / "app"
            checkout.mkdir()
            entry = self._registry_entry(checkout)

            created, message = self.module.write_missing_server_conf_from_registry(checkout, entry)
            first_body = (checkout / "server.conf").read_text(encoding="utf-8")
            second_created, second_message = self.module.write_missing_server_conf_from_registry(checkout, entry)
            second_body = (checkout / "server.conf").read_text(encoding="utf-8")

        self.assertTrue(created)
        self.assertIn("created missing server.conf", message)
        self.assertFalse(second_created)
        self.assertIn("existing server.conf is valid", second_message)
        self.assertEqual(first_body, second_body)
        payload = json.loads(first_body)
        self.assertEqual(payload["name"], "app")
        self.assertEqual(payload["deploy_hooks"]["build"], "npm run build")

    def test_dry_run_reports_non_destructive_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp) / "app"
            checkout.mkdir()
            registry_path = pathlib.Path(tmp) / "registry.json"
            registry_path.write_text(json.dumps([self._registry_entry(checkout)]), encoding="utf-8")

            args = argparse.Namespace(
                site="app",
                config=str(registry_path),
                email="",
                configure_github_hook=False,
                dry_run=True,
                json=False,
            )
            result = self.module.repair_site(args)

        self.assertTrue(result["dryRun"])
        self.assertFalse(result["changed"])
        self.assertFalse((checkout / "server.conf").exists())
        self.assertTrue(any("create missing" in item for item in result["plan"]))

    def test_repair_refuses_tracked_local_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp) / "app"
            (checkout / ".git").mkdir(parents=True)

            with patch.object(
                self.module,
                "run_checked",
                return_value=subprocess.CompletedProcess(
                    ["git"],
                    0,
                    stdout=" M server.conf\n",
                    stderr="",
                ),
            ):
                with self.assertRaisesRegex(self.module.RepairError, "tracked local changes"):
                    self.module.ensure_no_tracked_local_changes(checkout)

    def test_repair_site_refreshes_registry_and_deploys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp) / "app"
            checkout.mkdir()
            (checkout / "public").mkdir()
            registry_path = pathlib.Path(tmp) / "registry.json"
            registry_path.write_text(json.dumps([self._registry_entry(checkout)]), encoding="utf-8")
            env_file = pathlib.Path(tmp) / "site-automation"
            env_file.write_text("DEFAULT_TLS_EMAIL=ops@example.com\nWEBHOOK_SECRET=test\n", encoding="utf-8")
            args = argparse.Namespace(
                site="app",
                config=str(registry_path),
                email="",
                configure_github_hook=False,
                dry_run=False,
                json=False,
            )

            with patch.dict(os.environ, {"SITE_AUTOMATION_ENV_FILE": str(env_file)}, clear=False):
                self.module.AUTOMATION_ENV_FILE = env_file
                with patch.object(self.module, "require_root"):
                    with patch.object(self.module, "setup_automation_units"):
                        with patch.object(self.module, "clone_or_update_checkout", return_value="main"):
                            with patch.object(
                                self.module,
                                "run_checked",
                                return_value=subprocess.CompletedProcess([], 0, "", ""),
                            ):
                                with patch.object(self.module, "update_automation_env", return_value=("example/app", "")):
                                    with patch.object(self.module, "deploy_registry_entry") as deploy:
                                        deploy.return_value = SimpleNamespace(
                                            name="app",
                                            domain="app.example.com",
                                            service_name="app.service",
                                            checkout_path=str(checkout),
                                            branch="main",
                                            webhook_repo="example/app",
                                            webhook_url="",
                                            webhook_secret="test",
                                            hook_status=("skipped", "webhook setup was skipped"),
                                        )
                                        result = self.module.repair_site(args)

            payload = json.loads(registry_path.read_text(encoding="utf-8"))
            server_conf_exists = (checkout / "server.conf").exists()

        self.assertEqual(result["site"], "app")
        self.assertTrue(server_conf_exists)
        self.assertEqual(payload[0]["name"], "app")
        deploy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
