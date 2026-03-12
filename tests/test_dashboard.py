#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest


def load_module():
    path = pathlib.Path(__file__).resolve().parents[1] / "monitor" / "dashboard.py"
    spec = importlib.util.spec_from_file_location("dashboard", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_load_sites_accepts_manual_monitor_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "websites.json"
            path.write_text(json.dumps([{"name": "Main", "url": "https://example.com"}]), encoding="utf-8")
            sites = self.module.load_sites(path)
            self.assertEqual("https://example.com", sites[0]["url"])

    def test_load_sites_derives_from_deploy_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "sites.json"
            state_dir = pathlib.Path(tmpdir) / "state"
            state_dir.mkdir()
            (state_dir / "marketing-site.json").write_text(
                json.dumps(
                    {
                        "site": "marketing-site",
                        "last_successful_release": "/srv/releases/123",
                        "last_deploy_timestamp": "2026-03-11T10:00:00Z",
                        "last_health_check": {"status": "passing"},
                        "current_release": "/srv/releases/123",
                    }
                ),
                encoding="utf-8",
            )
            path.write_text(
                json.dumps(
                    [
                        {
                            "name": "marketing-site",
                            "site_url": "https://example.com",
                            "runtime": {"mode": "service"},
                        }
                    ]
                ),
                encoding="utf-8",
            )
            sites = self.module.load_sites(path, state_dir=state_dir)
            self.assertEqual("https://example.com", sites[0]["url"])
            self.assertEqual("/srv/releases/123", sites[0]["deploy"]["current_release"])


if __name__ == "__main__":
    unittest.main()
