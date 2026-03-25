#!/usr/bin/env python3
from __future__ import annotations

import argparse

from interactive_cli import ensure_interactive, maybe_sudo, prompt_bool, prompt_text, run_command, shell_script


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive server bootstrap wrapper.")
    parser.add_argument("--domain")
    parser.add_argument("--web-root")
    parser.add_argument("--port")
    parser.add_argument("--email")
    parser.add_argument("--www", action="store_true")
    parser.add_argument("--skip-certbot", action="store_true")
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--skip-hardening", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--skip-automation", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    return parser.parse_args()


def collect_missing(args: argparse.Namespace) -> None:
    ensure_interactive(args, ["domain", "email"])
    if not args.web_root and not args.port:
        if not (args.interactive or __import__("sys").stdin.isatty()):
            raise SystemExit("One of --web-root or --port is required")

    if not args.domain:
        args.domain = prompt_text("Primary domain", required=True)
    if not args.web_root and not args.port:
        if prompt_bool("Proxy an existing app port instead of serving a static web root?", default=True):
            args.port = prompt_text("App port", default="4001", required=True)
        else:
            args.web_root = prompt_text("Static web root", required=True)
    if not args.email:
        args.email = prompt_text("Let's Encrypt email", required=True)
    if not args.www:
        args.www = prompt_bool("Configure www redirect and certificate coverage?", default=False)
    if not args.skip_certbot:
        args.skip_certbot = prompt_bool("Skip Certbot for now?", default=False)
    if not args.skip_docker:
        args.skip_docker = prompt_bool("Skip Docker installation?", default=False)
    if not args.skip_hardening:
        args.skip_hardening = prompt_bool("Skip SSH/UFW/fail2ban hardening?", default=False)
    if not args.skip_automation:
        args.skip_automation = prompt_bool("Skip watcher/webhook automation services?", default=False)


def main() -> None:
    args = parse_args()
    collect_missing(args)

    cmd = [
        "bash",
        str(shell_script("init-server.sh")),
        "--domain",
        args.domain,
        "--email",
        args.email,
    ]
    if args.web_root:
        cmd.extend(["--web-root", args.web_root])
    else:
        cmd.extend(["--port", args.port])
    if args.www:
        cmd.append("--www")
    if args.skip_certbot:
        cmd.append("--skip-certbot")
    if args.skip_docker:
        cmd.append("--skip-docker")
    if args.skip_hardening:
        cmd.append("--skip-hardening")
    if args.non_interactive:
        cmd.append("--non-interactive")
    if args.skip_automation:
        cmd.append("--skip-automation")

    run_command(maybe_sudo(cmd))


if __name__ == "__main__":
    main()
