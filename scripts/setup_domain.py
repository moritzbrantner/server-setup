#!/usr/bin/env python3
from __future__ import annotations

import argparse

from simple_setup_common import ensure_dns_points_here, repo_root, require_root, run_checked


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expose a running service or static root under a domain with TLS.")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--root", default="")
    parser.add_argument("--port", default="")
    parser.add_argument("--email", required=True)
    parser.add_argument("--www", action="store_true")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if (not args.root and not args.port) or (args.root and args.port):
        raise SystemExit("Provide exactly one of --root or --port.")


def main() -> None:
    args = parse_args()
    validate_args(args)
    require_root()
    root = repo_root()

    print("[1/3] Installing nginx site")
    nginx_cmd = [
        "python3",
        str(root / "scripts/install_nginx_site.py"),
        "--domain",
        args.domain,
    ]
    if args.root:
        nginx_cmd.extend(["--root", args.root])
    else:
        nginx_cmd.extend(["--port", args.port])
    if args.www:
        nginx_cmd.append("--www-redirect")
    run_checked(nginx_cmd, cwd=root)

    print("[2/3] Verifying DNS points to this server")
    ensure_dns_points_here(args.domain, include_www=args.www)

    print("[3/3] Requesting Let's Encrypt certificate")
    certbot_cmd = [
        "python3",
        str(root / "scripts/setup_letsencrypt.py"),
        "--domain",
        args.domain,
        "--email",
        args.email,
    ]
    if args.www:
        certbot_cmd.append("--www")
    run_checked(certbot_cmd, cwd=root)

    print(
        "\nDomain setup complete.\n"
        f"- Domain: {args.domain}\n"
        f"- Target: {args.root if args.root else f'127.0.0.1:{args.port}'}\n"
        f"- TLS: active for {args.domain}{' and www.' + args.domain if args.www else ''}"
    )


if __name__ == "__main__":
    main()
