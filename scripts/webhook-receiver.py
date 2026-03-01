#!/usr/bin/env python3
"""Minimal GitHub webhook receiver that triggers systemd deployment service."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "9001"))
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/github/push")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
RUNNER_SERVICE = os.environ.get("RUNNER_SERVICE", "site-discovery-deploy.service")


def valid_signature(payload: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        return True

    if not signature.startswith("sha256="):
        return False

    provided = signature.split("=", 1)[1]
    expected = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(provided, expected)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path != WEBHOOK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(content_length)

        signature = self.headers.get("X-Hub-Signature-256", "")
        if not valid_signature(payload, signature):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"invalid signature")
            return

        event = self.headers.get("X-GitHub-Event", "")
        if event != "push":
            self.send_response(202)
            self.end_headers()
            self.wfile.write(b"ignored event")
            return

        try:
            json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"invalid json")
            return

        subprocess.run(["systemctl", "start", RUNNER_SERVICE], check=False)

        self.send_response(202)
        self.end_headers()
        self.wfile.write(b"deploy triggered")


def main() -> None:
    server = HTTPServer((WEBHOOK_HOST, WEBHOOK_PORT), Handler)
    print(
        f"Webhook receiver listening on {WEBHOOK_HOST}:{WEBHOOK_PORT}{WEBHOOK_PATH}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
