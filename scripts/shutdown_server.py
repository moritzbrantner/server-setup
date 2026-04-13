#!/usr/bin/env python3
from __future__ import annotations

import argparse

from registry_contract import DEFAULT_REGISTRY_PATH
from simple_setup_common import repo_root, run_checked


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stop the stack, or purge generated config as well.")
    parser.add_argument("--config", default=str(DEFAULT_REGISTRY_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--purge", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = repo_root()

    if args.purge:
        cmd = [
            "python3",
            str(root / "scripts/reset_server_setup.py"),
            "--config",
            args.config,
            "--yes",
        ]
    else:
        cmd = [
            "python3",
            str(root / "scripts/shutdown_websites.py"),
            "--config",
            args.config,
        ]

    if args.dry_run:
        cmd.append("--dry-run")

    run_checked(cmd, cwd=root)


if __name__ == "__main__":
    main()
