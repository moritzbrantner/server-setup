"""Local-only read-only web view over the server-setup core."""

from __future__ import annotations

import html
import ipaddress
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from server_setup.config import ServerSetupConfig
from server_setup.core import ServerSetupCore
from server_setup.modules import default_modules
from server_setup.system import System


class UiError(ValueError):
    """Raised when the local UI would violate its safety boundary."""


def validate_loopback_host(host: str) -> str:
    candidate = host.strip()
    if not candidate:
        raise UiError("UI host must not be empty")
    if candidate.lower() == "localhost":
        return candidate
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError as error:
        raise UiError("UI host must be localhost or a literal loopback address") from error
    if not address.is_loopback:
        raise UiError("UI is local-only; refusing to bind to a non-loopback address")
    return candidate


def validate_port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise UiError("UI port must be between 1 and 65535")
    return port


def collect_state(config: ServerSetupConfig, system: System) -> dict[str, Any]:
    core = ServerSetupCore(config, default_modules(system))
    plan = core.plan()
    report = core.validate()
    return {
        "drift": plan.has_changes or not report.ok,
        "config": {
            "version": config.version,
            "host": {
                "timezone": config.host.timezone,
                "unattended_upgrades": config.host.unattended_upgrades,
            },
            "security": {
                "firewall": config.security.firewall,
                "fail2ban": config.security.fail2ban,
                "ssh_hardening": config.security.ssh_hardening,
            },
            "dokploy": {
                "enabled": config.dokploy.enabled,
                "version": config.dokploy.version,
            },
            "dns": {"enabled": config.dns.enabled},
            "monitoring": {
                "uptime_kuma": config.monitoring.uptime_kuma,
                "beszel": config.monitoring.beszel,
            },
        },
        "changes": [
            {
                "module": change.module,
                "kind": change.kind.value,
                "summary": change.summary,
                "details": change.details,
                "action": change.action,
                "target": change.target,
            }
            for change in plan.changes
        ],
        "validation": [
            {
                "module": result.module,
                "status": result.status.value,
                "summary": result.summary,
                "details": result.details,
            }
            for result in report.results
        ],
    }


def _cell(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def render_html(state: dict[str, Any]) -> str:
    changes = state["changes"]
    validation = state["validation"]
    config = state["config"]
    drift_label = "Drift detected" if state["drift"] else "Converged"

    change_rows = "".join(
        "<tr>"
        f"<td>{_cell(change['module'])}</td>"
        f"<td>{_cell(change['kind'])}</td>"
        f"<td>{_cell(change['summary'])}</td>"
        f"<td>{_cell(change['details'])}</td>"
        "</tr>"
        for change in changes
    ) or '<tr><td colspan="4">No planned changes.</td></tr>'

    validation_rows = "".join(
        "<tr>"
        f"<td>{_cell(result['module'])}</td>"
        f"<td>{_cell(result['status'])}</td>"
        f"<td>{_cell(result['summary'])}</td>"
        f"<td>{_cell(result['details'])}</td>"
        "</tr>"
        for result in validation
    ) or '<tr><td colspan="4">No validation results.</td></tr>'

    config_rows: list[str] = []
    for section, values in config.items():
        if isinstance(values, dict):
            for key, value in values.items():
                config_rows.append(
                    "<tr>"
                    f"<td>{_cell(section)}</td>"
                    f"<td>{_cell(key)}</td>"
                    f"<td>{_cell(value)}</td>"
                    "</tr>"
                )
        else:
            config_rows.append(
                "<tr>"
                f"<td>config</td><td>{_cell(section)}</td><td>{_cell(values)}</td>"
                "</tr>"
            )

    return "".join(
        [
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<meta http-equiv="refresh" content="15">',
            "<title>server-setup</title>",
            "<style>",
            "body{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;line-height:1.45}",
            "table{width:100%;border-collapse:collapse;margin:0 0 2rem}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #ccc;padding:.55rem}",
            "code{font-family:ui-monospace,monospace}h1,h2{margin-top:1.6rem}.status{font-weight:700}",
            "</style></head><body>",
            "<h1>server-setup</h1>",
            f'<p class="status">{_cell(drift_label)}</p>',
            '<p>Read-only local view. Refreshes every 15 seconds. JSON evidence: <a href="/api/state">/api/state</a>.</p>',
            "<h2>Planned changes</h2>",
            "<table><thead><tr><th>Module</th><th>Kind</th><th>Summary</th><th>Details</th></tr></thead>",
            f"<tbody>{change_rows}</tbody></table>",
            "<h2>Validation</h2>",
            "<table><thead><tr><th>Module</th><th>Status</th><th>Summary</th><th>Details</th></tr></thead>",
            f"<tbody>{validation_rows}</tbody></table>",
            "<h2>Desired configuration</h2>",
            "<table><thead><tr><th>Section</th><th>Key</th><th>Value</th></tr></thead>",
            f"<tbody>{''.join(config_rows)}</tbody></table>",
            "</body></html>",
        ]
    )


def _handler_factory(config: ServerSetupConfig, system: System) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _headers(self, content_type: str, length: int) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            state = collect_state(config, system)
            if self.path == "/":
                body = render_html(state).encode("utf-8")
                self._headers("text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if self.path == "/api/state":
                body = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8")
                self._headers("application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            self.send_error(405, "The local UI is read-only")

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def _server_class(host: str) -> type[ThreadingHTTPServer]:
    if ":" not in host:
        return ThreadingHTTPServer

    class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    return IPv6ThreadingHTTPServer


def serve_ui(config: ServerSetupConfig, system: System, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    bind_host = validate_loopback_host(host)
    bind_port = validate_port(port)
    server_class = _server_class(bind_host)
    server = server_class((bind_host, bind_port), _handler_factory(config, system))
    print(f"server-setup UI: http://{bind_host}:{bind_port}/")
    print("Read-only and loopback-only. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
