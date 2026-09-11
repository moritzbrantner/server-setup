from __future__ import annotations

import unittest
from pathlib import Path


class SetupBootstrapTests(unittest.TestCase):
    def test_supported_os_is_checked_before_apt_mutation(self) -> None:
        setup = Path("setup.sh").read_text(encoding="utf-8")

        os_check = setup.index('case "${ID:-}:${VERSION_ID:-}" in')
        apt_update = setup.index("apt-get update -y")

        self.assertLess(os_check, apt_update)
        self.assertIn("debian:12|ubuntu:24.04", setup[os_check:apt_update])
        self.assertIn("unsupported host", setup[os_check:apt_update])

    def test_bootstrap_installs_monitoring_compose_bundle_with_python_core(self) -> None:
        setup = Path("setup.sh").read_text(encoding="utf-8")

        self.assertIn('cp -a "$ROOT_DIR/server_setup" "$INSTALL_DIR/server_setup"', setup)
        self.assertIn('cp "$ROOT_DIR/services/compose.yml" "$INSTALL_DIR/services/compose.yml"', setup)
        self.assertNotIn('cp -a "$ROOT_DIR/services" "$INSTALL_DIR/services"', setup)


if __name__ == "__main__":
    unittest.main()
