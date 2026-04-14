#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from collections import deque

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))


def load_module():
    path = ROOT_DIR / "scripts" / "server_conf_contract.py"
    spec = importlib.util.spec_from_file_location("server_conf_contract", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ServerConfContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def _prompt_text(self, answers: list[str]):
        queue = deque(answers)

        def _inner(prompt: str, default: str | None = None, required: bool = False) -> str:
            if not queue:
                raise AssertionError(f"Unexpected prompt: {prompt}")
            value = queue.popleft()
            if value == "__DEFAULT__":
                return default or ""
            return value

        return _inner

    def _prompt_bool(self, answers: list[bool]):
        queue = deque(answers)

        def _inner(prompt: str, default: bool = False) -> bool:
            if not queue:
                raise AssertionError(f"Unexpected boolean prompt: {prompt}")
            return queue.popleft()

        return _inner

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

    def test_create_server_conf_interactively_generates_static_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = self.module.create_server_conf_interactively(
                tmp,
                prompt_text_fn=self._prompt_text(
                    [
                        "__DEFAULT__",
                        "site.example.com",
                        "__DEFAULT__",
                        "__DEFAULT__",
                        "npm ci && npm run build",
                    ]
                ),
                prompt_bool_fn=self._prompt_bool([True]),
                print_fn=lambda _: None,
            )

            conf = json.loads(conf_path.read_text(encoding="utf-8"))

        self.assertEqual(conf["name"], pathlib.Path(tmp).name)
        self.assertEqual(conf["domain"], "site.example.com")
        self.assertEqual(conf["build_output"], "public")
        self.assertEqual(conf["deploy_hooks"]["build"], "npm ci && npm run build")
        self.assertTrue(conf["nginx"]["www_redirect"])
        self.assertEqual(conf["nginx"]["tls_hostnames"], ["site.example.com", "www.site.example.com"])

    def test_create_server_conf_interactively_generates_service_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / ".env.example").write_text("TOKEN=\n", encoding="utf-8")
            conf_path = self.module.create_server_conf_interactively(
                tmp,
                prompt_text_fn=self._prompt_text(
                    [
                        "api",
                        "api.example.com",
                        "service",
                        "__DEFAULT__",
                        "npm ci",
                        "PORT=4100 npm run start",
                        "4100",
                        "/healthz",
                        "__DEFAULT__",
                    ]
                ),
                prompt_bool_fn=self._prompt_bool([False]),
                print_fn=lambda _: None,
            )

            conf = json.loads(conf_path.read_text(encoding="utf-8"))

        self.assertEqual(conf["runtime"]["mode"], "service")
        self.assertEqual(conf["runtime"]["command"], "PORT=4100 npm run start")
        self.assertEqual(conf["runtime"]["port"], 4100)
        self.assertEqual(conf["runtime"]["health_endpoint"], "/healthz")
        self.assertEqual(conf["runtime"]["env_file"], str((pathlib.Path(tmp) / ".env").resolve()))


if __name__ == "__main__":
    unittest.main()
