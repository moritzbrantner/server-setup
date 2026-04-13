#!/usr/bin/env python3
from __future__ import annotations

import argparse

from simple_setup_common import repo_root, require_root, run_checked, setup_automation_units


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare the server baseline once.")
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--skip-hardening", action="store_true")
    parser.add_argument("--with-status-webapp", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    require_root()
    root = repo_root()

    print("[1/4] Installing baseline tools")
    cmd = ["bash", str(root / "scripts/ensure-server-tools.sh")]
    if args.skip_docker:
        cmd.append("--skip-docker")
    run_checked(cmd, cwd=root)

    if args.skip_hardening:
        print("[2/4] Skipping SSH/UFW/fail2ban hardening")
    else:
        print("[2/4] Applying SSH/UFW/fail2ban hardening")
        run_checked(["bash", str(root / "scripts/harden-server.sh")], cwd=root)

    print("[3/4] Installing deploy automation services")
    setup_automation_units(root, start_webhook=False)

    if args.with_status_webapp:
        print("[4/4] Installing status webapp")
        run_checked(["bash", str(root / "scripts/setup-status-webapp.sh"), "--root", str(root)], cwd=root)
    else:
        print("[4/4] Status webapp skipped")

    print(
        "\nServer preparation complete.\n"
        "- Tools: installed\n"
        f"- Docker: {'skipped' if args.skip_docker else 'installed'}\n"
        f"- Hardening: {'skipped' if args.skip_hardening else 'applied'}\n"
        "- Deploy automation: watcher + timer enabled\n"
        "- Webhook receiver: installed and enabled, starts after deploy-repo configures a secret"
    )


if __name__ == "__main__":
    main()
