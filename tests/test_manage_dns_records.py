#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from argparse import Namespace
from unittest import mock

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

    def test_namecheap_list_records_accepts_lowercase_host_elements(self) -> None:
        target = self.module.DomainTarget("app", "app.example.com", "namecheap", "example.com")
        client = self.module.NamecheapClient.__new__(self.module.NamecheapClient)
        root = ET.fromstring(
            """
            <ApiResponse>
              <CommandResponse>
                <DomainDNSGetHostsResult Domain="example.com">
                  <host HostId="111" Name="@" Type="A" Address="203.0.113.10" TTL="300" />
                </DomainDNSGetHostsResult>
              </CommandResponse>
            </ApiResponse>
            """
        )
        client.request = lambda *_args, **_kwargs: root

        records = client.list_records(target)

        self.assertEqual(records[0]["id"], "111")
        self.assertEqual(records[0]["name"], "@")
        self.assertEqual(records[0]["content"], "203.0.113.10")

    def test_namecheap_create_dry_run_does_not_call_set_hosts(self) -> None:
        target = self.module.DomainTarget("app", "app.example.com", "namecheap", "example.com")
        client = FakeDnsClient(
            [
                {
                    "id": "111",
                    "type": "A",
                    "name": "@",
                    "content": "203.0.113.10",
                    "ttl": 1800,
                    "prio": None,
                }
            ]
        )

        with mock.patch.object(self.module, "load_domain_target", return_value=target):
            with mock.patch.object(self.module, "provider_client", return_value=client):
                payload = self.module.run(
                    Namespace(
                        action="create",
                        registry="",
                        site="app",
                        type="A",
                        name="app",
                        content="203.0.113.20",
                        ttl="600",
                        prio="",
                        dry_run=True,
                    )
                )

        self.assertEqual(client.set_calls, [])
        self.assertTrue(payload["dryRun"])
        self.assertEqual(payload["warning"], self.module.NAMECHEAP_REPLACE_WARNING)
        self.assertEqual(payload["created"][0]["content"], "203.0.113.20")

    def test_namecheap_update_and_delete_diff_include_before_after(self) -> None:
        target = self.module.DomainTarget("app", "app.example.com", "namecheap", "example.com")
        records = [
            {
                "id": "111",
                "type": "A",
                "name": "app",
                "content": "203.0.113.10",
                "ttl": 1800,
                "prio": None,
            },
            {
                "id": "222",
                "type": "TXT",
                "name": "@",
                "content": "keep",
                "ttl": 1800,
                "prio": None,
            },
        ]

        with mock.patch.object(self.module, "load_domain_target", return_value=target):
            with mock.patch.object(self.module, "provider_client", return_value=FakeDnsClient(records)):
                update_payload = self.module.run(
                    Namespace(
                        action="update",
                        registry="",
                        site="app",
                        id="111",
                        type="A",
                        name="app",
                        content="203.0.113.11",
                        ttl="600",
                        prio="",
                        dry_run=True,
                    )
                )

            with mock.patch.object(self.module, "provider_client", return_value=FakeDnsClient(records)):
                delete_payload = self.module.run(
                    Namespace(
                        action="delete",
                        registry="",
                        site="app",
                        id="222",
                        dry_run=True,
                    )
                )

        self.assertEqual(update_payload["before"][0]["content"], "203.0.113.10")
        self.assertEqual(update_payload["after"][0]["content"], "203.0.113.11")
        self.assertEqual(update_payload["updated"][0]["before"]["content"], "203.0.113.10")
        self.assertEqual(update_payload["updated"][0]["after"]["content"], "203.0.113.11")
        self.assertEqual(delete_payload["before"][1]["id"], "222")
        self.assertEqual(len(delete_payload["after"]), 1)
        self.assertEqual(delete_payload["deleted"][0]["content"], "keep")

    def test_namecheap_mutation_json_includes_warning(self) -> None:
        target = self.module.DomainTarget("app", "app.example.com", "namecheap", "example.com")
        client = FakeDnsClient([])

        with mock.patch.object(self.module, "load_domain_target", return_value=target):
            with mock.patch.object(self.module, "provider_client", return_value=client):
                payload = self.module.run(
                    Namespace(
                        action="create",
                        registry="",
                        site="app",
                        type="A",
                        name="app",
                        content="203.0.113.20",
                        ttl="600",
                        prio="",
                        dry_run=False,
                    )
                )

        self.assertEqual(payload["warning"], self.module.NAMECHEAP_REPLACE_WARNING)
        self.assertEqual(client.set_calls[0][0]["content"], "203.0.113.20")

    def test_porkbun_dry_run_returns_proposed_change_without_api_write(self) -> None:
        target = self.module.DomainTarget("app", "app.example.com", "porkbun", "example.com")
        client = FakeDnsClient([])

        with mock.patch.object(self.module, "load_domain_target", return_value=target):
            with mock.patch.object(self.module, "provider_client", return_value=client):
                payload = self.module.run(
                    Namespace(
                        action="create",
                        registry="",
                        site="app",
                        type="A",
                        name="app",
                        content="203.0.113.20",
                        ttl="600",
                        prio="",
                        dry_run=True,
                    )
                )

        self.assertEqual(client.set_calls, [])
        self.assertEqual(client.create_calls, [])
        self.assertEqual(payload["after"][0]["content"], "203.0.113.20")
        self.assertEqual(payload["records"][0]["content"], "203.0.113.20")

    def test_relative_name_collapses_zone_apex(self) -> None:
        self.assertEqual(self.module.relative_name("example.com", "example.com"), "@")
        self.assertEqual(self.module.relative_name("app.example.com", "example.com"), "app")


class FakeDnsClient:
    def __init__(self, records: list[dict]) -> None:
        self.records = records
        self.set_calls: list[list[dict]] = []
        self.create_calls: list[dict] = []

    def list_records(self, target):
        return list(self.records)

    def set_records(self, target, records):
        self.set_calls.append(records)
        self.records = records
        return records

    def create_record(self, target, record):
        self.create_calls.append(record)
        self.records = [*self.records, record]
        return self.records


if __name__ == "__main__":
    unittest.main()
