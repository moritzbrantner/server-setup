#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CACHE = {
    "last_updated": None,
    "sites": [],
    "system": {},
}
CACHE_LOCK = threading.Lock()
DB_LOCK = threading.Lock()


def default_config_path() -> Path:
    deploy_path = Path("deploy/sites.json")
    if deploy_path.exists():
        return deploy_path
    return Path("monitor/websites.json")


def load_state(state_dir: Path | None, site_name: str) -> dict:
    if state_dir is None:
        return {}
    path = state_dir / f"{site_name}.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_sites(config_path: Path, state_dir: Path | None = None):
    with config_path.open("r", encoding="utf-8") as f:
        sites = json.load(f)

    if not isinstance(sites, list):
        raise ValueError("Config must be a JSON array of website objects.")

    normalized = []
    for idx, site in enumerate(sites):
        if not isinstance(site, dict):
            raise ValueError(f"Site at index {idx} must be an object.")

        name = site.get("name")
        if not name:
            raise ValueError(f"Site at index {idx} must include name.")

        url = site.get("url") or site.get("site_url")
        if not url and site.get("domain"):
            url = f"https://{site['domain']}"
        if not url:
            raise ValueError(f"Site at index {idx} must include url/site_url/domain.")

        timeout = float(site.get("timeout", 5))
        deploy_state = load_state(state_dir, name)
        normalized.append(
            {
                "name": name,
                "url": url,
                "timeout": timeout,
                "deploy": deploy_state,
                "runtime_mode": ((site.get("runtime") or {}).get("mode") or "static"),
            }
        )

    return normalized


def init_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                site_name TEXT NOT NULL,
                ok INTEGER NOT NULL,
                status_code INTEGER,
                latency_ms REAL,
                error TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checks_ts ON checks(ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checks_site_ts ON checks(site_name, ts)"
        )


def check_site(site):
    start = time.perf_counter()
    req = urllib.request.Request(
        site["url"],
        headers={"User-Agent": "server-setup-monitor/1.0"},
    )

    try:
        with urllib.request.urlopen(req, timeout=site["timeout"]) as resp:
            latency = (time.perf_counter() - start) * 1000
            code = int(resp.status)
            return {
                "name": site["name"],
                "url": site["url"],
                "ok": 200 <= code < 400,
                "status_code": code,
                "latency_ms": round(latency, 1),
                "error": None,
                "deploy": site.get("deploy", {}),
                "runtime_mode": site.get("runtime_mode"),
            }
    except Exception as exc:  # noqa: BLE001
        latency = (time.perf_counter() - start) * 1000
        return {
            "name": site["name"],
            "url": site["url"],
            "ok": False,
            "status_code": None,
            "latency_ms": round(latency, 1),
            "error": str(exc),
            "deploy": site.get("deploy", {}),
            "runtime_mode": site.get("runtime_mode"),
        }


def parse_meminfo():
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            values = {}
            for line in f:
                k, v = line.split(":", 1)
                values[k.strip()] = int(v.strip().split()[0])
        total = values.get("MemTotal", 0) / 1024
        available = values.get("MemAvailable", 0) / 1024
        used = max(total - available, 0)
        used_pct = (used / total * 100) if total else 0
        return {
            "total_mb": round(total, 0),
            "used_mb": round(used, 0),
            "used_pct": round(used_pct, 1),
        }
    except Exception:  # noqa: BLE001
        return {"total_mb": None, "used_mb": None, "used_pct": None}


def service_status(name):
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return "unknown"
    result = subprocess.run(
        [systemctl, "is-active", name],
        text=True,
        capture_output=True,
        check=False,
    )
    out = (result.stdout or "").strip()
    if out:
        return out
    return "unknown"


def collect_system_metrics():
    disk = shutil.disk_usage("/")
    load1, load5, load15 = os.getloadavg()
    mem = parse_meminfo()
    return {
        "hostname": os.uname().nodename,
        "load": {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)},
        "disk": {
            "total_gb": round(disk.total / (1024**3), 1),
            "used_gb": round(disk.used / (1024**3), 1),
            "used_pct": round((disk.used / disk.total) * 100, 1) if disk.total else 0,
        },
        "memory": mem,
        "services": {
            "nginx": service_status("nginx"),
            "docker": service_status("docker"),
        },
    }


