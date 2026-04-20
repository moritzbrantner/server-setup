#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))


def load_module():
    path = ROOT_DIR / "scripts" / "manage_dns_records.py"
    spec = importlib.util.spec_from_file_location("manage_dns_records", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ManageDnsRecordsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_load_domain_target_reads_deploy_config_dns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = pathlib.Path(tmp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "app",
                            "domain": "app.example.com",
                            "deploy_config": {
                                "name": "app",
                                "domain": "app.example.com",
                                "dns": {
                                    "provider": "porkbun",
                                    "zone": "example.com",
                                },
                            },
                        }
                    ]
                ),
                encoding="utf-8",
            )

            target = self.module.load_domain_target("app", registry_path)

        self.assertEqual(target.provider, "porkbun")
        self.assertEqual(target.zone, "example.com")
        self.assertEqual(target.domain, "app.example.com")

    def test_namecheap_update_preserves_unedited_records(self) -> None:
        target = self.module.DomainTarget("app", "app.example.com", "namecheap", "example.com")
        client = self.module.NamecheapClient.__new__(self.module.NamecheapClient)
        existing_records = [
            {
                "id": "111",
                "type": "A",
                "name": "@",
                "content": "203.0.113.10",
                "ttl": 1800,
                "prio": None,
            },
            {
                "id": "222",
                "type": "MX",
                "name": "@",
                "content": "mail.example.com",
                "ttl": 1800,
                "prio": 10,
            },
        ]
        set_calls: list[list[dict]] = []

        client.list_records = lambda _: existing_records
        client.set_records = lambda _, records: set_calls.append(records) or records

        client.update_record(
            target,
            {
                "id": "111",
                "type": "A",
                "name": "@",
                "content": "203.0.113.11",
                "ttl": 600,
                "prio": None,
            },
        )

        self.assertEqual(set_calls[0][0]["content"], "203.0.113.11")
        self.assertEqual(set_calls[0][1], existing_records[1])

    def test_relative_name_collapses_zone_apex(self) -> None:
        self.assertEqual(self.module.relative_name("example.com", "example.com"), "@")
        self.assertEqual(self.module.relative_name("app.example.com", "example.com"), "app")


if __name__ == "__main__":
    unittest.main()
