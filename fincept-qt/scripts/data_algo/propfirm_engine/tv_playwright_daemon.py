"""
Long-running Playwright daemon for TV auto-exec.

Connects to Chrome via CDP at startup, keeps browser + page in memory, and
listens on localhost:5556 for trade commands. Webhook posts to /trade on each
entry alert; daemon executes the click sequence and returns result.

Why daemon: each cold-start of Playwright takes 5-8s. With long-running session,
each trade fires in ~300ms (ribbon click + submit click).

Start:
    python -m propfirm_engine.tv_playwright_daemon  # listens on :5556

Test:
    curl -X POST http://127.0.0.1:5556/trade \\
         -H 'Content-Type: application/json' \\
         -d '{"action": "buy", "ticker": "AAPL", "qty": 1}'

Webhook integration:
    tv_webhook.py imports tv_playwright_daemon_client and fires-and-forget
    after open_trade succeeds.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import tv_playwright as tx


DAEMON_PORT = 5556

# Global state holding the Playwright session
_state: dict = {"pw": None, "context": None, "page": None, "lock": threading.Lock()}


def _init_session(cdp_url: str = "http://127.0.0.1:9222"):
    """Open Playwright CDP connection once at daemon start."""
    pw, context, page = tx.connect_cdp(cdp_url)
    _state["pw"] = pw
    _state["context"] = context
    _state["page"] = page
    print(f"[daemon] connected to Chrome CDP; TV page url: {page.url}", file=sys.stderr)


def _execute_trade(action: str, ticker: str | None, qty: int) -> dict:
    """Run the trade execution under lock to serialize multiple alerts."""
    with _state["lock"]:
        page = _state["page"]
        if page is None:
            return {"error": "no page in session"}
        try:
            log = tx.execute_signal(page, action, ticker, qty)
            return log
        except Exception as e:
            return {"error": f"execute_signal failed: {e}"}


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            page = _state.get("page")
            return self._reply(200, {
                "ok": True,
                "url": page.url if page else None,
                "title": page.title()[:60] if page else None,
            })
        if self.path == "/status":
            page = _state.get("page")
            if page is None:
                return self._reply(503, {"error": "no page"})
            try:
                return self._reply(200, tx.get_status(page))
            except Exception as e:
                return self._reply(500, {"error": str(e)})
        if self.path == "/account":
            page = _state.get("page")
            if page is None:
                return self._reply(503, {"error": "no page"})
            try:
                return self._reply(200, tx.get_account_state(page))
            except Exception as e:
                return self._reply(500, {"error": str(e)})
        self._reply(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/trade":
            return self._reply(404, {"error": "expected POST /trade"})
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode(errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as e:
            return self._reply(400, {"error": f"invalid json: {e}"})
        action = body.get("action", "")
        ticker = body.get("ticker")
        qty = int(body.get("qty") or 1)
        if action.lower() not in ("buy", "sell", "long", "short"):
            return self._reply(400, {"error": f"bad action: {action}"})
        result = _execute_trade(action, ticker, qty)
        self._reply(200, result)
        self.log_message("TRADE %s %s qty=%s → aborted=%s steps=%d",
                         action, ticker, qty,
                         result.get("aborted", "no"), len(result.get("steps", [])))

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")


def main():
    p = argparse.ArgumentParser(prog="tv_playwright_daemon")
    p.add_argument("--port", type=int, default=DAEMON_PORT)
    p.add_argument("--cdp", default="http://127.0.0.1:9222")
    args = p.parse_args()

    _init_session(args.cdp)

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[daemon] tv_playwright_daemon listening on http://127.0.0.1:{args.port}", file=sys.stderr)
    print(f"[daemon] endpoints: GET /health /status /account, POST /trade", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try: _state["context"].close()
        except: pass
        try: _state["pw"].stop()
        except: pass


if __name__ == "__main__":
    main()
