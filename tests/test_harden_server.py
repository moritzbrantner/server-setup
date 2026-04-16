#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT_DIR / "scripts" / "harden_server.py"
    spec = importlib.util.spec_from_file_location("harden_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HardenServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_default_run_skips_ssh_configuration(self) -> None:
        with patch.object(self.module, "require_root"), patch.object(self.module.shutil, "which", return_value="/usr/bin/apt-get"):
            with patch.object(self.module, "configure_unattended_upgrades"), patch.object(self.module, "configure_fail2ban"), patch.object(self.module, "configure_ufw"):
                with patch.object(self.module, "ensure_safe_to_disable_password_auth") as ensure_safe, patch.object(
                    self.module, "write_sshd_hardening_config"
                ) as write_sshd, patch.object(self.module, "run_checked"):
                    with patch.object(sys, "argv", ["harden_server.py"]):
                        self.module.main()

        ensure_safe.assert_not_called()
        write_sshd.assert_not_called()

    def test_configure_ssh_flag_enables_ssh_configuration(self) -> None:
        with patch.object(self.module, "require_root"), patch.object(self.module.shutil, "which", return_value="/usr/bin/apt-get"):
            with patch.object(self.module, "configure_unattended_upgrades"), patch.object(self.module, "configure_fail2ban"), patch.object(self.module, "configure_ufw"):
                with patch.object(self.module, "ensure_safe_to_disable_password_auth") as ensure_safe, patch.object(
                    self.module, "write_sshd_hardening_config"
                ) as write_sshd, patch.object(self.module, "run_checked"):
                    with patch.object(sys, "argv", ["harden_server.py", "--configure-ssh"]):
                        self.module.main()

        ensure_safe.assert_called_once()
        write_sshd.assert_called_once()


if __name__ == "__main__":
    unittest.main()
