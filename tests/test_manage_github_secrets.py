#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))


def load_module():
    path = ROOT_DIR / "scripts" / "manage_github_secrets.py"
    spec = importlib.util.spec_from_file_location("manage_github_secrets", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ManageGithubSecretsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_normalize_repo_full_name_accepts_direct_repo_name(self) -> None:
        self.assertEqual(self.module.normalize_repo_full_name("example/app"), "example/app")

    def test_main_lists_workflow_secrets_for_registry_site(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "app"
            (checkout / ".github" / "workflows").mkdir(parents=True)
            (checkout / ".github" / "workflows" / "deploy.yml").write_text(
                "env:\n  TOKEN: ${{ secrets.API_TOKEN }}\n",
                encoding="utf-8",
            )
            (checkout / ".env").write_text("API_TOKEN=present\n", encoding="utf-8")
            registry_path = Path(tmp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "app",
                            "repo_url": "https://github.com/example/app.git",
                            "webhook_repo": "example/app",
                            "checkout_path": str(checkout),
                        }
                    ]
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch_argv(
                [
                    "manage_github_secrets.py",
                    "list",
                    "--site",
                    "app",
                    "--config",
                    str(registry_path),
                    "--json",
                ]
            ):
                with contextlib.redirect_stdout(stdout):
                    self.module.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["repo"], "example/app")
        self.assertEqual(payload["siteName"], "app")
        self.assertEqual(payload["envFilePath"], str(checkout / ".env"))
        self.assertEqual(payload["secrets"][0]["name"], "API_TOKEN")
        self.assertEqual(payload["secrets"][0]["requiredByWorkflows"], [".github/workflows/deploy.yml"])

    def test_main_sets_secret_in_repository_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "app"
            checkout.mkdir()

            stdout = io.StringIO()
            with patch_argv(
                [
                    "manage_github_secrets.py",
                    "set",
                    "TOKEN",
                    "--checkout",
                    str(checkout),
                    "--value",
                    "super-secret",
                ]
            ):
                with contextlib.redirect_stdout(stdout):
                    self.module.main()

            body = (checkout / ".env").read_text(encoding="utf-8")

        self.assertIn("TOKEN=super-secret\n", body)
        self.assertIn("Updated repository secret TOKEN", stdout.getvalue())

    def test_main_deletes_secret_from_repo_url_registry_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "app"
            checkout.mkdir()
            (checkout / ".env").write_text("TOKEN=present\n", encoding="utf-8")
            registry_path = Path(tmp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "app",
                            "repo_url": "https://github.com/example/app.git",
                            "webhook_repo": "example/app",
                            "checkout_path": str(checkout),
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with patch_argv(
                [
                    "manage_github_secrets.py",
                    "delete",
                    "TOKEN",
                    "--repo",
                    "https://github.com/example/app.git",
                    "--config",
                    str(registry_path),
                ]
            ):
                self.module.main()

            body = (checkout / ".env").read_text(encoding="utf-8")

        self.assertEqual(body, "")


class patch_argv(contextlib.AbstractContextManager):
    def __init__(self, value: list[str]) -> None:
        self._value = value
        self._previous: list[str] | None = None

    def __enter__(self):
        self._previous = sys.argv[:]
        sys.argv = self._value
        return self

    def __exit__(self, exc_type, exc, tb):
        assert self._previous is not None
        sys.argv = self._previous
        return False


if __name__ == "__main__":
    unittest.main()
