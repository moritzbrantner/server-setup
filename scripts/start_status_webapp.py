#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    server_setup_root = Path(os.environ.get("SERVER_SETUP_ROOT", str(script_dir.parent)))
    status_webapp_host = os.environ.get("STATUS_WEBAPP_HOST", "0.0.0.0")
    status_webapp_port = os.environ.get("STATUS_WEBAPP_PORT", "4000")
    webapp_dir = server_setup_root / "monitor/webapp"
    bun_install = os.environ.get("BUN_INSTALL", f"{Path.home()}/.bun")
    env = os.environ.copy()
    env["BUN_INSTALL"] = bun_install
    env["PATH"] = f"{bun_install}/bin:{env['PATH']}"

    if not (webapp_dir / "node_modules/next").is_dir():
        subprocess.run(["bun", "install"], cwd=webapp_dir, env=env, check=True)
    if not (webapp_dir / ".next/BUILD_ID").is_file():
        subprocess.run(["bun", "run", "build"], cwd=webapp_dir, env=env, check=True)
    subprocess.run(
        ["bun", "run", "start", "--", "--hostname", status_webapp_host, "--port", status_webapp_port],
        cwd=webapp_dir,
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
