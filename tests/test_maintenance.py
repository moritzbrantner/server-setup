from __future__ import annotations

import json
import unittest
from pathlib import Path

from server_setup.cli import run
from server_setup.maintenance import SnapshotError, parse_snapshot
from server_setup.modules.host import BASE_PACKAGES
from tests.fake_system import FakeSystem
from tests.test_cli import SAFE_CONFIG


class MaintenanceTests(unittest.TestCase):
    def _config(self, system: FakeSystem, text: str = SAFE_CONFIG) -> Path:
        path = Path("/tmp/test-server-setup-maintenance.toml")
        system.files[str(path)] = text
        return path

    def _converged(self) -> tuple[FakeSystem, Path]:
        system = FakeSystem()
        path = self._config(system)
        self.assertEqual(run(["apply", "--config", str(path), "--yes"], system=system), 0)
        for package in BASE_PACKAGES:
            system.package_versions[package] = "1.0"
        return system, path

    def test_drift_is_nonzero_before_apply_and_clean_after_convergence(self) -> None:
        system = FakeSystem()
        path = self._config(system)

        self.assertEqual(run(["drift", "--config", str(path)], system=system), 1)
        self.assertEqual(run(["apply", "--config", str(path), "--yes"], system=system), 0)
        self.assertEqual(run(["drift", "--config", str(path), "--json"], system=system), 0)

    def test_snapshot_captures_exact_managed_package_versions_with_private_mode(self) -> None:
        system, path = self._converged()
        snapshot_path = Path("/tmp/server-setup-snapshot.json")

        self.assertEqual(
            run(["snapshot", "--config", str(path), "--output", str(snapshot_path)], system=system),
            0,
        )

        snapshot = parse_snapshot(system.files[str(snapshot_path)])
        self.assertEqual(snapshot.packages, {package: "1.0" for package in BASE_PACKAGES})
        self.assertEqual(snapshot.config_text, SAFE_CONFIG)
        self.assertEqual(system.modes[str(snapshot_path)], 0o600)

    def test_upgrade_refuses_unreconciled_drift_before_writing_snapshot(self) -> None:
        system = FakeSystem()
        path = self._config(system)
        snapshot_path = Path("/tmp/server-setup-upgrade.json")

        self.assertEqual(
            run(["upgrade", "--config", str(path), "--snapshot", str(snapshot_path), "--yes"], system=system),
            2,
        )
        self.assertNotIn(str(snapshot_path), system.files)
        self.assertFalse(any("--only-upgrade" in call for call in system.calls))

    def test_upgrade_updates_only_packages_declared_managed_by_current_config(self) -> None:
        system, path = self._converged()
        snapshot_path = Path("/tmp/server-setup-upgrade.json")
        for package in BASE_PACKAGES:
            system.available_versions[package] = "2.0"

        # UFW is globally known to server-setup, but this config explicitly leaves
        # firewall management disabled. A leftover/manual UFW installation must not
        # therefore be pulled into this maintenance transaction.
        system.installed.add("ufw")
        system.package_versions["ufw"] = "1.0"
        system.available_versions["ufw"] = "2.0"

        system.installed.add("unowned-package")
        system.package_versions["unowned-package"] = "9.9"
        system.available_versions["unowned-package"] = "10.0"

        self.assertEqual(
            run(["upgrade", "--config", str(path), "--snapshot", str(snapshot_path), "--yes"], system=system),
            0,
        )

        snapshot = parse_snapshot(system.files[str(snapshot_path)])
        self.assertEqual(snapshot.packages, {package: "1.0" for package in BASE_PACKAGES})
        self.assertTrue(all(system.package_versions[package] == "2.0" for package in BASE_PACKAGES))
        self.assertEqual(system.package_versions["ufw"], "1.0")
        self.assertEqual(system.package_versions["unowned-package"], "9.9")
        upgrade_calls = [call for call in system.calls if "--only-upgrade" in call]
        self.assertEqual(len(upgrade_calls), 1)
        self.assertNotIn("ufw", upgrade_calls[0])
        self.assertNotIn("unowned-package", upgrade_calls[0])

    def test_rollback_restores_exact_snapshot_versions(self) -> None:
        system, path = self._converged()
        snapshot_path = Path("/tmp/server-setup-upgrade.json")
        for package in BASE_PACKAGES:
            system.available_versions[package] = "2.0"

        self.assertEqual(
            run(["upgrade", "--config", str(path), "--snapshot", str(snapshot_path), "--yes"], system=system),
            0,
        )
        self.assertEqual(
            run(["rollback", "--config", str(path), "--snapshot", str(snapshot_path), "--yes"], system=system),
            0,
        )

        self.assertTrue(all(system.package_versions[package] == "1.0" for package in BASE_PACKAGES))
        downgrade_calls = [call for call in system.calls if "--allow-downgrades" in call]
        self.assertEqual(len(downgrade_calls), 1)

    def test_rollback_refuses_when_config_changed_after_snapshot(self) -> None:
        system, path = self._converged()
        snapshot_path = Path("/tmp/server-setup-upgrade.json")
        for package in BASE_PACKAGES:
            system.available_versions[package] = "2.0"
        self.assertEqual(
            run(["upgrade", "--config", str(path), "--snapshot", str(snapshot_path), "--yes"], system=system),
            0,
        )
        system.files[str(path)] = SAFE_CONFIG.replace('timezone = "UTC"', 'timezone = "Europe/Berlin"')

        self.assertEqual(
            run(["rollback", "--config", str(path), "--snapshot", str(snapshot_path), "--yes"], system=system),
            2,
        )
        self.assertTrue(all(system.package_versions[package] == "2.0" for package in BASE_PACKAGES))

    def test_snapshot_parser_rejects_package_not_enabled_by_embedded_config(self) -> None:
        payload = {
            "schema": 1,
            "config": SAFE_CONFIG,
            "packages": {"ufw": "1.0"},
            "dokploy_version": None,
        }

        with self.assertRaises(SnapshotError):
            parse_snapshot(json.dumps(payload))

    def test_snapshot_parser_rejects_unknown_package_targets(self) -> None:
        payload = {
            "schema": 1,
            "config": SAFE_CONFIG,
            "packages": {"bash": "1.0"},
            "dokploy_version": None,
        }

        with self.assertRaises(SnapshotError):
            parse_snapshot(json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
