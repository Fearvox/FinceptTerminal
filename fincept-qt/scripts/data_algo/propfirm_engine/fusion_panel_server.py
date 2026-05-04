"""Local read-only web panel for the propfirm Fusion Chat tab."""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .fusion_panel import DEFAULT_ALERT_LOG_PATH, alert_feed_payload, fusion_panel_html, now_utc


class FusionPanelHandler(BaseHTTPRequestHandler):
    alert_log_path: str = DEFAULT_ALERT_LOG_PATH

    def _json(self, code: int, body: dict):
        data = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _html(self, code: int, body: str):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_HEAD(self):  # noqa: N802
        return self.do_GET()

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {
                "ok": True,
                "ts": now_utc(),
                "mode": "local-readonly-display",
                "mutation_bridge_enabled": False,
                "secret_values_recorded": False,
            })
            return
        if parsed.path == "/alerts":
            q = parse_qs(parsed.query)
            try:
                limit = int((q.get("limit") or [80])[0])
            except ValueError:
                limit = 80
            self._json(200, alert_feed_payload(self.alert_log_path, limit=limit))
            return
        if parsed.path in ("/", "/fusion-panel"):
            if parsed.path == "/":
                self._redirect("/fusion-panel")
                return
            self._html(200, fusion_panel_html())
            return
        self._json(404, {"error": "not_found", "available": ["/health", "/alerts", "/fusion-panel"]})

    def do_POST(self):  # noqa: N802
        self._json(405, {
            "error": "method_not_allowed",
            "allowed_methods": ["GET", "HEAD"],
            "mutation_bridge_enabled": False,
        })

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write(f"[{self.log_date_time_string()}] panel {fmt % args}\n")


def make_handler(alert_log_path: str):
    class _H(FusionPanelHandler):
        pass
    _H.alert_log_path = alert_log_path
    return _H


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="fusion_panel_server",
        description="Local read-only propfirm alert panel for Windburn Fusion Chat.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address; keep 127.0.0.1 unless you know why")
    parser.add_argument("--port", type=int, default=5556)
    parser.add_argument("--alert-log", default=DEFAULT_ALERT_LOG_PATH)
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), make_handler(args.alert_log))
    print(f"▶ propfirm fusion panel listening on http://{args.host}:{args.port}/fusion-panel", flush=True)
    print("  GET /alerts       (sanitized local alert feed)")
    print("  GET /health")
    print("  mode: read-only display; no trade execution")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n▶ shutting down panel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
