#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This script must be run as root (use sudo).")


def run_checked(cmd: list[str], env: dict[str, str] | None = None, allow_fail: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, text=True, capture_output=True, env=env, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0 and not allow_fail:
        raise SystemExit(result.returncode)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Let's Encrypt certificates with certbot.")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--www", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for ch in args.domain):
        raise SystemExit(f"Error: invalid domain value '{args.domain}'.")
    if "@" not in args.email or "." not in args.email.split("@")[-1]:
        raise SystemExit(f"Error: invalid email '{args.email}'.")
    require_root()

    print("[1/5] Installing Certbot and Nginx plugin...")
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    run_checked(["apt-get", "update", "-y"], env=env)
    run_checked(["apt-get", "install", "-y", "certbot", "python3-certbot-nginx"], env=env)

    domains = ["-d", args.domain]
    if args.www:
        domains.extend(["-d", f"www.{args.domain}"])

    print("[2/5] Requesting and installing certificate...")
    run_checked(
        [
            "certbot",
            "--nginx",
            "--non-interactive",
            "--agree-tos",
            "--keep-until-expiring",
            "--expand",
            "--cert-name",
            args.domain,
            "--email",
            args.email,
            "--redirect",
            *domains,
        ]
    )

    print("[3/5] Ensuring certbot.timer is enabled...")
    run_checked(["systemctl", "enable", "certbot.timer"], allow_fail=True)
    run_checked(["systemctl", "start", "certbot.timer"], allow_fail=True)

    print("[4/5] Checking certificate configuration...")
    run_checked(["certbot", "certificates", "--cert-name", args.domain])

    print("[5/5] Reloading Nginx...")
    run_checked(["nginx", "-t"])
    run_checked(["systemctl", "reload", "nginx"])

    print(
        "\nDone.\n"
        f"Certificate installed for: {args.domain}{' and www.' + args.domain if args.www else ''}\n"
        "Auto-renewal: enabled (certbot.timer)\n\n"
        "Verify:\n  sudo certbot certificates"
    )


if __name__ == "__main__":
    main()
