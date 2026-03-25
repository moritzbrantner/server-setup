#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


LETSENCRYPT_LIVE_DIR = Path(os.environ.get("LETSENCRYPT_LIVE_DIR", "/etc/letsencrypt/live"))
LETSENCRYPT_OPTIONS_PATH = Path(
    os.environ.get("LETSENCRYPT_OPTIONS_PATH", "/etc/letsencrypt/options-ssl-nginx.conf")
)
LETSENCRYPT_DHPARAM_PATH = Path(
    os.environ.get("LETSENCRYPT_DHPARAM_PATH", "/etc/letsencrypt/ssl-dhparams.pem")
)


def valid_port(value: str) -> bool:
    return value.isdigit() and 1 <= int(value) <= 65535


def render_nginx_site_config(domain: str, root: str, port: str, www_redirect: bool) -> str:
    cert_dir = LETSENCRYPT_LIVE_DIR / domain
    cert_fullchain = cert_dir / "fullchain.pem"
    cert_privkey = cert_dir / "privkey.pem"
    has_tls = (
        cert_fullchain.is_file()
        and cert_privkey.is_file()
        and LETSENCRYPT_OPTIONS_PATH.is_file()
        and LETSENCRYPT_DHPARAM_PATH.is_file()
    )

    if port:
        location_block = (
            "    location / {\n"
            "        proxy_http_version 1.1;\n"
            "        proxy_set_header Host $host;\n"
            "        proxy_set_header X-Real-IP $remote_addr;\n"
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "        proxy_set_header X-Forwarded-Proto $scheme;\n"
            "        proxy_set_header X-Forwarded-Host $host;\n"
            "        proxy_set_header X-Forwarded-Port $server_port;\n"
            "        proxy_set_header Upgrade $http_upgrade;\n"
            '        proxy_set_header Connection "upgrade";\n'
            f"        proxy_pass http://127.0.0.1:{port};\n"
            "        proxy_read_timeout 60s;\n"
            "        proxy_send_timeout 60s;\n"
            "    }\n"
        )
    else:
        location_block = (
            f"    root {root};\n"
            "    index index.html index.htm;\n\n"
            "    location / {\n"
            "        try_files $uri $uri/ =404;\n"
            "    }\n"
        )

    redirect_block = ""
    if www_redirect:
        redirect_block += (
            "\nserver {\n"
            "    listen 80;\n"
            "    listen [::]:80;\n"
            f"    server_name www.{domain};\n\n"
            f"    return 301 https://{domain}$request_uri;\n"
            "}\n"
        )
        if has_tls:
            redirect_block += (
                "\nserver {\n"
                "    listen 443 ssl;\n"
                "    listen [::]:443 ssl;\n"
                f"    server_name www.{domain};\n\n"
                f"    ssl_certificate {cert_fullchain};\n"
                f"    ssl_certificate_key {cert_privkey};\n"
                f"    include {LETSENCRYPT_OPTIONS_PATH};\n"
                f"    ssl_dhparam {LETSENCRYPT_DHPARAM_PATH};\n\n"
                f"    return 301 https://{domain}$request_uri;\n"
                "}\n"
            )

    if has_tls:
        return (
            "server {\n"
            "    listen 80;\n"
            "    listen [::]:80;\n"
            f"    server_name {domain};\n\n"
            f"    return 301 https://{domain}$request_uri;\n"
            "}\n\n"
            "server {\n"
            "    listen 443 ssl;\n"
            "    listen [::]:443 ssl;\n"
            f"    server_name {domain};\n\n"
            f"    ssl_certificate {cert_fullchain};\n"
            f"    ssl_certificate_key {cert_privkey};\n"
            f"    include {LETSENCRYPT_OPTIONS_PATH};\n"
            f"    ssl_dhparam {LETSENCRYPT_DHPARAM_PATH};\n\n"
            f"{location_block}\n"
            f"    access_log /var/log/nginx/{domain}.access.log;\n"
            f"    error_log  /var/log/nginx/{domain}.error.log;\n"
            "}\n"
            f"{redirect_block}"
        )

    return (
        "server {\n"
        "    listen 80;\n"
        "    listen [::]:80;\n"
        f"    server_name {domain};\n\n"
        f"{location_block}\n"
        f"    access_log /var/log/nginx/{domain}.access.log;\n"
        f"    error_log  /var/log/nginx/{domain}.error.log;\n"
        "}\n"
        f"{redirect_block}"
    )


