from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import unittest
from unittest.mock import patch


def load_module():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_discovery_deploy.py"
    spec = importlib.util.spec_from_file_location("run_discovery_deploy", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class RunDiscoveryDeployTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_merge_site_entries_preserves_manual_onboarded_entries(self) -> None:
        merged = self.module.merge_site_entries(
            [
                {
                    "name": "manual-site",
                    "domain": "manual.test",
                    "managed_via": "onboard",
                },
                {
                    "name": "old-discovered",
                    "domain": "old.test",
                    "source_server_conf": "/srv/apps/old/server.conf",
                },
            ],
            [
                {
                    "name": "discovered-site",
                    "domain": "discovered.test",
                    "source_server_conf": "/srv/apps/discovered/server.conf",
                }
            ],
        )

        self.assertEqual(
            merged,
            [
                {
                    "name": "discovered-site",
                    "domain": "discovered.test",
                    "source_server_conf": "/srv/apps/discovered/server.conf",
                },
                {
                    "name": "manual-site",
                    "domain": "manual.test",
                    "managed_via": "onboard",
                },
            ],
        )

    def test_run_discovery_treats_missing_server_conf_as_empty_discovery(self) -> None:
        with patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["python3", "discover_sites.py"],
                1,
                stdout="",
                stderr="No valid server.conf files found under base glob: /srv/apps/*\n",
            ),
        ):
            entries = self.module.run_discovery(
                pathlib.Path("/opt/server-setup"),
                "/srv/apps/*",
                pathlib.Path("/tmp/discovered.json"),
            )

        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()
