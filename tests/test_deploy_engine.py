#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))


def load_module():
    path = ROOT_DIR / "scripts" / "deploy_engine.py"
    spec = importlib.util.spec_from_file_location("deploy_engine", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DeployEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def _base_env(self, tmp: str) -> dict[str, str]:
        return {
            "STATE_DIR": f"{tmp}/state",
            "LOCK_DIR": f"{tmp}/locks",
            "LOG_DIR": f"{tmp}/logs",
            "NGINX_SITE_AVAILABLE_DIR": f"{tmp}/nginx-available",
            "NGINX_SITE_ENABLED_DIR": f"{tmp}/nginx-enabled",
            "NGINX_DEFAULT_SITE_LINK": f"{tmp}/nginx-enabled/default",
            "SYSTEMD_UNIT_DIR": f"{tmp}/systemd",
            "LETSENCRYPT_LIVE_DIR": f"{tmp}/letsencrypt/live",
            "LETSENCRYPT_OPTIONS_PATH": f"{tmp}/letsencrypt/options-ssl-nginx.conf",
            "LETSENCRYPT_DHPARAM_PATH": f"{tmp}/letsencrypt/ssl-dhparams.pem",
        }

    def _run_side_effect(self, cmd, cwd=None, env=None, capture=False):
        if cmd[:2] == ["nginx", "-t"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd and cmd[0] == "curl":
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def test_deploy_registry_entry_succeeds_for_static_site(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._base_env(tmp)
            pathlib.Path(env["NGINX_SITE_AVAILABLE_DIR"]).mkdir(parents=True, exist_ok=True)
            pathlib.Path(env["NGINX_SITE_ENABLED_DIR"]).mkdir(parents=True, exist_ok=True)
            pathlib.Path(env["LETSENCRYPT_OPTIONS_PATH"]).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(env["LETSENCRYPT_OPTIONS_PATH"]).write_text("ssl", encoding="utf-8")
            pathlib.Path(env["LETSENCRYPT_DHPARAM_PATH"]).write_text("dhparam", encoding="utf-8")
            checkout = pathlib.Path(tmp) / "app"
            (checkout / "public").mkdir(parents=True)
            (checkout / "public" / "index.html").write_text("ok", encoding="utf-8")
            entry = {
                "name": "static-app",
                "repo_url": "https://github.com/example/static-app.git",
                "branch": "main",
                "checkout_path": str(checkout),
                "server_conf_path": str(checkout / "server.conf"),
                "service_name": "static-app.service",
                "domain": "static.example.com",
                "webhook_repo": "example/static-app",
                "managed_by": "deploy-repo",
                "deploy_config": {
                    "name": "static-app",
                    "domain": "static.example.com",
                    "build_output": "public",
                    "web_root": None,
                    "deploy_hooks": {"pre_deploy": None, "build": None, "post_deploy": None},
                    "runtime": {
                        "mode": "static",
                        "working_dir": ".",
                        "user": "root",
                        "health_endpoint": "/health",
                        "health_retries": 1,
                        "health_interval_seconds": 1,
                    },
                    "service": {"name": "static-app.service"},
                    "nginx": {"www_redirect": False, "tls_hostnames": ["static.example.com"]},
                },
            }

            with patch.dict(os.environ, env, clear=False):
                with patch.object(self.module, "run_checked", return_value=subprocess.CompletedProcess([], 0, "", "")):
                    with patch.object(self.module, "run", side_effect=self._run_side_effect):
                        with patch.object(self.module, "ensure_dns_points_here", return_value=None):
                            result = self.module.deploy_registry_entry(entry, tls_email="ops@example.com", configure_webhook=False)

            self.assertEqual(result.name, "static-app")
            conf_path = pathlib.Path(env["NGINX_SITE_AVAILABLE_DIR"]) / "static-app.conf"
            self.assertTrue(conf_path.exists())
            state_path = pathlib.Path(env["STATE_DIR"]) / "static-app.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["last_deploy_status"], "success")

    def test_deploy_registry_entry_succeeds_for_service_site(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._base_env(tmp)
            pathlib.Path(env["NGINX_SITE_AVAILABLE_DIR"]).mkdir(parents=True, exist_ok=True)
            pathlib.Path(env["NGINX_SITE_ENABLED_DIR"]).mkdir(parents=True, exist_ok=True)
            pathlib.Path(env["LETSENCRYPT_OPTIONS_PATH"]).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(env["LETSENCRYPT_OPTIONS_PATH"]).write_text("ssl", encoding="utf-8")
            pathlib.Path(env["LETSENCRYPT_DHPARAM_PATH"]).write_text("dhparam", encoding="utf-8")
            checkout = pathlib.Path(tmp) / "app"
            checkout.mkdir()
            entry = {
                "name": "service-app",
                "repo_url": "https://github.com/example/service-app.git",
                "branch": "main",
                "checkout_path": str(checkout),
                "server_conf_path": str(checkout / "server.conf"),
                "service_name": "service-app.service",
                "domain": "service.example.com",
                "webhook_repo": "example/service-app",
                "managed_by": "deploy-repo",
                "deploy_config": {
                    "name": "service-app",
                    "domain": "service.example.com",
                    "build_output": ".",
                    "web_root": None,
                    "deploy_hooks": {"pre_deploy": None, "build": None, "post_deploy": None},
                    "runtime": {
                        "mode": "service",
                        "working_dir": ".",
                        "user": "root",
                        "health_endpoint": "/health",
                        "health_retries": 1,
                        "health_interval_seconds": 1,
                        "command": "PORT=3000 bun run start",
                        "port": 3000,
                    },
                    "service": {"name": "service-app.service"},
                    "nginx": {"www_redirect": False, "tls_hostnames": ["service.example.com"]},
                },
            }

            with patch.dict(os.environ, env, clear=False):
                with patch.object(self.module, "run_checked", return_value=subprocess.CompletedProcess([], 0, "", "")):
                    with patch.object(self.module, "run", side_effect=self._run_side_effect):
                        with patch.object(self.module, "ensure_dns_points_here", return_value=None):
                            result = self.module.deploy_registry_entry(entry, tls_email="ops@example.com", configure_webhook=False)

            self.assertEqual(result.service_name, "service-app.service")
            unit_path = pathlib.Path(env["SYSTEMD_UNIT_DIR"]) / "service-app.service"
            self.assertTrue(unit_path.exists())
            self.assertIn("PORT=3000 bun run start", unit_path.read_text(encoding="utf-8"))

    def test_run_optional_uses_bun_aware_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            with patch.dict(os.environ, {"HOME": "/root", "PATH": "/usr/bin"}, clear=False):
                with patch.object(self.module, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run_mock:
                    self.module.run_optional("bun run build", cwd=checkout)

        _, kwargs = run_mock.call_args
        self.assertEqual(kwargs["cwd"], checkout)
        self.assertTrue(kwargs["capture"])
        self.assertEqual(kwargs["env"]["BUN_INSTALL"], "/root/.bun")
        self.assertTrue(kwargs["env"]["PATH"].startswith("/root/.bun/bin:"))

    def test_maybe_install_node_dependencies_runs_bun_install_for_bun_builds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            (checkout / "package.json").write_text("{}", encoding="utf-8")
            (checkout / "bun.lock").write_text("", encoding="utf-8")

            with patch.dict(os.environ, {"HOME": "/root", "PATH": "/usr/bin"}, clear=False):
                with patch.object(self.module, "run_checked", return_value=subprocess.CompletedProcess([], 0, "", "")) as run_checked_mock:
                    self.module.maybe_install_node_dependencies(checkout, "bun run build", "bun run start")

        args, kwargs = run_checked_mock.call_args
        self.assertEqual(args[0], ["bun", "install"])
        self.assertEqual(kwargs["cwd"], checkout)
        self.assertEqual(kwargs["env"]["BUN_INSTALL"], "/root/.bun")

    def test_maybe_install_node_dependencies_skips_when_build_hook_installs_already(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            (checkout / "package.json").write_text("{}", encoding="utf-8")
            (checkout / "bun.lock").write_text("", encoding="utf-8")

            with patch.object(self.module, "run_checked") as run_checked_mock:
                self.module.maybe_install_node_dependencies(checkout, "bun install && bun run build", "bun run start")

        run_checked_mock.assert_not_called()
