"""
Propfirm v4 P-MFE — TradingView alert webhook receiver.

Tiny stdlib HTTP server that accepts POST /tv-signal with a JSON
payload fired by a TradingView alert on the WillyAlgoTrader script.
Each validated alert creates an open trade row in trade_journal.sqlite.

See README.tv_integration.md for the end-to-end setup (Pine template,
TV alert config, cloudflared tunnel, environment variables).

Expected JSON payload (fields marked * required):
  {
    "secret":   "<matches $TV_WEBHOOK_SECRET env>",    *
    "symbol":   "USOIL",                                 *
    "side":     "long" | "short",                        *
    "entry":    87.32,                                   *
    "sl":       86.90,                                   *
    "tp":       88.49,                                   *
    "setup":    "willy_long_a"  (default willy_signal)
    "regime":   "mixed_norm_vol",
    "timeframe":"1h",
    "tqi":      0.37,
    "ts_utc":   "2026-04-20T09:15:00Z"  (default: now)
  }

Responds 200 with {"id": N, ...} on success, 400/401 on validation error.

Usage:
    export TV_WEBHOOK_SECRET=<generate a random 32-char string>
    python3 -m propfirm_engine.tv_webhook              # listens on :5555
    python3 -m propfirm_engine.tv_webhook --port 8080

Security note: exposed via cloudflared tunnel or similar. A shared
secret in the payload is the bare minimum — rotate if leaked. Consider
adding IP allow-list for TradingView alert sender IPs in production.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from .log_tv_trade import open_trade
from .trade_journal import DEFAULT_DB_PATH


REQUIRED = ("symbol", "side", "entry", "sl", "tp")


class TVWebhookHandler(BaseHTTPRequestHandler):
    # Injected by TVWebhookServer — set per-instance via constructor
    secret: str = ""
    db_path: str = DEFAULT_DB_PATH

    def _reply(self, code: int, body: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        data = json.dumps(body).encode()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._reply(200, {"ok": True, "ts": datetime.now(timezone.utc).isoformat()})
            return
        self._reply(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/tv-signal":
            self._reply(404, {"error": "expected POST /tv-signal"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode(errors="replace")

        # TradingView sometimes wraps the JSON body in extra whitespace or
        # sends plain text — try JSON, then fall back to a lenient parse.
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            self._reply(400, {"error": f"invalid json: {e}", "raw": raw[:200]})
            return
        if not isinstance(payload, dict):
            self._reply(400, {"error": "payload must be a json object"})
            return

        # Secret check (constant-time not needed; local-only exposure)
        if not self.secret:
            self._reply(500, {"error": "server started without TV_WEBHOOK_SECRET"})
            return
        if payload.get("secret") != self.secret:
            self._reply(401, {"error": "bad or missing secret"})
            self.log_message("rejected request (secret mismatch) from %s", self.client_address[0])
            return

        missing = [k for k in REQUIRED if k not in payload]
        if missing:
            self._reply(400, {"error": f"missing required fields: {missing}"})
            return

        try:
            row = open_trade(
                db=self.db_path,
                symbol=str(payload["symbol"]),
                side=str(payload["side"]),
                entry=float(payload["entry"]),
                sl=float(payload["sl"]),
                tp=float(payload["tp"]),
                setup=str(payload.get("setup", "willy_signal")),
                regime=str(payload.get("regime", "")),
                entry_ts=payload.get("ts_utc"),
            )
        except (ValueError, TypeError) as e:
            self._reply(400, {"error": f"bad payload: {e}"})
            return
        except Exception as e:  # pragma: no cover
            self._reply(500, {"error": f"{type(e).__name__}: {e}"})
            return

        # Include extra alert metadata so downstream tools can trace back
        row["tqi"] = payload.get("tqi")
        row["q_strength"] = payload.get("q_strength")
        row["timeframe"] = payload.get("timeframe")
        self._reply(200, row)
        self.log_message("logged trade #%s %s %s @ %s setup=%s",
                         row["id"], row["symbol"], row["side"],
                         row["entry_price"], row["setup_reason"])

    # Quieter access log — default is noisy for each request
    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")


def make_handler(secret: str, db_path: str):
    class _H(TVWebhookHandler):
        pass
    _H.secret = secret
    _H.db_path = db_path
    return _H


def main() -> int:
    p = argparse.ArgumentParser(prog="tv_webhook",
                                description="Local webhook receiver for TradingView alerts.")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (default 127.0.0.1; override to 0.0.0.0 if you "
                        "expose directly — NOT recommended; prefer cloudflared tunnel)")
    p.add_argument("--port", type=int, default=5555)
    p.add_argument("--db", default=DEFAULT_DB_PATH)
    args = p.parse_args()

    secret = os.environ.get("TV_WEBHOOK_SECRET", "")
    if not secret:
        print("ERROR: set TV_WEBHOOK_SECRET env var (use a 32-char random string).",
              file=sys.stderr)
        return 2

    handler_cls = make_handler(secret, args.db)
    server = HTTPServer((args.host, args.port), handler_cls)
    print(f"▶ tv_webhook listening on http://{args.host}:{args.port}  "
          f"(POST /tv-signal, GET /health)", flush=True)
    print(f"  db: {args.db}")
    print(f"  secret length: {len(secret)} chars")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n▶ shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