def persist_results(db_path: Path, ts: int, results):
    with DB_LOCK:
        with sqlite3.connect(db_path) as conn:
            conn.executemany(
                """
                INSERT INTO checks (ts, site_name, ok, status_code, latency_ms, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        ts,
                        row["name"],
                        1 if row["ok"] else 0,
                        row["status_code"],
                        row["latency_ms"],
                        row["error"],
                    )
                    for row in results
                ],
            )
            cutoff = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
            conn.execute("DELETE FROM checks WHERE ts < ?", (cutoff,))


def read_analytics(db_path: Path):
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp())
    with DB_LOCK:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT site_name,
                       COUNT(*) as total,
                       SUM(ok) as ok_count,
                       AVG(latency_ms) as avg_latency,
                       MAX(latency_ms) as max_latency
                FROM checks
                WHERE ts >= ?
                GROUP BY site_name
                ORDER BY site_name
                """,
                (cutoff,),
            ).fetchall()

    analytics = []
    for name, total, ok_count, avg_latency, max_latency in rows:
        ok_count = ok_count or 0
        uptime = (ok_count / total * 100) if total else 0
        analytics.append(
            {
                "name": name,
                "samples": total,
                "uptime_24h_pct": round(uptime, 2),
                "avg_latency_ms": round(avg_latency, 1) if avg_latency is not None else None,
                "max_latency_ms": round(max_latency, 1) if max_latency is not None else None,
            }
        )
    return analytics


def start_monitor(sites, db_path: Path, interval: int):
    def loop():
        while True:
            ts = int(time.time())
            with ThreadPoolExecutor(max_workers=min(16, max(1, len(sites)))) as pool:
                results = list(pool.map(check_site, sites))

            persist_results(db_path, ts, results)
            system = collect_system_metrics()
            with CACHE_LOCK:
                CACHE["last_updated"] = ts
                CACHE["sites"] = results
                CACHE["system"] = system
            time.sleep(interval)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()


