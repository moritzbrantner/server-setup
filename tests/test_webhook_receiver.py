#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import pathlib
import unittest


def load_module():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "webhook-receiver.py"
    spec = importlib.util.spec_from_file_location("webhook_receiver", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
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

    def test_repo_filter_accepts_matching_push(self) -> None:
        os.environ["WEBHOOK_ALLOWED_REPOS"] = "org/repo,org/other"
        try:
            module = load_module()
            payload = {"ref": "refs/heads/main", "repository": {"full_name": "org/repo"}}
            self.assertTrue(module.should_trigger_deploy(payload))
        finally:
            os.environ.pop("WEBHOOK_ALLOWED_REPOS", None)


if __name__ == "__main__":
    unittest.main()
