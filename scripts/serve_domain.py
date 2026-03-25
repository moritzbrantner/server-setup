#!/usr/bin/env python3
from __future__ import annotations

import argparse

from interactive_cli import ensure_interactive, maybe_sudo, prompt_bool, prompt_text, run_command, shell_script


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive Nginx domain bootstrap wrapper.")
    parser.add_argument("--domain")
    parser.add_argument("--root")
    parser.add_argument("--port")
    parser.add_argument("--www-redirect", action="store_true")
    parser.add_argument("--email", default="")
    parser.add_argument("--interactive", action="store_true")
    return parser.parse_args()


def collect_missing(args: argparse.Namespace) -> None:
    ensure_interactive(args, ["domain"])
    if not args.domain:
        args.domain = prompt_text("Domain", required=True)
    if not args.root and not args.port:
        if prompt_bool("Proxy an existing local app port?", default=True):
            args.port = prompt_text("App port", default="4001", required=True)
        else:
            args.root = prompt_text("Static web root", required=True)
    if not args.www_redirect:
        args.www_redirect = prompt_bool("Redirect www to apex domain?", default=False)
    if not args.email:
        args.email = prompt_text("Admin email for metadata", default="")


def main() -> None:
    args = parse_args()
    collect_missing(args)

    cmd = [
        "bash",
        str(shell_script("install-nginx-site.sh")),
        "--domain",
        args.domain,
    ]
    if args.root:
        cmd.extend(["--root", args.root])
    else:
        cmd.extend(["--port", args.port])
    if args.www_redirect:
        cmd.append("--www-redirect")
    if args.email:
        cmd.extend(["--email", args.email])

    run_command(maybe_sudo(cmd))


if __name__ == "__main__":
    main()
