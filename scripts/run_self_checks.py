#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> None:
    root_dir = Path(__file__).resolve().parent.parent
    subprocess.run(["bash", str(root_dir / "tests/run-tests.sh")], check=True, cwd=root_dir)


if __name__ == "__main__":
    main()
