#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


def main() -> None:
    root_dir = Path("/opt/server-setup")
    apps_dir = Path(os.environ.get("EXAMPLE_APPS_DIR", "/srv/apps"))
    tls_email = os.environ.get("DEFAULT_TLS_EMAIL", "ops@example.com")
    postgres_host = os.environ.get("POSTGRES_HOST", "test-db")
    postgres_port = os.environ.get("POSTGRES_PORT", "5432")
    postgres_db = os.environ.get("POSTGRES_DB", "server_setup")
    postgres_user = os.environ.get("POSTGRES_USER", "server_setup")
    if os.environ.get("SKIP_EXAMPLE_DEPLOY", "0") == "1":
        print("Skipping example app deployment because SKIP_EXAMPLE_DEPLOY=1")
        return

    for _ in range(30):
        result = subprocess.run(
            ["pg_isready", "-h", postgres_host, "-p", postgres_port, "-U", postgres_user, "-d", postgres_db],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            break
        time.sleep(2)

    for repo_dir in sorted(path for path in apps_dir.iterdir() if (path / "server.conf").is_file()):
        subprocess.run(
            [
                "python3",
                str(root_dir / "scripts/deploy_repo.py"),
                "--repo-url",
                str(repo_dir),
                "--dest",
                str(repo_dir),
                "--email",
                tls_email,
                "--skip-github-hook",
            ],
            check=True,
            cwd=root_dir,
        )


if __name__ == "__main__":
    main()
