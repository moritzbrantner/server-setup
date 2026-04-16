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
    path = ROOT_DIR / "scripts" / "repository_secrets.py"
    spec = importlib.util.spec_from_file_location("repository_secrets", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class RepositorySecretsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_discover_workflow_secret_references_collects_dot_and_bracket_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            workflow_dir = checkout / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "deploy.yml").write_text(
                "env:\n"
                "  API_TOKEN: ${{ secrets.API_TOKEN }}\n"
                "  PRIVATE_KEY: ${{ secrets['PRIVATE_KEY'] }}\n",
                encoding="utf-8",
            )

            discovered = self.module.discover_workflow_secret_references(checkout)

        self.assertEqual(
            discovered,
            {
                "API_TOKEN": [".github/workflows/deploy.yml"],
                "PRIVATE_KEY": [".github/workflows/deploy.yml"],
            },
        )

    def test_select_secret_env_file_prefers_root_env_example(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            (checkout / ".env.example").write_text("TOKEN=\n", encoding="utf-8")

            target = self.module.select_secret_env_file(checkout)

        self.assertEqual(target, checkout / ".env")

    def test_set_repository_secret_seeds_target_from_example(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            (checkout / ".env.example").write_text("# demo\nTOKEN=\n", encoding="utf-8")

            payload = self.module.set_repository_secret(checkout, "TOKEN", "secret")
            body = (checkout / ".env").read_text(encoding="utf-8")

        self.assertIn("TOKEN=secret\n", body)
        self.assertEqual(payload["envFilePath"], str(checkout / ".env"))

    def test_list_repository_secrets_merges_workflow_and_env_file_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            workflow_dir = checkout / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "deploy.yml").write_text(
                "env:\n  TOKEN: ${{ secrets.API_TOKEN }}\n",
                encoding="utf-8",
            )
            (checkout / ".env").write_text("API_TOKEN=present\nPORT=3000\n", encoding="utf-8")

            payload = self.module.list_repository_secrets(checkout)

        self.assertEqual(payload["workflowFiles"], [".github/workflows/deploy.yml"])
        secret_names = [entry["name"] for entry in payload["secrets"]]
        self.assertEqual(secret_names, ["API_TOKEN", "PORT"])
        self.assertTrue(payload["secrets"][0]["configured"])

    def test_delete_repository_secret_removes_key_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            env_file = checkout / ".env"
            env_file.write_text("TOKEN=present\nKEEP=yes\n", encoding="utf-8")

            self.module.delete_repository_secret(checkout, "TOKEN")

            body = env_file.read_text(encoding="utf-8")

        self.assertEqual(body, "KEEP=yes\n")

    def test_set_repository_secret_quotes_multiline_values_for_env_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)

            self.module.set_repository_secret(checkout, "PRIVATE_KEY", "line1\nline2")

            body = (checkout / ".env").read_text(encoding="utf-8")

        self.assertEqual(body, 'PRIVATE_KEY="line1\\nline2"\n')


if __name__ == "__main__":
    unittest.main()
