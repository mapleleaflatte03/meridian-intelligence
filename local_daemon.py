#!/usr/bin/env python3
"""Simple local HTTP bridge for running the universal operator."""

from __future__ import annotations

import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HOST = "127.0.0.1"
PORT = 8266
ALLOWED_ORIGIN = "https://app.welliam.codes"
WORKSPACE_DIR = Path(__file__).resolve().parent


class LocalDaemonHandler(BaseHTTPRequestHandler):
    server_version = "MeridianLocalDaemon/0.2"

    def _origin_allowed(self) -> bool:
        return self.headers.get("Origin") == ALLOWED_ORIGIN

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")

    def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._origin_allowed():
            self._send_json(403, {"status": "error", "output": "origin_not_allowed"})
            return
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if not self._origin_allowed():
            self._send_json(403, {"status": "error", "output": "origin_not_allowed"})
            return

        if self.path != "/api/run":
            self._send_json(404, {"status": "error", "output": "not_found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"status": "error", "output": "invalid_content_length"})
            return

        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"status": "error", "output": f"invalid_json: {exc}"})
            return

        goal = payload.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            self._send_json(400, {"status": "error", "output": "goal_required"})
            return

        result = subprocess.run(
            ["python3", "universal_operator.py", goal],
            cwd=str(WORKSPACE_DIR),
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            self._send_json(200, {"status": "success", "output": result.stdout})
            return

        self._send_json(
            500,
            {
                "status": "error",
                "output": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            },
        )

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), LocalDaemonHandler)
    print(f"Local daemon listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
