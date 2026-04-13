#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest


def load_module():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "server_conf_contract.py"
    spec = importlib.util.spec_from_file_location("server_conf_contract", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ServerConfContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_normalize_accepts_minimal_static_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = pathlib.Path(tmp) / "server.conf"
            conf_path.write_text(
                json.dumps(
                    {
                        "name": "simple-site",
                        "domain": "simple.localhost",
                        "build_output": "public",
                    }
                ),
                encoding="utf-8",
            )

            normalized = self.module.normalize_server_conf(tmp)

        self.assertEqual(normalized["runtime"]["mode"], "static")
        self.assertEqual(normalized["service"]["name"], "simple-site.service")
        self.assertEqual(normalized["nginx"]["tls_hostnames"], ["simple.localhost"])

    def test_normalize_accepts_nested_service_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = pathlib.Path(tmp) / "server.conf"
            conf_path.write_text(
                json.dumps(
                    {
                        "name": "api",
                        "domain": "api.localhost",
                        "build_output": ".",
                        "deploy_hooks": {"build": "npm ci && npm run build"},
                        "runtime": {
                            "mode": "service",
                            "command": "PORT=4001 npm run start",
                            "port": 4001,
                        },
                    }
                ),
                encoding="utf-8",
            )

            normalized = self.module.normalize_server_conf(tmp)

        self.assertEqual(normalized["deploy_hooks"]["build"], "npm ci && npm run build")
        self.assertEqual(normalized["runtime"]["mode"], "service")
        self.assertEqual(normalized["runtime"]["command"], "PORT=4001 npm run start")
        self.assertEqual(normalized["runtime"]["port"], 4001)

    def test_normalize_rejects_legacy_top_level_shorthand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = pathlib.Path(tmp) / "server.conf"
            conf_path.write_text(
                json.dumps(
                    {
                        "name": "legacy",
                        "domain": "legacy.localhost",
                        "build_output": ".",
                        "command": "npm run start",
                        "port": 3000,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(self.module.ValidationError, "legacy top-level key 'command'"):
                self.module.normalize_server_conf(tmp)

    def test_normalize_rejects_removed_infrastructure_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = pathlib.Path(tmp) / "server.conf"
            conf_path.write_text(
                json.dumps(
                    {
                        "name": "legacy",
                        "domain": "legacy.localhost",
                        "build_output": ".",
                        "repo": "git@github.com:example/legacy.git",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(self.module.ValidationError, "unsupported key 'repo'"):
                self.module.normalize_server_conf(tmp)


if __name__ == "__main__":
    unittest.main()
