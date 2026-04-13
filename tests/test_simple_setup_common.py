from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from simple_setup_common import github_repo_full_name, merge_csv_values, update_env_file


class SimpleSetupCommonTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
