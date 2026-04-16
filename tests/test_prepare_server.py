#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import call, patch

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))


def load_module():
    path = ROOT_DIR / "scripts" / "prepare_server.py"
    spec = importlib.util.spec_from_file_location("prepare_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PrepareServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_with_status_webapp_runs_status_webapp_installer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            args = argparse.Namespace(
                email="ops@example.com",
                skip_docker=True,
                skip_hardening=True,
                with_ssh_hardening=False,
                with_status_webapp=True,
            )

            with patch.object(self.module, "parse_args", return_value=args):
                with patch.object(self.module, "require_root"):
                    with patch.object(self.module, "repo_root", return_value=root):
                        with patch.object(self.module, "load_env_file", return_value={}):
                            with patch.object(self.module, "setup_automation_units"):
                                with patch.object(self.module, "update_env_file"):
                                    with patch.object(self.module, "run_checked") as run_checked:
                                        self.module.main()

        run_checked.assert_has_calls(
            [
                call(["python3", str(root / "scripts/ensure_server_tools.py"), "--skip-docker"], cwd=root),
                call(["python3", str(root / "scripts/setup_status_webapp.py"), "--root", str(root)], cwd=root),
            ]
        )

    def test_without_status_webapp_skips_status_webapp_installer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            args = argparse.Namespace(
                email="ops@example.com",
                skip_docker=True,
                skip_hardening=True,
                with_ssh_hardening=False,
                with_status_webapp=False,
            )

            with patch.object(self.module, "parse_args", return_value=args):
                with patch.object(self.module, "require_root"):
                    with patch.object(self.module, "repo_root", return_value=root):
                        with patch.object(self.module, "load_env_file", return_value={}):
                            with patch.object(self.module, "setup_automation_units"):
                                with patch.object(self.module, "update_env_file"):
                                    with patch.object(self.module, "run_checked") as run_checked:
                                        self.module.main()

        status_installer = ["python3", str(root / "scripts/setup_status_webapp.py"), "--root", str(root)]
        self.assertNotIn(call(status_installer, cwd=root), run_checked.mock_calls)

    def test_default_hardening_keeps_ssh_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            args = argparse.Namespace(
                email="ops@example.com",
                skip_docker=True,
                skip_hardening=False,
                with_ssh_hardening=False,
                with_status_webapp=False,
            )

            with patch.object(self.module, "parse_args", return_value=args):
                with patch.object(self.module, "require_root"):
                    with patch.object(self.module, "repo_root", return_value=root):
                        with patch.object(self.module, "load_env_file", return_value={}):
                            with patch.object(self.module, "setup_automation_units"):
                                with patch.object(self.module, "update_env_file"):
                                    with patch.object(self.module, "run_checked") as run_checked:
                                        self.module.main()

        self.assertIn(
            call(["python3", str(root / "scripts/harden_server.py")], cwd=root),
            run_checked.mock_calls,
        )
        self.assertNotIn(
            call(["python3", str(root / "scripts/harden_server.py"), "--configure-ssh"], cwd=root),
            run_checked.mock_calls,
        )

    def test_explicit_ssh_hardening_passes_opt_in_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            args = argparse.Namespace(
                email="ops@example.com",
                skip_docker=True,
                skip_hardening=False,
                with_ssh_hardening=True,
                with_status_webapp=False,
            )

            with patch.object(self.module, "parse_args", return_value=args):
                with patch.object(self.module, "require_root"):
                    with patch.object(self.module, "repo_root", return_value=root):
                        with patch.object(self.module, "load_env_file", return_value={}):
                            with patch.object(self.module, "setup_automation_units"):
                                with patch.object(self.module, "update_env_file"):
                                    with patch.object(self.module, "run_checked") as run_checked:
                                        self.module.main()

        self.assertIn(
            call(["python3", str(root / "scripts/harden_server.py"), "--configure-ssh"], cwd=root),
            run_checked.mock_calls,
        )


if __name__ == "__main__":
    unittest.main()
