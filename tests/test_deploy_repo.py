#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))


def load_module():
    path = ROOT_DIR / "scripts" / "deploy_repo.py"
    spec = importlib.util.spec_from_file_location("deploy_repo", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DeployRepoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_ensure_server_conf_skips_prompt_when_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = pathlib.Path(tmp) / "server.conf"
            conf_path.write_text("{}", encoding="utf-8")

            with patch.object(self.module, "create_server_conf_interactively") as create_mock:
                result = self.module.ensure_server_conf(tmp)

        self.assertEqual(result, conf_path)
        create_mock.assert_not_called()

    def test_ensure_server_conf_creates_file_interactively_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = pathlib.Path(tmp) / "server.conf"

            with patch.object(self.module, "create_server_conf_interactively", return_value=conf_path) as create_mock:
                with patch.object(self.module.sys, "stdin", types.SimpleNamespace(isatty=lambda: True)):
                    result = self.module.ensure_server_conf(tmp)

        self.assertEqual(result, conf_path)
        create_mock.assert_called_once()

    def test_ensure_server_conf_fails_in_non_interactive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(self.module.sys, "stdin", types.SimpleNamespace(isatty=lambda: False)):
                with self.assertRaisesRegex(SystemExit, "Run deploy_repo.py in an interactive terminal"):
                    self.module.ensure_server_conf(tmp)


if __name__ == "__main__":
    unittest.main()
