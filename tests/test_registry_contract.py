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
    path = ROOT_DIR / "scripts" / "registry_contract.py"
    spec = importlib.util.spec_from_file_location("registry_contract", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RegistryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def _service_conf(
        self,
        *,
        name: str,
        domain: str,
        service_name: str,
        port: int,
    ) -> dict:
        return {
            "name": name,
            "domain": domain,
            "service": {"name": service_name},
            "runtime": {"mode": "service", "port": port},
            "source_server_conf": f"/srv/apps/{name}/server.conf",
        }

    def test_upsert_registry_entry_writes_expected_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "registry.json"
            entry = self.module.upsert_registry_entry(
                path,
                "https://github.com/example/app.git",
                "main",
                "/srv/apps/app",
                {
                    "name": "app",
                    "domain": "app.example.com",
                    "service": {"name": "app.service"},
                    "runtime": {"mode": "static"},
                    "source_server_conf": "/srv/apps/app/server.conf",
                },
            )

            payload = self.module.load_registry(path)

        self.assertEqual(entry["managed_by"], "deploy-repo")
        self.assertEqual(payload[0]["webhook_repo"], "example/app")
        self.assertEqual(payload[0]["checkout_path"], "/srv/apps/app")

    def test_find_registry_entry_by_push_matches_repo_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "registry.json"
            self.module.save_registry(
                [
                    {
                        "name": "app",
                        "repo_url": "https://github.com/example/app.git",
                        "branch": "main",
                        "checkout_path": "/srv/apps/app",
                        "server_conf_path": "/srv/apps/app/server.conf",
                        "service_name": "app.service",
                        "domain": "app.example.com",
                        "webhook_repo": "example/app",
                        "managed_by": "deploy-repo",
                        "deploy_config": {"runtime": {"mode": "static"}, "service": {"name": "app.service"}},
                    }
                ],
                path,
            )

            entry = self.module.find_registry_entry_by_push("example/app", "main", path)

        self.assertIsNotNone(entry)
        self.assertEqual(entry["name"], "app")

    def test_upsert_registry_entry_rejects_duplicate_domain_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "registry.json"
            self.module.upsert_registry_entry(
                path,
                "https://github.com/example/alpha.git",
                "main",
                "/srv/apps/alpha",
                self._service_conf(
                    name="alpha",
                    domain="alpha.example.com",
                    service_name="alpha.service",
                    port=3000,
                ),
            )

            with self.assertRaisesRegex(self.module.RegistryError, "existing site 'alpha'"):
                self.module.upsert_registry_entry(
                    path,
                    "https://github.com/example/beta.git",
                    "main",
                    "/srv/apps/beta",
                    self._service_conf(
                        name="beta",
                        domain="ALPHA.EXAMPLE.COM",
                        service_name="beta.service",
                        port=3001,
                    ),
                )

    def test_upsert_registry_entry_rejects_duplicate_service_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "registry.json"
            self.module.upsert_registry_entry(
                path,
                "https://github.com/example/alpha.git",
                "main",
                "/srv/apps/alpha",
                self._service_conf(
                    name="alpha",
                    domain="alpha.example.com",
                    service_name="shared.service",
                    port=3000,
                ),
            )

            with self.assertRaisesRegex(self.module.RegistryError, "service_name 'shared.service'"):
                self.module.upsert_registry_entry(
                    path,
                    "https://github.com/example/beta.git",
                    "main",
                    "/srv/apps/beta",
                    self._service_conf(
                        name="beta",
                        domain="beta.example.com",
                        service_name="shared.service",
                        port=3001,
                    ),
                )

    def test_upsert_registry_entry_rejects_duplicate_runtime_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "registry.json"
            self.module.upsert_registry_entry(
                path,
                "https://github.com/example/alpha.git",
                "main",
                "/srv/apps/alpha",
                self._service_conf(
                    name="alpha",
                    domain="alpha.example.com",
                    service_name="alpha.service",
                    port=3000,
                ),
            )

            with self.assertRaisesRegex(self.module.RegistryError, "runtime.port '3000'"):
                self.module.upsert_registry_entry(
                    path,
                    "https://github.com/example/beta.git",
                    "main",
                    "/srv/apps/beta",
                    self._service_conf(
                        name="beta",
                        domain="beta.example.com",
                        service_name="beta.service",
                        port=3000,
                    ),
                )

    def test_upsert_registry_entry_allows_replacing_same_named_site(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "registry.json"
            self.module.upsert_registry_entry(
                path,
                "https://github.com/example/alpha.git",
                "main",
                "/srv/apps/alpha-old",
                self._service_conf(
                    name="alpha",
                    domain="alpha.example.com",
                    service_name="alpha.service",
                    port=3000,
                ),
            )

            entry = self.module.upsert_registry_entry(
                path,
                "https://github.com/example/alpha.git",
                "main",
                "/srv/apps/alpha",
                self._service_conf(
                    name="alpha",
                    domain="alpha.example.com",
                    service_name="alpha.service",
                    port=3000,
                ),
            )
            payload = self.module.load_registry(path)

        self.assertEqual(entry["checkout_path"], "/srv/apps/alpha")
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["checkout_path"], "/srv/apps/alpha")
