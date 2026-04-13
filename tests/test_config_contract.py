#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest


def load_module():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "config_contract.py"
    spec = importlib.util.spec_from_file_location("config_contract", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ConfigContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_normalize_server_conf_accepts_minimal_static_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = pathlib.Path(tmp) / "server.conf"
            conf_path.write_text(
                json.dumps(
                    {
                        "name": "simple-site",
                        "repo": "https://github.com/example/simple-site.git",
                        "branch": "main",
                        "domain": "simple.localhost",
                        "build_output": "public",
                    }
                ),
                encoding="utf-8",
            )

            normalized = self.module.normalize_server_conf(conf_path)

        self.assertEqual(normalized["runtime"]["mode"], "static")
        self.assertEqual(normalized["service"]["name"], "simple-site.service")
        self.assertEqual(normalized["nginx"]["tls_hostnames"], ["simple.localhost"])

    def test_normalize_server_conf_keeps_repo_auth_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = pathlib.Path(tmp) / "server.conf"
            conf_path.write_text(
                json.dumps(
                    {
                        "name": "tlm-deutschland",
                        "repo": "https://github.com/moritzbrantner/tlm-deutschland.git",
                        "branch": "main",
                        "domain": "tlm-deutschland.de",
                        "workdir": "/srv/apps/tlm-deutschland",
                        "build_output": ".",
                        "repo_auth": {
                            "github_token": "${TLM_DEUTSCHLAND_GITHUB_TOKEN}",
                            "github_username": "x-access-token",
                        },
                        "deploy_hooks": {"build": "bun run build"},
                        "runtime": {"mode": "service", "command": "bun run start", "port": 3001},
                        "service": {"name": "tlm-deutschland.service"},
                    }
                ),
                encoding="utf-8",
            )

            normalized = self.module.normalize_server_conf(conf_path)

        self.assertEqual(
            normalized["repo_auth"],
            {
                "github_token": "${TLM_DEUTSCHLAND_GITHUB_TOKEN}",
                "github_username": "x-access-token",
            },
        )

    def test_normalize_server_conf_supports_top_level_service_shorthand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = pathlib.Path(tmp) / "server.conf"
            conf_path.write_text(
                json.dumps(
                    {
                        "name": "marketing-site",
                        "repo": "https://github.com/example/marketing-site.git",
                        "branch": "main",
                        "domain": "example.com",
                        "build_output": "dist",
                        "build": "npm ci && npm run build",
                        "command": "PORT=4003 npm run start",
                        "port": 4003,
                        "user": "www-data",
                        "env_file": "/etc/default/marketing-site",
                        "health_endpoint": "/healthz",
                        "reload_cmd": "sudo systemctl reload nginx",
                        "www_redirect": True,
                    }
                ),
                encoding="utf-8",
            )

            normalized = self.module.normalize_server_conf(conf_path)

        self.assertEqual(normalized["build_cmd"], "npm ci && npm run build")
        self.assertEqual(normalized["post_deploy_cmd"], "sudo systemctl reload nginx")
        self.assertEqual(normalized["runtime"]["mode"], "service")
        self.assertEqual(normalized["runtime"]["command"], "PORT=4003 npm run start")
        self.assertEqual(normalized["runtime"]["port"], 4003)
        self.assertEqual(normalized["runtime"]["user"], "www-data")
        self.assertEqual(normalized["runtime"]["env_file"], "/etc/default/marketing-site")
        self.assertEqual(normalized["runtime"]["health_endpoint"], "/healthz")
        self.assertEqual(normalized["service"]["name"], "marketing-site.service")
        self.assertTrue(normalized["nginx"]["www_redirect"])


if __name__ == "__main__":
    unittest.main()
