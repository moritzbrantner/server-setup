#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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

    def test_main_lists_secrets_for_registry_site(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = pathlib.Path(tmp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "app",
                            "repo_url": "https://github.com/example/app.git",
                            "webhook_repo": "example/app",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "manage_github_secrets.py",
                    "list",
                    "--site",
                    "app",
                    "--config",
                    str(registry_path),
                    "--json",
                ],
            ):
                with patch.object(self.module, "shutil_which", return_value="/usr/bin/gh"):
                    with patch.object(
                        self.module.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess(
                            ["gh", "secret", "list"],
                            0,
                            stdout='[{"name":"API_KEY","updatedAt":"2026-04-01T10:00:00Z","visibility":"private","numSelectedRepos":0}]',
                            stderr="",
                        ),
                    ):
                        with contextlib.redirect_stdout(stdout):
                            self.module.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["repo"], "example/app")
        self.assertEqual(payload["siteName"], "app")
        self.assertEqual(payload["secrets"][0]["name"], "API_KEY")

    def test_main_sets_secret_with_stdin_body(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run(cmd, **kwargs):
            calls.append({"cmd": cmd, "input": kwargs.get("input")})
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        stdout = io.StringIO()
        with patch.object(
            sys,
            "argv",
            [
                "manage_github_secrets.py",
                "set",
                "TOKEN",
                "--repo",
                "example/app",
                "--value",
                "super-secret",
            ],
        ):
            with patch.object(self.module, "shutil_which", return_value="/usr/bin/gh"):
                with patch.object(self.module.subprocess, "run", side_effect=fake_run):
                    with contextlib.redirect_stdout(stdout):
                        self.module.main()

        self.assertEqual(
            calls[0]["cmd"],
            ["gh", "secret", "set", "TOKEN", "--repo", "example/app"],
        )
        self.assertEqual(calls[0]["input"], "super-secret")
        self.assertIn("Updated GitHub secret TOKEN", stdout.getvalue())

    def test_main_deletes_secret_for_repo_url(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(
            sys,
            "argv",
            [
                "manage_github_secrets.py",
                "delete",
                "TOKEN",
                "--repo",
                "https://github.com/example/app.git",
            ],
        ):
            with patch.object(self.module, "shutil_which", return_value="/usr/bin/gh"):
                with patch.object(self.module.subprocess, "run", side_effect=fake_run):
                    self.module.main()

        self.assertEqual(
            calls[0],
            ["gh", "secret", "delete", "TOKEN", "--repo", "example/app"],
        )


if __name__ == "__main__":
    unittest.main()
