"""Run the propfirm TradingView webhook and local Fusion panel together."""
from __future__ import annotations

import argparse
import os
import sys
import threading
from http.server import HTTPServer

from .fusion_panel import DEFAULT_ALERT_LOG_PATH
from .fusion_panel_server import make_handler as make_panel_handler
from .trade_journal import DEFAULT_DB_PATH
from .tv_webhook import make_handler as make_webhook_handler


def _serve(server: HTTPServer):
    server.serve_forever(poll_interval=0.25)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="fusion_stack",
        description="Start propfirm webhook receiver + local Fusion Chat alert panel.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="webhook bind address; use 0.0.0.0 only behind a trusted tunnel")
    parser.add_argument("--port", type=int, default=5555, help="webhook port")
    parser.add_argument("--panel-host", default="127.0.0.1", help="panel bind address; keep local")
    parser.add_argument("--panel-port", type=int, default=5556, help="panel port for Fusion Chat iframe")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--alert-log", default=DEFAULT_ALERT_LOG_PATH)
    args = parser.parse_args()

    secret = os.environ.get("TV_WEBHOOK_SECRET", "")
    if not secret:
        print("ERROR: set TV_WEBHOOK_SECRET env var before starting the webhook lane.", file=sys.stderr)
        return 2

    webhook = HTTPServer((args.host, args.port), make_webhook_handler(secret, args.db, args.alert_log))
    panel = HTTPServer((args.panel_host, args.panel_port), make_panel_handler(args.alert_log))

    webhook_thread = threading.Thread(target=_serve, args=(webhook,), name="propfirm-tv-webhook", daemon=True)
    panel_thread = threading.Thread(target=_serve, args=(panel,), name="propfirm-fusion-panel", daemon=True)
    webhook_thread.start()
    panel_thread.start()

    print(f"▶ tv_webhook listening on http://{args.host}:{args.port}", flush=True)
    print("  POST /tv-signal?secret=<secret>  (TradingView alerts)")
    print(f"▶ fusion panel listening on http://{args.panel_host}:{args.panel_port}/fusion-panel")
    print("  GET /alerts  (sanitized local feed)")
    print(f"  secret length: {len(secret)} chars")
    print("  mode: webhook writes journal/feed; panel is read-only; no trade execution")

    try:
        while webhook_thread.is_alive() and panel_thread.is_alive():
            webhook_thread.join(timeout=0.5)
            panel_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\n▶ shutting down propfirm fusion stack")
    finally:
        webhook.shutdown()
        panel.shutdown()
        webhook.server_close()
        panel.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
