#!/usr/bin/env python3
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Callable


IGNORED_DIR_NAMES = {".git", "node_modules", ".next"}
ASSIGNMENT_RE = re.compile(r"^(?P<indent>\s*)(?P<export>export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")


def find_example_dotfiles(checkout_path: str | Path) -> list[Path]:
    checkout = Path(checkout_path)
    matches: list[Path] = []
    for path in sorted(checkout.rglob(".*.example")):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIR_NAMES for part in path.relative_to(checkout).parts[:-1]):
            continue
        matches.append(path)
    return matches


def target_dotfile_path(example_path: str | Path) -> Path:
    path = Path(example_path)
    return path.with_name(path.name.removesuffix(".example"))


def suggested_runtime_env_file(checkout_path: str | Path) -> str:
    checkout = Path(checkout_path)
    for example_path in find_example_dotfiles(checkout):
        target_path = target_dotfile_path(example_path)
        if target_path.parent == checkout and target_path.name == ".env":
            return str(target_path.resolve())
    return ""


def _parse_assignment(line: str) -> tuple[str, str, str, str] | None:
    match = ASSIGNMENT_RE.match(line)
    if not match:
        return None
    prefix = f"{match.group('indent')}{match.group('export') or ''}"
    return prefix, match.group("key"), match.group("value"), line


def _render_value(key: str, example_value: str, prompt_text_fn: Callable[..., str], target_path: Path) -> str:
    default_value = example_value.strip() or None
    return prompt_text_fn(
        f"{target_path.name} -> {key}",
        default=default_value,
        required=True,
    )


def create_dotfile_from_example(
    example_path: str | Path,
    *,
    prompt_text_fn: Callable[..., str],
    print_fn: Callable[[str], None] = print,
) -> Path:
    example = Path(example_path)
    target = target_dotfile_path(example)
    if target.exists():
        return target

    print_fn(f"Config template found: {example}")
    lines = example.read_text(encoding="utf-8").splitlines()
    rendered: list[str] = []
    for line in lines:
        parsed = _parse_assignment(line)
        if not parsed:
            rendered.append(line)
            continue
        prefix, key, example_value, _ = parsed
        value = _render_value(key, example_value, prompt_text_fn, target)
        rendered.append(f"{prefix}{key}={value}")

    body = "\n".join(rendered).rstrip() + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = tempfile.NamedTemporaryFile("w", delete=False, dir=str(target.parent), encoding="utf-8")
    try:
        tmp_file.write(body)
        tmp_file.close()
        Path(tmp_file.name).replace(target)
    finally:
        Path(tmp_file.name).unlink(missing_ok=True)
    return target


def ensure_example_dotfiles(
    checkout_path: str | Path,
    *,
    prompt_text_fn: Callable[..., str],
    is_interactive: bool,
    print_fn: Callable[[str], None] = print,
) -> list[Path]:
    checkout = Path(checkout_path)
    created: list[Path] = []
    pending = [path for path in find_example_dotfiles(checkout) if not target_dotfile_path(path).exists()]
    if pending and not is_interactive:
        details = ", ".join(str(path) for path in pending)
        raise SystemExit(
            "Missing repository config files derived from example templates. "
            f"Run deploy_repo.py in an interactive terminal to complete them: {details}"
        )
    for example_path in pending:
        created.append(
            create_dotfile_from_example(
                example_path,
                prompt_text_fn=prompt_text_fn,
                print_fn=print_fn,
            )
        )
    return created
