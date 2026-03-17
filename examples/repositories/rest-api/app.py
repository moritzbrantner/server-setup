#!/usr/bin/env python3
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ALLOWED_ORIGINS = {
    "http://app.localhost",
    "http://app.localhost:4002",
    "http://localhost:4002",
    "http://127.0.0.1:4002",
}


def build_database_url():
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]

    required = ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing database environment variables: {', '.join(missing)}")

    return (
        "postgresql://{user}:{password}@{host}:{port}/{db}".format(
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host=os.environ["POSTGRES_HOST"],
            port=os.environ["POSTGRES_PORT"],
            db=os.environ["POSTGRES_DB"],
        )
    )


def sql_literal(value):
    return "'" + value.replace("'", "''") + "'"


def run_query(sql):
    result = subprocess.run(
        [
            "psql",
            build_database_url(),
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-c",
            sql,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "psql command failed")
    return result.stdout.strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.apply_cors()
        self.end_headers()
        self.wfile.write(body)

    def apply_cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self.apply_cors()
        self.end_headers()

    def do_GET(self):
        try:
            if self.path == "/healthz":
                payload = json.loads(
                    run_query(
                        """
                        select json_build_object(
                          'status', 'ok',
                          'database', current_database(),
                          'user', current_user
                        );
                        """
                    )
                )
                self.send_json(200, payload)
                return

            if self.path == "/api/items":
                payload = run_query(
                    """
                    select coalesce(
                      json_agg(
                        json_build_object(
                          'id', id,
                          'title', title,
                          'created_at', to_char(created_at at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                        )
                        order by id desc
                      ),
                      '[]'::json
                    )
                    from demo_items;
                    """
                )
                self.send_json(200, json.loads(payload))
                return

            if self.path == "/api/db-status":
                payload = run_query(
                    """
                    select json_build_object(
                      'database', current_database(),
                      'user', current_user,
                      'item_count', (select count(*) from demo_items)
                    );
                    """
                )
                self.send_json(200, json.loads(payload))
                return

            self.send_json(404, {"error": "Not found"})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def do_POST(self):
        if self.path != "/api/items":
            self.send_json(404, {"error": "Not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            title = str(payload.get("title", "")).strip()
            if not title:
                self.send_json(400, {"error": "Field 'title' is required"})
                return

            inserted = run_query(
                f"""
                insert into demo_items(title)
                values ({sql_literal(title)})
                returning json_build_object(
                  'id', id,
                  'title', title,
                  'created_at', to_char(created_at at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                );
                """
            )
            self.send_json(201, json.loads(inserted))
        except json.JSONDecodeError:
            self.send_json(400, {"error": "Request body must be valid JSON"})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})


def main():
    port = int(os.environ.get("PORT", "4001"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"rest-api listening on {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
