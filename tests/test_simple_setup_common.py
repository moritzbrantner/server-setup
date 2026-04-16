from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from simple_setup_common import (
    git_command_with_github_auth,
    github_repo_full_name,
    merge_csv_values,
    setup_automation_units,
    update_env_file,
)


class SimpleSetupCommonTests(unittest.TestCase):
    def test_webhook_service_unit_declares_install_target(self) -> None:
        unit_body = (ROOT_DIR / "ops" / "systemd" / "site-webhook-receiver.service").read_text(encoding="utf-8")

        self.assertIn("[Install]", unit_body)
        self.assertIn("WantedBy=multi-user.target", unit_body)

    def test_github_repo_full_name_handles_https_and_ssh(self) -> None:
        self.assertEqual(
            github_repo_full_name("https://github.com/example/app.git"),
            "example/app",
        )
        self.assertEqual(
            github_repo_full_name("git@github.com:example/app.git"),
            "example/app",
        )
        self.assertEqual(github_repo_full_name("https://gitlab.com/example/app.git"), "")

    def test_merge_csv_values_deduplicates(self) -> None:
        self.assertEqual(
            merge_csv_values("alpha,beta", ["beta", "gamma"]),
            "alpha,beta,gamma",
        )

    def test_update_env_file_replaces_and_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "site.env"
            path.write_text("# comment\nFOO=old\n", encoding="utf-8")

            update_env_file(path, {"FOO": "new", "BAR": "added"})

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "# comment\nFOO=new\n\nBAR=added\n",
            )

    def test_setup_automation_units_enables_webhook_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            systemd_dir = root_dir / "ops" / "systemd"
            systemd_dir.mkdir(parents=True)
            (systemd_dir / "site-webhook-receiver.service").write_text(
                "[Unit]\nDescription=Webhook\n\n[Install]\nWantedBy=multi-user.target\n",
                encoding="utf-8",
            )
            (systemd_dir / "site-automation.env.example").write_text(
                "DEFAULT_TLS_EMAIL=\n",
                encoding="utf-8",
            )

            copy_calls: list[tuple[Path | str, Path | str]] = []

            def fake_copy(src: Path | str, dst: Path | str) -> None:
                copy_calls.append((src, dst))

            with patch("simple_setup_common.shutil.copyfile", side_effect=fake_copy):
                with patch("simple_setup_common.run_checked") as run_checked_mock:
                    with patch("simple_setup_common.AUTOMATION_ENV_FILE", root_dir / "site-automation.env"):
                        env_file = setup_automation_units(root_dir, start_webhook=False)

        self.assertEqual(env_file, root_dir / "site-automation.env")
        self.assertIn(
            (systemd_dir / "site-webhook-receiver.service", Path("/etc/systemd/system") / "site-webhook-receiver.service"),
            copy_calls,
        )
        run_checked_mock.assert_any_call(["systemctl", "daemon-reload"])
        run_checked_mock.assert_any_call(["systemctl", "enable", "site-webhook-receiver.service"], allow_fail=True)

    def test_git_command_with_github_auth_uses_active_gh_token(self) -> None:
        expected = base64.b64encode(b"x-access-token:secret-token").decode("ascii")
        with patch.dict("os.environ", {}, clear=True):
            with patch("simple_setup_common.shutil.which", return_value="/usr/bin/gh"):
                with patch(
                    "simple_setup_common.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        ["gh", "auth", "token", "--hostname", "github.com"],
                        0,
                        stdout="secret-token\n",
                        stderr="",
                    ),
                ) as run_mock:
                    cmd = git_command_with_github_auth(
                        "https://github.com/example/app.git",
                        "clone",
                        "https://github.com/example/app.git",
                        "/tmp/app",
                    )

        self.assertEqual(
            cmd,
            [
                "git",
                "-c",
                f"http.https://github.com/.extraheader=AUTHORIZATION: basic {expected}",
                "clone",
                "https://github.com/example/app.git",
                "/tmp/app",
            ],
        )
        run_mock.assert_called_once()

    def test_git_command_with_github_auth_uses_sudo_user_gh_token_when_running_as_root(self) -> None:
        expected = base64.b64encode(b"x-access-token:secret-token").decode("ascii")
        with patch.dict("os.environ", {"USER": "root", "SUDO_USER": "moenarch"}, clear=True):
            with patch(
                "simple_setup_common.shutil.which",
                side_effect=lambda name: f"/usr/bin/{name}" if name in {"gh", "sudo"} else None,
            ):
                with patch(
                    "simple_setup_common.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess(["gh", "auth", "token", "--hostname", "github.com"], 1, stdout="", stderr=""),
                        subprocess.CompletedProcess(
                            ["sudo", "-u", "moenarch", "-H", "gh", "auth", "token", "--hostname", "github.com"],
                            0,
                            stdout="secret-token\n",
                            stderr="",
                        ),
                    ],
                ) as run_mock:
                    cmd = git_command_with_github_auth(
                        "https://github.com/example/app.git",
                        "fetch",
                        "--prune",
                        "origin",
                    )

        self.assertEqual(
            cmd,
            [
                "git",
                "-c",
                f"http.https://github.com/.extraheader=AUTHORIZATION: basic {expected}",
                "fetch",
                "--prune",
                "origin",
            ],
        )
        self.assertEqual(run_mock.call_count, 2)

    def test_git_command_with_github_auth_skips_urls_with_embedded_credentials(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with patch("simple_setup_common.subprocess.run") as run_mock:
                cmd = git_command_with_github_auth(
                    "https://user:token@github.com/example/app.git",
                    "clone",
                    "https://user:token@github.com/example/app.git",
                    "/tmp/app",
                )

        self.assertEqual(
            cmd,
            [
                "git",
                "clone",
                "https://user:token@github.com/example/app.git",
                "/tmp/app",
            ],
        )
        run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
