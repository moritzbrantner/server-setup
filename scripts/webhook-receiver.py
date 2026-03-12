#!/usr/bin/env python3
"""GitHub webhook receiver that triggers the discovery/deploy service."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "9001"))
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/github/push")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
WEBHOOK_ALLOW_INSECURE = os.environ.get("WEBHOOK_ALLOW_INSECURE", "false").lower() == "true"
WEBHOOK_ALLOWED_REPOS = {
    item.strip() for item in os.environ.get("WEBHOOK_ALLOWED_REPOS", "").split(",") if item.strip()
}
WEBHOOK_ALLOWED_BRANCHES = {
    item.strip() for item in os.environ.get("WEBHOOK_ALLOWED_BRANCHES", "").split(",") if item.strip()
}
RUNNER_SERVICE = os.environ.get("RUNNER_SERVICE", "site-discovery-deploy.service")
LOG_DIR = Path(os.environ.get("LOG_DIR", "/var/log/server-setup"))
LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", "14"))


def log_event(action: str, result: str, message: str, *, level: str = "info") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOG_RETENTION_DAYS)
    for path in LOG_DIR.glob("webhook-*.log"):
        if datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff:
            path.unlink(missing_ok=True)

    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": action,
        "result": result,
        "level": level,
        "message": message,
    }
    line = json.dumps(payload)
    print(line, flush=True)
    with (LOG_DIR / f"webhook-{datetime.now(timezone.utc):%Y%m%d}.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def valid_signature(payload: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        return WEBHOOK_ALLOW_INSECURE

    if not signature.startswith("sha256="):
        return False

    provided = signature.split("=", 1)[1]
    expected = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(provided, expected)


def should_trigger_deploy(payload: dict) -> bool:
    repo = ((payload.get("repository") or {}).get("full_name") or "").strip()
    ref = str(payload.get("ref") or "")
    branch = ref.removeprefix("refs/heads/")

    if WEBHOOK_ALLOWED_REPOS and repo not in WEBHOOK_ALLOWED_REPOS:
        return False
    if WEBHOOK_ALLOWED_BRANCHES and branch not in WEBHOOK_ALLOWED_BRANCHES:
        return False
    return True


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
            log_event("webhook", "rejected", "invalid signature", level="error")
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"invalid signature")
            return

        event = self.headers.get("X-GitHub-Event", "")
        if event != "push":
            log_event("webhook", "ignored", f"event={event or 'unknown'}")
            self.send_response(202)
            self.end_headers()
            self.wfile.write(b"ignored event")
            return

        try:
            body = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            log_event("webhook", "rejected", "invalid json", level="error")
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"invalid json")
            return

        if not should_trigger_deploy(body):
            log_event("webhook", "ignored", "push did not match repo/branch filters")
            self.send_response(202)
            self.end_headers()
            self.wfile.write(b"ignored push")
            return

        subprocess.run(["systemctl", "start", RUNNER_SERVICE], check=False)
        log_event("webhook", "accepted", "deploy triggered")
        self.send_response(202)
        self.end_headers()
        self.wfile.write(b"deploy triggered")


def main() -> None:
    if not WEBHOOK_SECRET and not WEBHOOK_ALLOW_INSECURE:
        raise SystemExit("WEBHOOK_SECRET must be set unless WEBHOOK_ALLOW_INSECURE=true")

    server = HTTPServer((WEBHOOK_HOST, WEBHOOK_PORT), Handler)
    print(
        f"Webhook receiver listening on {WEBHOOK_HOST}:{WEBHOOK_PORT}{WEBHOOK_PATH}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
