#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def default_base_glob() -> str:
    try:
        if Path("/root/apps").is_dir():
            return "/root/apps/*"
    except PermissionError:
        pass
    return "/srv/apps/*"


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover sites from server.conf files.")
    parser.add_argument("--base-glob", default=default_base_glob())
    parser.add_argument("--output", default="deploy/sites.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cmd = [
        "python3",
        str(Path(__file__).resolve().parent / "config_contract.py"),
        "discover",
        "--base-glob",
        args.base_glob,
        "--output",
        args.output,
    ]
    if args.dry_run:
        cmd.append("--dry-run")

    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