def run_checked(cmd: list[str]) -> None:
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This script must be run as root (use sudo).")


def create_static_root_if_needed(root: str, domain: str) -> None:
    if not root:
        return
    print("[2/6] Creating web root...")
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    index_path = root_path / "index.html"
    if not index_path.exists():
        index_path.write_text(
            "<!doctype html>\n"
            "<html lang=\"en\">\n"
            f"  <head><meta charset=\"utf-8\"><title>{domain}</title></head>\n"
            f"  <body><h1>{domain} is live</h1></body>\n"
            "</html>\n",
            encoding="utf-8",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install nginx and configure a site for a domain."
    )
    parser.add_argument("--domain")
    parser.add_argument("--root", default="")
    parser.add_argument("--port", default="")
    parser.add_argument("--www-redirect", action="store_true")
    parser.add_argument("--email", default="")
    parser.add_argument("--render-config", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.domain:
        raise SystemExit("Error: --domain is required.")
    if (not args.root and not args.port) or (args.root and args.port):
        raise SystemExit("Error: one of --root or --port is required.")
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for ch in args.domain):
        raise SystemExit(f"Error: invalid domain value '{args.domain}'.")
    if args.port and not valid_port(args.port):
        raise SystemExit("Error: --port must be a numeric TCP port between 1 and 65535.")

    if args.render_config:
        print(render_nginx_site_config(args.domain, args.root, args.port, args.www_redirect))
        return

    require_root()
    site_conf = Path(f"/etc/nginx/sites-available/{args.domain}.conf")
    site_link = Path(f"/etc/nginx/sites-enabled/{args.domain}.conf")

    print("[1/6] Installing Nginx...")
    run_checked(["apt-get", "update", "-y"])
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    result = subprocess.run(
        ["apt-get", "install", "-y", "nginx"], env=env, text=True, capture_output=True, check=False
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)

    if args.root:
        create_static_root_if_needed(args.root, args.domain)
    else:
        print("[2/6] Configuring reverse proxy target...")
        print(f"Proxy upstream: http://127.0.0.1:{args.port}")

    print("[3/6] Writing Nginx site config...")
    site_conf.write_text(
        render_nginx_site_config(args.domain, args.root, args.port, args.www_redirect) + "\n",
        encoding="utf-8",
    )

    print("[4/6] Enabling site...")
    if site_link.exists() or site_link.is_symlink():
        site_link.unlink()
    os.symlink(site_conf, site_link)
    default_site = Path("/etc/nginx/sites-enabled/default")
    if default_site.exists():
        default_site.unlink()

    print("[5/6] Validating and reloading Nginx...")
    run_checked(["nginx", "-t"])
    run_checked(["systemctl", "enable", "nginx"])
    run_checked(["systemctl", "restart", "nginx"])

    print("[6/6] Adjusting firewall (if UFW is active)...")
    if shutil.which("ufw"):
        status = subprocess.run(["ufw", "status"], text=True, capture_output=True, check=False)
        if "Status: active" in status.stdout:
            run_checked(["ufw", "allow", "Nginx Full"])
            print("UFW updated: allowed 'Nginx Full'.")
        else:
            print("UFW installed but not active; skipping firewall change.")
    else:
        print("UFW not installed; skipping firewall change.")


if __name__ == "__main__":
    main()
