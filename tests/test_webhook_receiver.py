#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from types import SimpleNamespace
from unittest.mock import patch

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))


def load_module():
    path = ROOT_DIR / "scripts" / "webhook-receiver.py"
    spec = importlib.util.spec_from_file_location("webhook_receiver", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WebhookReceiverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def _post_webhook(self, payload: dict) -> tuple[int, bytes]:
        server = HTTPServer(("127.0.0.1", 0), self.module.Handler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}{self.module.WEBHOOK_PATH}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"X-GitHub-Event": "push", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()
        finally:
            thread.join(timeout=5)
            server.server_close()

    def _registry_entry(self, checkout_path: pathlib.Path) -> dict:
        return {
            "name": "app",
            "repo_url": "https://github.com/org/repo.git",
            "branch": "main",
            "checkout_path": str(checkout_path),
            "server_conf_path": str(checkout_path / "server.conf"),
            "service_name": "app.service",
            "domain": "app.example.com",
            "webhook_repo": "org/repo",
            "managed_by": "deploy-repo",
            "deploy_config": {
                "name": "app",
                "domain": "app.example.com",
                "build_output": "public",
                "runtime": {"mode": "static"},
                "service": {"name": "app.service"},
            },
        }

    def test_valid_signature_matches(self) -> None:
        payload = b'{"ref":"refs/heads/main"}'
        self.module.WEBHOOK_SECRET = "test-secret"
        digest = hmac.new(b"test-secret", payload, hashlib.sha256).hexdigest()
        self.assertTrue(self.module.valid_signature(payload, f"sha256={digest}"))

    def test_branch_filter_ignores_unmatched_push(self) -> None:
        os.environ["WEBHOOK_ALLOWED_BRANCHES"] = "main"
        try:
            module = load_module()
            payload = {"ref": "refs/heads/feature/demo", "repository": {"full_name": "org/repo"}}
            self.assertFalse(module.should_trigger_deploy(payload))
        finally:
            os.environ.pop("WEBHOOK_ALLOWED_BRANCHES", None)

    def test_repo_filter_accepts_matching_registered_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = pathlib.Path(tmp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "app",
                            "repo_url": "https://github.com/org/repo.git",
                            "branch": "main",
                            "checkout_path": "/srv/apps/app",
                            "server_conf_path": "/srv/apps/app/server.conf",
                            "service_name": "app.service",
                            "domain": "app.example.com",
                            "webhook_repo": "org/repo",
                            "managed_by": "deploy-repo",
                            "deploy_config": {"runtime": {"mode": "static"}, "service": {"name": "app.service"}},
                        }
                    ]
                ),
                encoding="utf-8",
            )
            os.environ["WEBHOOK_ALLOWED_REPOS"] = "org/repo,org/other"
            os.environ["REGISTRY_PATH"] = str(registry_path)
            try:
                module = load_module()
                payload = {"ref": "refs/heads/main", "repository": {"full_name": "org/repo"}}
                self.assertTrue(module.should_trigger_deploy(payload))
            finally:
                os.environ.pop("WEBHOOK_ALLOWED_REPOS", None)
                os.environ.pop("REGISTRY_PATH", None)

    def test_refresh_registry_entry_updates_checkout_before_deploy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = pathlib.Path(tmp) / "registry.json"
            checkout_path = pathlib.Path(tmp) / "app"
            entry = {
                "name": "app",
                "repo_url": "https://github.com/org/repo.git",
                "branch": "main",
                "checkout_path": str(checkout_path),
            }
            self.module.REGISTRY_PATH = registry_path
            self.module.LOG_DIR = pathlib.Path(tmp) / "logs"

            with patch.object(self.module, "clone_or_update_checkout", return_value="main") as clone:
                with patch.object(self.module, "build_registry_entry", return_value={**entry, "refreshed": True}) as build:
                    refreshed = self.module.refresh_registry_entry(entry)

        self.assertTrue(refreshed["refreshed"])
        clone.assert_called_once_with("https://github.com/org/repo.git", checkout_path.resolve(), "main")
        build.assert_called_once_with(registry_path, "https://github.com/org/repo.git", "main", checkout_path.resolve())

    def test_handler_refreshes_checkout_and_deploys_registered_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = pathlib.Path(tmp) / "registry.json"
            checkout_path = pathlib.Path(tmp) / "app"
            entry = self._registry_entry(checkout_path)
            registry_path.write_text(json.dumps([entry]), encoding="utf-8")
            self.module.REGISTRY_PATH = registry_path
            self.module.LOG_DIR = pathlib.Path(tmp) / "logs"
            self.module.WEBHOOK_SECRET = ""
            self.module.WEBHOOK_ALLOW_INSECURE = True
            self.module.WEBHOOK_ALLOWED_REPOS = set()
            self.module.WEBHOOK_ALLOWED_BRANCHES = set()
            self.module.DEFAULT_TLS_EMAIL = "ops@example.com"
            payload = {"ref": "refs/heads/main", "repository": {"full_name": "org/repo"}}

            with patch.object(self.module, "clone_or_update_checkout", return_value="main") as clone:
                with patch.object(self.module, "build_registry_entry", return_value=entry) as build:
                    with patch.object(
                        self.module,
                        "deploy_registry_entry",
                        return_value=SimpleNamespace(name="app"),
                    ) as deploy:
                        status, body = self._post_webhook(payload)

            log_body = "\n".join(path.read_text(encoding="utf-8") for path in self.module.LOG_DIR.glob("*.log"))

        self.assertEqual(status, 202)
        self.assertEqual(body, b"deploy triggered")
        clone.assert_called_once_with("https://github.com/org/repo.git", checkout_path.resolve(), "main")
        build.assert_called_once_with(registry_path, "https://github.com/org/repo.git", "main", checkout_path.resolve())
        deploy.assert_called_once()
        self.assertIn('"action": "checkout refresh"', log_body)
        self.assertIn('"action": "registry refresh"', log_body)
        self.assertIn('"action": "deploy"', log_body)

    def test_handler_records_failed_state_when_checkout_refresh_fails_before_deploy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = pathlib.Path(tmp) / "registry.json"
            checkout_path = pathlib.Path(tmp) / "app"
            entry = self._registry_entry(checkout_path)
            registry_path.write_text(json.dumps([entry]), encoding="utf-8")
            state_dir = pathlib.Path(tmp) / "state"
            self.module.REGISTRY_PATH = registry_path
            self.module.LOG_DIR = pathlib.Path(tmp) / "logs"
            self.module.WEBHOOK_SECRET = ""
            self.module.WEBHOOK_ALLOW_INSECURE = True
            self.module.WEBHOOK_ALLOWED_REPOS = set()
            self.module.WEBHOOK_ALLOWED_BRANCHES = set()
            self.module.DEFAULT_TLS_EMAIL = "ops@example.com"
            payload = {"ref": "refs/heads/main", "repository": {"full_name": "org/repo"}}

            with patch.dict(
                os.environ,
                {
                    "STATE_DIR": str(state_dir),
                    "LOCK_DIR": str(pathlib.Path(tmp) / "locks"),
                    "LOG_DIR": str(pathlib.Path(tmp) / "deploy-logs"),
                },
            ):
                with patch.object(self.module, "clone_or_update_checkout", side_effect=RuntimeError("fetch failed")):
                    with patch.object(self.module, "deploy_registry_entry") as deploy:
                        status, body = self._post_webhook(payload)

            state = json.loads((state_dir / "app.json").read_text(encoding="utf-8"))

        self.assertEqual(status, 500)
        self.assertEqual(body, b"deploy failed")
        deploy.assert_not_called()
        self.assertEqual(state["last_deploy_status"], "failed")
        self.assertIn("fetch failed", state["last_failure_reason"])
        self.assertNotEqual(state.get("last_deploy_status"), "success")


if __name__ == "__main__":
    unittest.main()
