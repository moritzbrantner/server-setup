#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    example_apps_dir = os.environ.get("EXAMPLE_APPS_DIR", "/srv/apps")
    skip_example_seed = os.environ.get("SKIP_EXAMPLE_SEED", "0")
    seed_script = Path("/opt/server-setup/scripts/seed_example_repositories.py")
    Path(example_apps_dir).mkdir(parents=True, exist_ok=True)

    if skip_example_seed != "1":
        subprocess.run(["python3", str(seed_script), "--target-dir", example_apps_dir], check=True)
    else:
        print("Skipping example repository seeding because SKIP_EXAMPLE_SEED=1")

    if len(sys.argv) > 1:
        os.execvp(sys.argv[1], sys.argv[1:])
    os.execv("/sbin/init", ["/sbin/init"])


if __name__ == "__main__":
    main()
