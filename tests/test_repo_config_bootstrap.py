#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest
from collections import deque

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT_DIR / "scripts" / "repo_config_bootstrap.py"
    spec = importlib.util.spec_from_file_location("repo_config_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class RepoConfigBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def _prompt_text(self, answers: list[str]):
        queue = deque(answers)

        def _inner(prompt: str, default: str | None = None, required: bool = False) -> str:
            if not queue:
                raise AssertionError(f"Unexpected prompt: {prompt}")
            value = queue.popleft()
            if value == "__DEFAULT__":
                return default or ""
            return value

        return _inner

    def test_find_example_dotfiles_recurses_and_ignores_build_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            (checkout / ".env.example").write_text("FOO=\n", encoding="utf-8")
            (checkout / "config").mkdir()
            (checkout / "config" / ".runtime.example").write_text("BAR=\n", encoding="utf-8")
            (checkout / ".git").mkdir()
            (checkout / ".git" / ".ignored.example").write_text("NOPE=\n", encoding="utf-8")
            (checkout / "node_modules").mkdir()
            (checkout / "node_modules" / ".also-ignored.example").write_text("NOPE=\n", encoding="utf-8")

            paths = self.module.find_example_dotfiles(checkout)

        self.assertEqual(
            [path.relative_to(checkout).as_posix() for path in paths],
            [".env.example", "config/.runtime.example"],
        )

    def test_create_dotfile_from_example_prompts_for_each_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            example = checkout / ".env.example"
            example.write_text("# demo\nFOO=\nBAR=3000\n", encoding="utf-8")

            target = self.module.create_dotfile_from_example(
                example,
                prompt_text_fn=self._prompt_text(["secret", "__DEFAULT__"]),
                print_fn=lambda _: None,
            )

            body = target.read_text(encoding="utf-8")

        self.assertEqual(target.name, ".env")
        self.assertEqual(body, "# demo\nFOO=secret\nBAR=3000\n")

    def test_ensure_example_dotfiles_skips_existing_targets_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            example = checkout / ".env.example"
            target = checkout / ".env"
            example.write_text("FOO=\n", encoding="utf-8")
            target.write_text("FOO=existing\n", encoding="utf-8")

            created = self.module.ensure_example_dotfiles(
                checkout,
                prompt_text_fn=self._prompt_text([]),
                is_interactive=True,
                print_fn=lambda _: None,
            )

        self.assertEqual(created, [])

    def test_ensure_example_dotfiles_requires_interactive_terminal_for_pending_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            (checkout / ".env.example").write_text("FOO=\n", encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "interactive terminal"):
                self.module.ensure_example_dotfiles(
                    checkout,
                    prompt_text_fn=self._prompt_text([]),
                    is_interactive=False,
                    print_fn=lambda _: None,
                )

    def test_suggested_runtime_env_file_prefers_root_env_example(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = pathlib.Path(tmp)
            (checkout / ".env.example").write_text("FOO=\n", encoding="utf-8")

            suggested = self.module.suggested_runtime_env_file(checkout)

        self.assertEqual(suggested, str((checkout / ".env").resolve()))


if __name__ == "__main__":
    unittest.main()
