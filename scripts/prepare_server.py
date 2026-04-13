#!/usr/bin/env python3
from __future__ import annotations

import argparse

from simple_setup_common import AUTOMATION_ENV_FILE, load_env_file, repo_root, require_root, run_checked, setup_automation_units, update_env_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare the server baseline once.")
    parser.add_argument("--email", default="")
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--skip-hardening", action="store_true")
    parser.add_argument("--with-status-webapp", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    require_root()
    root = repo_root()
    existing_email = load_env_file(AUTOMATION_ENV_FILE).get("DEFAULT_TLS_EMAIL", "").strip()
    tls_email = args.email.strip() or existing_email
    if not tls_email:
        raise SystemExit("--email is required unless DEFAULT_TLS_EMAIL already exists in /etc/default/site-automation")

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
    update_env_file(
        AUTOMATION_ENV_FILE,
        {
            "REPO_ROOT": str(root),
            "REGISTRY_PATH": str(root / "deploy/registry.json"),
            "DEFAULT_TLS_EMAIL": tls_email,
        },
    )

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
        f"- Default TLS email: {tls_email}\n"
        "- Deploy automation: webhook receiver installed\n"
        "- Webhook receiver: enabled, starts after deploy-repo configures a secret"
    )


if __name__ == "__main__":
    main()