HTML = """<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\"/>
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>
  <title>Server Overview</title>
  <style>
    body { font-family: Inter, Arial, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
    .wrap { max-width: 1200px; margin: 0 auto; padding: 20px; }
    .cards { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); }
    .card { background: #1e293b; padding: 14px; border-radius: 10px; border: 1px solid #334155; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { padding: 10px; border-bottom: 1px solid #334155; text-align: left; vertical-align: top; }
    th { color: #94a3b8; }
    .ok { color: #22c55e; font-weight: 600; }
    .bad { color: #ef4444; font-weight: 600; }
    .muted { color: #94a3b8; }
    .head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
    h1, h2 { margin: 0; }
    .small { font-size: 13px; }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"head\"><h1>Server & Website Overview</h1><div id=\"updated\" class=\"small muted\"></div></div>
    <div id=\"system\" class=\"cards\"></div>

    <h2 style=\"margin-top:20px\">Live website checks</h2>
    <table id=\"sites\"><thead><tr><th>Website</th><th>Status</th><th>Deploy</th><th>Release</th><th>Latency</th><th>Error</th></tr></thead><tbody></tbody></table>

    <h2 style=\"margin-top:20px\">24h analytics</h2>
    <table id=\"analytics\"><thead><tr><th>Website</th><th>Uptime</th><th>Samples</th><th>Avg latency</th><th>Max latency</th></tr></thead><tbody></tbody></table>
  </div>
  <script>
    function statusClass(ok) { return ok ? 'ok' : 'bad'; }
    function fmt(v, suffix='') { return (v === null || v === undefined || v === '') ? 'n/a' : `${v}${suffix}`; }

    async function refresh() {
      const res = await fetch('/api/overview');
      const data = await res.json();

      document.getElementById('updated').textContent = data.last_updated
        ? `Last update: ${new Date(data.last_updated * 1000).toLocaleString()}`
        : 'Waiting for first check...';

      const s = data.system || {};
      document.getElementById('system').innerHTML = `
        <div class='card'><div class='muted small'>Host</div><div>${s.hostname || 'n/a'}</div></div>
        <div class='card'><div class='muted small'>Load (1m / 5m / 15m)</div><div>${fmt(s.load?.['1m'])} / ${fmt(s.load?.['5m'])} / ${fmt(s.load?.['15m'])}</div></div>
        <div class='card'><div class='muted small'>Disk usage</div><div>${fmt(s.disk?.used_gb, ' GB')} / ${fmt(s.disk?.total_gb, ' GB')} (${fmt(s.disk?.used_pct, '%')})</div></div>
        <div class='card'><div class='muted small'>Memory usage</div><div>${fmt(s.memory?.used_mb, ' MB')} / ${fmt(s.memory?.total_mb, ' MB')} (${fmt(s.memory?.used_pct, '%')})</div></div>
        <div class='card'><div class='muted small'>Services</div><div>Nginx: <span class='${s.services?.nginx === 'active' ? 'ok' : 'bad'}'>${s.services?.nginx || 'unknown'}</span><br>Docker: <span class='${s.services?.docker === 'active' ? 'ok' : 'bad'}'>${s.services?.docker || 'unknown'}</span></div></div>
      `;

      const siteBody = document.querySelector('#sites tbody');
      siteBody.innerHTML = (data.sites || []).map(site => `
        <tr>
          <td>${site.name}<div class='muted small'>${site.url}</div></td>
          <td class='${statusClass(site.ok)}'>${site.ok ? 'UP' : 'DOWN'}</td>
          <td>${fmt(site.deploy?.last_deploy_status)}<div class='muted small'>health: ${fmt(site.deploy?.last_health_check?.status)}</div></td>
          <td class='small'>${fmt(site.deploy?.current_release)}</td>
          <td>${fmt(site.latency_ms, ' ms')}</td>
          <td class='small'>${site.error || site.deploy?.last_failure_reason || ''}</td>
        </tr>
      `).join('');

      const anBody = document.querySelector('#analytics tbody');
      anBody.innerHTML = (data.analytics || []).map(row => `
        <tr>
          <td>${row.name}</td>
          <td class='${row.uptime_24h_pct >= 99 ? 'ok' : 'bad'}'>${fmt(row.uptime_24h_pct, '%')}</td>
          <td>${fmt(row.samples)}</td>
          <td>${fmt(row.avg_latency_ms, ' ms')}</td>
          <td>${fmt(row.max_latency_ms, ' ms')}</td>
        </tr>
      `).join('');
    }

    refresh();
    setInterval(refresh, 30000);
  </script>
</body>
</html>
"""


def overview_payload(db_path: Path) -> dict:
    with CACHE_LOCK:
        payload = {
            "last_updated": CACHE["last_updated"],
            "sites": CACHE["sites"],
            "system": CACHE["system"],
        }
    payload["analytics"] = read_analytics(db_path)
    return payload


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/overview":
            body = json.dumps(overview_payload(Path(self.server.db_path))).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/status":
            overview = overview_payload(Path(self.server.db_path))
            body = json.dumps(
                {
                    "last_updated": overview["last_updated"],
                    "sites": [
                        {
                            "name": site["name"],
                            "ok": site["ok"],
                            "status_code": site["status_code"],
                            "deploy": site.get("deploy", {}),
                        }
                        for site in overview["sites"]
                    ],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(404, "Not found")

    def log_message(self, format, *args):
        return


def main():
    parser = argparse.ArgumentParser(description="Simple website/server overview dashboard")
    parser.add_argument("--config", default=None, help="Path to websites config JSON")
    parser.add_argument("--db", default="monitor/history.db", help="Path to sqlite database")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8085, help="Port to bind")
    parser.add_argument("--interval", type=int, default=30, help="Check interval in seconds")
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("STATE_DIR", "/var/lib/server-setup/state"),
        help="Path to deploy state JSON directory",
    )
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else default_config_path()
    state_dir = Path(args.state_dir) if Path(args.state_dir).exists() else None

    sites = load_sites(config_path, state_dir=state_dir)
    db_path = Path(args.db)
    init_db(db_path)
    start_monitor(sites, db_path, args.interval)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.db_path = str(db_path)
    print(f"Dashboard running on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
