#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))


def load_module():
    path = ROOT_DIR / "scripts" / "manage_porkbun_dns.py"
    spec = importlib.util.spec_from_file_location("manage_porkbun_dns", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ManagePorkbunDnsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_normalize_record_name_accepts_root_subdomain_and_fqdn(self) -> None:
        self.assertEqual(self.module.normalize_record_name("example.com", ""), "example.com")
        self.assertEqual(self.module.normalize_record_name("example.com", "@"), "example.com")
        self.assertEqual(self.module.normalize_record_name("example.com", "www"), "www.example.com")
        self.assertEqual(self.module.normalize_record_name("example.com", "*.example.com"), "*.example.com")

    def test_porkbun_post_sends_credentials_and_payload(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse({"status": "SUCCESS", "records": []})

        credentials = self.module.PorkbunCredentials(api_key="pk_test", secret_api_key="sk_test")
        with patch.object(self.module.urllib.request, "urlopen", side_effect=fake_urlopen):
            payload = self.module.porkbun_post(
                "https://api.example.test/root",
                ["dns", "retrieve", "example.com"],
                credentials,
                {"extra": "value"},
            )

        self.assertEqual(payload["status"], "SUCCESS")
        self.assertEqual(captured["url"], "https://api.example.test/root/dns/retrieve/example.com")
        self.assertEqual(
            captured["body"],
            {
                "apikey": "pk_test",
                "secretapikey": "sk_test",
                "extra": "value",
            },
        )

    def test_main_lists_records_as_json(self) -> None:
        stdout = io.StringIO()
        credentials = self.module.PorkbunCredentials(api_key="pk_test", secret_api_key="sk_test")

        with patch.object(self.module, "read_credentials", return_value=credentials):
            with patch.object(
                self.module,
                "list_records",
                return_value=[
                    {
                        "id": "123",
                        "name": "www.example.com",
                        "type": "A",
                        "content": "1.2.3.4",
                        "ttl": "600",
                        "prio": "0",
                    }
                ],
            ):
                with patch_argv(["manage_porkbun_dns.py", "--json", "list", "example.com"]):
                    with contextlib.redirect_stdout(stdout):
                        self.module.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["domain"], "example.com")
        self.assertEqual(payload["records"][0]["id"], "123")


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
