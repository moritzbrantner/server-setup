#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def load_module(filename: str):
    path = SCRIPTS_DIR / filename
    module_name = f"test_{path.stem.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class InteractiveCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module("interactive_cli.py")

    def test_prompt_text_returns_default_on_empty_input(self) -> None:
        with patch("builtins.input", return_value=""):
            value = self.module.prompt_text("Email", default="ops@example.com")

        self.assertEqual(value, "ops@example.com")

    def test_prompt_bool_retries_until_valid_answer(self) -> None:
        with patch("builtins.input", side_effect=["maybe", "yes"]):
            value = self.module.prompt_bool("Continue")

        self.assertTrue(value)

    def test_ensure_interactive_rejects_missing_required_args_without_tty(self) -> None:
        args = argparse.Namespace(interactive=False, email="")

        with patch.object(self.module.sys.stdin, "isatty", return_value=False):
            with self.assertRaises(SystemExit) as context:
                self.module.ensure_interactive(args, ["email"])

        self.assertIn("--email", str(context.exception))

    def test_maybe_sudo_prefixes_command_for_non_root_users(self) -> None:
        with patch.object(self.module.os, "geteuid", return_value=1000):
            cmd = self.module.maybe_sudo(["python3", "scripts/prepare_server.py"])

        self.assertEqual(cmd, ["sudo", "python3", "scripts/prepare_server.py"])


class MigrateRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module("migrate_registry.py")

    def test_main_is_idempotent_when_rewriting_server_conf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            checkout = root / "apps" / "demo"
            checkout.mkdir(parents=True)
            legacy_path = root / "sites.json"
            registry_path = root / "registry.json"
            legacy_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "demo",
                            "domain": "demo.example.com",
                            "repo": "https://github.com/example/demo.git",
                            "branch": "main",
                            "workdir": str(checkout),
                            "build_output": "public",
                            "command": "python3 app.py",
                            "port": 3000,
                            "service_name": "demo.service",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                input=str(legacy_path),
                output=str(registry_path),
                rewrite_server_conf=True,
            )

            with patch.object(self.module, "parse_args", return_value=args):
                self.module.main()
            first_registry = json.loads(registry_path.read_text(encoding="utf-8"))
            first_server_conf = (checkout / "server.conf").read_text(encoding="utf-8")

            with patch.object(self.module, "parse_args", return_value=args):
                self.module.main()
            second_registry = json.loads(registry_path.read_text(encoding="utf-8"))
            second_server_conf = (checkout / "server.conf").read_text(encoding="utf-8")

        self.assertEqual(first_registry, second_registry)
        self.assertEqual(first_server_conf, second_server_conf)
        self.assertEqual(first_registry[0]["name"], "demo")
        self.assertEqual(first_registry[0]["service_name"], "demo.service")


class RunSelfChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module("run_self_checks.py")

    def test_main_executes_top_level_test_runner(self) -> None:
        with patch.object(self.module.subprocess, "run") as run:
            self.module.main()

        run.assert_called_once_with(
            ["bash", str(ROOT_DIR / "tests/run-tests.sh")],
            check=True,
            cwd=ROOT_DIR,
        )


class EnsureServerToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module("ensure_server_tools.py")

    def test_main_installs_nginx_in_baseline_package_set(self) -> None:
        args = argparse.Namespace(skip_docker=True)

        with patch.object(self.module, "parse_args", return_value=args):
            with patch.object(self.module, "require_root"):
                with patch.object(
                    self.module.shutil,
                    "which",
                    side_effect=lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
                ):
                    with patch.object(self.module, "run_checked"):
                        with patch.object(self.module, "install_pkgs") as install_pkgs:
                            with patch.object(self.module, "install_or_update_bun"):
                                with patch.object(self.module, "install_or_update_gh"):
                                    with patch.object(self.module, "ensure_postgres_enabled"):
                                        self.module.main()

        baseline_packages = install_pkgs.call_args_list[0].args[0]
        self.assertIn("nginx", baseline_packages)


class SandboxEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module("sandbox_entrypoint.py")

    def test_main_seeds_examples_and_execs_custom_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"EXAMPLE_APPS_DIR": tmp}, clear=False):
                with patch.object(self.module.subprocess, "run") as run:
                    with patch.object(self.module.os, "execvp", side_effect=SystemExit(0)) as execvp:
                        with patch.object(self.module.os, "execv") as execv:
                            with patch.object(self.module.sys, "argv", ["sandbox_entrypoint.py", "bash", "-lc", "echo ok"]):
                                with self.assertRaises(SystemExit):
                                    self.module.main()

        run.assert_called_once_with(
            ["python3", "/opt/server-setup/scripts/seed_example_repositories.py", "--target-dir", tmp],
            check=True,
        )
        execvp.assert_called_once_with("bash", ["bash", "-lc", "echo ok"])
        execv.assert_not_called()

    def test_main_skips_seed_and_starts_init_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"EXAMPLE_APPS_DIR": tmp, "SKIP_EXAMPLE_SEED": "1"}, clear=False):
                with patch.object(self.module.subprocess, "run") as run:
                    with patch.object(self.module.os, "execv") as execv:
                        with patch.object(self.module.sys, "argv", ["sandbox_entrypoint.py"]):
                            self.module.main()

        run.assert_not_called()
        execv.assert_called_once_with("/sbin/init", ["/sbin/init"])


class StartExampleAppsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module("start_example_apps.py")

    def test_main_skips_deploy_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"EXAMPLE_APPS_DIR": tmp, "SKIP_EXAMPLE_DEPLOY": "1"}, clear=False):
                with patch.object(self.module.subprocess, "run") as run:
                    self.module.main()

        run.assert_not_called()

    def test_main_repeated_runs_deploy_sorted_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            apps_dir = pathlib.Path(tmp)
            for name in ("beta-site", "alpha-site"):
                repo_dir = apps_dir / name
                repo_dir.mkdir()
                (repo_dir / "server.conf").write_text("{}", encoding="utf-8")
            (apps_dir / "notes").mkdir()

            calls: list[dict[str, object]] = []

            def fake_run(cmd, **kwargs):
                calls.append({"cmd": cmd, "cwd": kwargs.get("cwd")})

                class Result:
                    returncode = 0

                return Result()

            env = {
                "EXAMPLE_APPS_DIR": str(apps_dir),
                "DEFAULT_TLS_EMAIL": "ops@example.com",
                "POSTGRES_HOST": "postgres.local",
                "POSTGRES_PORT": "5433",
                "POSTGRES_DB": "server_setup",
                "POSTGRES_USER": "server_setup",
            }

            with patch.dict(os.environ, env, clear=False):
                with patch.object(self.module.subprocess, "run", side_effect=fake_run):
                    self.module.main()
                    self.module.main()

        deploy_cmds = [call["cmd"] for call in calls if call["cmd"][0] == "python3"]
        readiness_cmds = [call["cmd"] for call in calls if call["cmd"][0] == "pg_isready"]
        expected_alpha = [
            "python3",
            "/opt/server-setup/scripts/deploy_repo.py",
            "--repo-url",
            str(apps_dir / "alpha-site"),
            "--dest",
            str(apps_dir / "alpha-site"),
            "--email",
            "ops@example.com",
            "--skip-github-hook",
        ]
        expected_beta = [
            "python3",
            "/opt/server-setup/scripts/deploy_repo.py",
            "--repo-url",
            str(apps_dir / "beta-site"),
            "--dest",
            str(apps_dir / "beta-site"),
            "--email",
            "ops@example.com",
            "--skip-github-hook",
        ]

        self.assertEqual(len(readiness_cmds), 2)
        self.assertEqual(
            deploy_cmds,
            [expected_alpha, expected_beta, expected_alpha, expected_beta],
        )


class ShutdownServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module("shutdown_server.py")

    def test_purge_dry_run_invokes_reset_script(self) -> None:
        args = argparse.Namespace(config="/tmp/registry.json", dry_run=True, purge=True)

        with patch.object(self.module, "parse_args", return_value=args):
            with patch.object(self.module, "repo_root", return_value=ROOT_DIR):
                with patch.object(self.module, "run_checked") as run_checked:
                    self.module.main()

        run_checked.assert_called_once_with(
            [
                "python3",
                str(ROOT_DIR / "scripts/reset_server_setup.py"),
                "--config",
                "/tmp/registry.json",
                "--yes",
                "--dry-run",
            ],
            cwd=ROOT_DIR,
        )

    def test_non_purge_invokes_shutdown_websites(self) -> None:
        args = argparse.Namespace(config="/tmp/registry.json", dry_run=False, purge=False)

        with patch.object(self.module, "parse_args", return_value=args):
            with patch.object(self.module, "repo_root", return_value=ROOT_DIR):
                with patch.object(self.module, "run_checked") as run_checked:
                    self.module.main()

        run_checked.assert_called_once_with(
            [
                "python3",
                str(ROOT_DIR / "scripts/shutdown_websites.py"),
                "--config",
                "/tmp/registry.json",
            ],
            cwd=ROOT_DIR,
        )


if __name__ == "__main__":
    unittest.main()
