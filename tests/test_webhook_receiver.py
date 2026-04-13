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
import unittest

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


if __name__ == "__main__":
    unittest.main()
