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
    print(f"[daemon] cdp mode | TV page url: {page.url}", file=sys.stderr)


def _init_session_persistent(headless: bool = False):
    """Launch a dedicated Chromium with persistent profile (/tmp/tv_pw_profile).

    Independent from operator's main browser — won't get kicked by TV's
    single-active-session rule. Operator logs in once on first run; cookies
    persist in profile directory.
    """
    pw, context, page = tx.open_browser_persistent(headless=headless)
    _state["pw"] = pw
    _state["context"] = context
    _state["page"] = page
    print(f"[daemon] persistent mode | profile=/tmp/tv_pw_profile | TV page url: {page.url}", file=sys.stderr)


def _execute_trade(action: str, ticker: str | None, qty: int, sl: float | None = None,
                   units: int | None = None) -> dict:
    """Run the trade execution under lock to serialize multiple alerts.

    If `units` is set (>0), use submit_one_order: set qty to N units, click once.
    Otherwise fall back to execute_signal (clicks qty times, 1 unit each).
    """
    with _state["lock"]:
        page = _state["page"]
        if page is None:
            return {"error": "no page in session"}
        try:
            if units is not None and units > 0:
                # symbol check still required
                matches, detail = tx._chart_symbol_matches(page, ticker)
                if not matches:
                    return {"aborted": f"chart-symbol-mismatch: {detail}", "units": units}
                panel = tx.ensure_panel(page)
                if not panel.get("tradable"):
                    return {"aborted": "not_tradable", "panel": panel, "units": units}
                return tx.submit_one_order(page, action, units, sl=sl)
            # legacy path: click `qty` times, 1 unit each
            return tx.execute_signal(page, action, ticker, qty, sl=sl)
        except Exception as e:
            return {"error": f"execute failed: {e}"}


def _run_under_lock(fn) -> Any:
    """Helper to run a page-using function under the session lock."""
    with _state["lock"]:
        page = _state["page"]
        if page is None:
            return {"error": "no page in session"}
        try:
            return fn(page)
        except Exception as e:
            return {"error": f"{fn.__name__ if hasattr(fn,'__name__') else 'op'} failed: {e}"}


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
            return self._reply(200, _run_under_lock(tx.get_status))
        if self.path == "/account":
            return self._reply(200, _run_under_lock(tx.get_account_state))
        if self.path == "/positions":
            return self._reply(200, _run_under_lock(tx.list_positions))
        if self.path == "/endpoints":
            return self._reply(200, {
                "GET":  ["/health", "/status", "/account", "/positions", "/endpoints"],
                "POST": ["/trade", "/eval", "/switch-symbol", "/close-all", "/modify-position-sl"],
            })
        self._reply(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode(errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            return self._reply(400, {"error": f"invalid json: {e}"})

        if self.path == "/trade":
            action = body.get("action", "")
            ticker = body.get("ticker")
            qty = int(body.get("qty") or 1)
            units = body.get("units")
            if units is not None:
                try: units = int(units)
                except: units = None
            sl = body.get("sl")
            if sl is not None:
                try: sl = float(sl)
                except: sl = None
            if action.lower() not in ("buy", "sell", "long", "short"):
                return self._reply(400, {"error": f"bad action: {action}"})
            result = _execute_trade(action, ticker, qty, sl=sl, units=units)
            self._reply(200, result)
            self.log_message("TRADE %s %s units=%s qty=%s → aborted=%s",
                             action, ticker, units, qty, result.get("aborted", "no"))
            return

        if self.path == "/eval":
            js = body.get("js", "")
            arg = body.get("arg")
            if not js:
                return self._reply(400, {"error": "missing 'js' field"})
            with _state["lock"]:
                page = _state["page"]
                if page is None:
                    return self._reply(503, {"error": "no page"})
                try:
                    result = tx.eval_js(page, js, arg)
                    self._reply(200, {"result": result})
                except Exception as e:
                    self._reply(500, {"error": f"eval failed: {e}"})
            return

        if self.path == "/switch-symbol":
            symbol = body.get("symbol", "")
            if not symbol:
                return self._reply(400, {"error": "missing 'symbol' field"})
            with _state["lock"]:
                page = _state["page"]
                if page is None:
                    return self._reply(503, {"error": "no page"})
                try:
                    result = tx.switch_symbol(page, symbol)
                    self._reply(200, result)
                except Exception as e:
                    self._reply(500, {"error": f"switch_symbol failed: {e}"})
            return

        if self.path == "/close-all":
            with _state["lock"]:
                page = _state["page"]
                if page is None:
                    return self._reply(503, {"error": "no page"})
                try:
                    result = tx.close_all_positions(page)
                    self._reply(200, result)
                    self.log_message("CLOSE_ALL → clicked=%s found=%s",
                                     result.get("clicked"), result.get("found"))
                except Exception as e:
                    self._reply(500, {"error": f"close_all failed: {e}"})
            return

        if self.path == "/modify-position-sl":
            sl_price = body.get("price")
            idx = int(body.get("index") or 0)
            if sl_price is None:
                return self._reply(400, {"error": "missing 'price' field"})
            try: sl_price = float(sl_price)
            except: return self._reply(400, {"error": "bad price"})
            with _state["lock"]:
                page = _state["page"]
                if page is None:
                    return self._reply(503, {"error": "no page"})
                try:
                    result = tx.modify_position_sl(page, sl_price, position_index=idx)
                    self._reply(200, result)
                except Exception as e:
                    self._reply(500, {"error": f"modify_position_sl failed: {e}"})
            return

        self._reply(404, {"error": f"unknown POST {self.path}"})

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")


def main():
    p = argparse.ArgumentParser(prog="tv_playwright_daemon")
    p.add_argument("--port", type=int, default=DAEMON_PORT)
    p.add_argument("--mode", choices=("cdp", "persistent"), default="cdp",
                   help="cdp: attach to existing Chrome via CDP. "
                        "persistent: launch dedicated Chromium with /tmp/tv_pw_profile.")
    p.add_argument("--cdp", default="http://127.0.0.1:9222",
                   help="(cdp mode) CDP URL of existing Chrome.")
    p.add_argument("--headless", action="store_true",
                   help="(persistent mode) run Chromium headless. Default is windowed so operator can log in.")
    args = p.parse_args()

    if args.mode == "persistent":
        _init_session_persistent(headless=args.headless)
    else:
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
