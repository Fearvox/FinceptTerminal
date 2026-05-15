"""
Propfirm v4 P-MFE — TradingView alert webhook receiver.

Accepts TradingView's `Webhook JSON Format` alerts from the open-source
WillyAlgoTrader Pine scripts (SATS Self-Aware Trend System v1.9.0 +
Precision Sniper v1.2.2). No Pine-side editing required — just enable
the "Webhook JSON Format" input in each script and point the TV alert
at this server's URL with `?secret=...` appended.

Endpoint (single): POST /tv-signal?secret=<TV_WEBHOOK_SECRET>
Health:              GET  /health

Entry payload (from both scripts, detected by `action` field):
    SATS:           {"action":"buy","ticker":"USOIL","tf":"1h","price":87.32,
                     "sl":86.9,"tp1":87.82,"tp2":88.32,"tp3":88.82,
                     "score":85,"tqi":0.50,"tp_mode":"fixed","tp_scale":1.00}
    Precision Sniper: {"action":"buy","ticker":"USOIL","price":87.32,
                       "sl":86.9,"tp1":87.82,"tp2":88.32,"tp3":88.82,
                       "score":6.5,"grade":"A","preset":"Default","tf":"1h"}

Exit payload (both, detected by `event` field):
    {"event":"tp1_hit|tp2_hit|tp3_hit|sl_hit","ticker":"USOIL","price":87.82}

Dispatch policy:
    action=buy      → open_trade(side=long, tp=tp1); extras in setup_reason
    action=sell     → open_trade(side=short, tp=tp1); extras in setup_reason
    event=tp{1,2,3}_hit → milestone (stderr log); NO journal update (Willy
                       keeps position open and trails SL)
    event=sl_hit    → close most-recent open trade on ticker with
                       reason=atr_trail if any tp milestone was seen,
                       else reason=sl

Usage:
    export TV_WEBHOOK_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    python3 -m propfirm_engine.tv_webhook              # :5555
    python3 -m propfirm_engine.tv_webhook --port 8080
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .log_tv_trade import open_trade
from . import notify_signal
from .trade_journal import DEFAULT_DB_PATH, connect


ENTRY_REQUIRED = ("action", "ticker", "price", "sl", "tp1")
EXIT_REQUIRED = ("event", "ticker", "price")
VALID_EVENTS = {"tp1_hit", "tp2_hit", "tp3_hit", "sl_hit"}
VALID_ACTIONS = {"buy", "sell"}


# In-memory milestone tracker keyed by trade id. Ephemeral — restart
# loses state, which is fine: a restart in the middle of an open paper
# trade is rare, and sl_hit semantics still default to "sl" if no
# milestone is observed.
_tp_milestones: dict[int, set[str]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_open_trade_for_ticker(db: str, ticker: str) -> dict | None:
    """Return the most recent open (exit_ts IS NULL) row for this ticker."""
    with connect(db) as conn:
        row = conn.execute("""
            SELECT * FROM trades
            WHERE symbol = ? AND exit_ts IS NULL
            ORDER BY id DESC LIMIT 1
        """, (ticker.upper(),)).fetchone()
    return dict(row) if row else None


def _close_trade(db: str, trade_id: int, exit_price: float,
                 reason: str, exit_ts: str | None = None) -> dict:
    exit_ts = exit_ts or _now_iso()
    with connect(db) as conn:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            raise ValueError(f"no trade id={trade_id}")
        entry = row["entry_price"]
        side = row["side"]
        if side == "long":
            pnl_pct = (exit_price / entry - 1) * 100
        else:
            pnl_pct = (1 - exit_price / entry) * 100
        conn.execute("""
            UPDATE trades SET exit_price=?, exit_ts=?, exit_reason=?, pnl_pct=?
            WHERE id=?
        """, (exit_price, exit_ts, reason, pnl_pct, trade_id))
        conn.commit()
    return {"id": trade_id, "exit_price": exit_price, "exit_ts": exit_ts,
            "exit_reason": reason, "pnl_pct": pnl_pct}


def _setup_reason_from_entry(p: dict) -> str:
    """Build a rich, grep-friendly setup_reason from alert fields.

    Prefix is the `source` field from the payload (lowercased, sanitized),
    defaulting to 'willy' for backward compat with the original SATS /
    Precision Sniper scripts that don't send `source`. Downstream the
    setup_reason prefix is parsed back as source for per-script
    journal analytics (see compare_signals_by_source.py).
    """
    action = str(p.get("action", "?"))
    src = str(p.get("source") or "willy").lower()
    # Keep prefix grep-friendly: alnum + underscore only.
    src = "".join(c for c in src if c.isalnum() or c == "_") or "willy"
    parts = [f"{src}_{action}"]
    if "grade" in p:
        parts.append(f"grade{p['grade']}")
    if "tqi" in p and p["tqi"] is not None:
        parts.append(f"tqi{p['tqi']}")
    if "score" in p and p["score"] is not None:
        parts.append(f"score{p['score']}")
    if "preset" in p:
        parts.append(f"preset{p['preset']}")
    if "tp_mode" in p:
        parts.append(f"tpmode{p['tp_mode']}")
    if "tp_scale" in p and p.get("tp_mode") == "dynamic":
        parts.append(f"scale{p['tp_scale']}")
    return "_".join(str(x) for x in parts)


def _regime_from_entry(p: dict) -> str:
    """Best-effort regime label from Willy metadata (both scripts
    compute a regime internally but neither serialises it explicitly;
    tp_mode is the closest proxy in SATS)."""
    if "preset" in p:
        return str(p["preset"])
    if "tp_mode" in p:
        return f"tpmode_{p['tp_mode']}"
    return ""


class TVWebhookHandler(BaseHTTPRequestHandler):
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

    def _check_secret(self) -> bool:
        """Secret is passed via URL query `?secret=...` — TV webhooks
        don't support custom headers, and body-based secrets require
        Pine edits. Query-string is the minimum-friction path."""
        if not self.secret:
            return False
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        supplied = (q.get("secret") or [""])[0]
        return supplied == self.secret

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._reply(200, {"ok": True, "ts": _now_iso()})
            return
        self._reply(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/tv-signal":
            self._reply(404, {"error": "expected POST /tv-signal?secret=..."})
            return
        if not self.secret:
            self._reply(500, {"error": "server started without TV_WEBHOOK_SECRET"})
            return
        if not self._check_secret():
            self._reply(401, {"error": "bad or missing ?secret=..."})
            self.log_message("rejected (secret) from %s", self.client_address[0])
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode(errors="replace")
        # P5b: log raw incoming body so 400s can be diagnosed
        self.log_message("RECV body=%s", raw[:500])
        # Willy Pine template emits .50 instead of 0.50 (no leading zero).
        # Repair before parse: insert 0 between separators and a bare decimal point.
        import re as _re
        repaired = _re.sub(r'(?<=[:,\[\s])(-?)\.(\d)', r'\g<1>0.\2', raw)
        if repaired != raw:
            self.log_message("REPAIRED leading-decimal: %s", repaired[:200])
        try:
            payload = json.loads(repaired)
        except json.JSONDecodeError as e:
            self.log_message("REJECT invalid-json: %s", str(e)[:200])
            self._reply(400, {"error": f"invalid json: {e}", "raw": raw[:200]})
            return
        if not isinstance(payload, dict):
            self.log_message("REJECT not-dict payload-type=%s", type(payload).__name__)
            self._reply(400, {"error": "payload must be a json object"})
            return

        # Dispatch on which key identifies the message type
        if "action" in payload:
            return self._handle_entry(payload)
        if "event" in payload:
            return self._handle_exit(payload)
        # Neither action nor event — log keys for diagnosis
        self.log_message("REJECT no-dispatch-key keys=%s", list(payload.keys()))
        self._reply(400, {"error": "payload must contain 'action' or 'event' field", "keys": list(payload.keys())})
        return
        self._reply(400, {"error": "payload must contain 'action' (entry) or 'event' (exit)"})

    def _handle_entry(self, p: dict):
        # P5b: SMC source uses relaxed schema (no sl/tp1 required — synthesized 1% SL / 2% TP)
        is_smc = str(p.get("source") or "").lower() == "smc"
        if is_smc:
            smc_required = ("action", "ticker", "price")
            missing = [k for k in smc_required if k not in p]
            if not missing:
                # synthesize sl + tp1 (1% SL, 2% TP for 1:2 R:R)
                try:
                    price = float(p["price"])
                    action_lc = str(p["action"]).lower()
                    if "sl" not in p or p.get("sl") is None:
                        p["sl"] = price * (0.99 if action_lc == "buy" else 1.01)
                    if "tp1" not in p or p.get("tp1") is None:
                        p["tp1"] = price * (1.02 if action_lc == "buy" else 0.98)
                    # SMC gets high conviction score+tqi to bypass quality filter
                    if "score" not in p: p["score"] = 50
                    if "tqi" not in p: p["tqi"] = 0.7
                except Exception:
                    pass
        else:
            missing = [k for k in ENTRY_REQUIRED if k not in p]
        if missing:
            self.log_message("REJECT entry-missing-fields %s have=%s source=%s", missing, list(p.keys()), p.get("source"))
            self._reply(400, {"error": f"missing required fields: {missing}", "received_keys": list(p.keys())})
            return
        action = str(p["action"]).lower()
        if action not in VALID_ACTIONS:
            self.log_message("REJECT entry-bad-action %s", action)
            self._reply(400, {"error": f"action must be one of {sorted(VALID_ACTIONS)}"})
            return

        side = "long" if action == "buy" else "short"
        try:
            row = open_trade(
                db=self.db_path,
                symbol=str(p["ticker"]),
                side=side,
                entry=float(p["price"]),
                sl=float(p["sl"]),
                tp=float(p["tp1"]),      # tp1 is the nearest realistic paper fill target
                setup=_setup_reason_from_entry(p),
                regime=_regime_from_entry(p),
                entry_ts=None,           # webhook receives near-realtime; use server now
            )
            if is_smc:
                self.log_message("SMC_ENTRY #%s %s event=%s synthesized sl=%s tp=%s",
                                 row["id"], side, p.get("event"), p["sl"], p["tp1"])
        except (ValueError, TypeError) as e:
            self._reply(400, {"error": f"bad payload: {e}"})
            return

        # Attach non-schema extras so the HTTP response carries full
        # context for downstream viewers (they're not stored in db).
        row["tp2"] = p.get("tp2")
        row["tp3"] = p.get("tp3")
        row["score"] = p.get("score")
        row["tqi"] = p.get("tqi")
        row["grade"] = p.get("grade")
        row["preset"] = p.get("preset")
        row["tp_mode"] = p.get("tp_mode")
        row["tp_scale"] = p.get("tp_scale")
        row["timeframe"] = p.get("tf")

        # Reset milestone tracker for the new trade id
        _tp_milestones[row["id"]] = set()

        self._reply(200, {"kind": "entry_opened", **row})
        self.log_message("ENTRY #%s %s %s @ %s tp=%s setup=%s",
                         row["id"], row["symbol"], side,
                         row["entry_price"], row["tp"], row["setup_reason"])

        # P5b manual-click router: notify operator + pbcopy trade plan
        notify_result = None
        try:
            notify_result = notify_signal.notify_entry(
                symbol=row["symbol"],
                side=side,
                entry=float(row["entry_price"]),
                sl=float(row["sl"]),
                tp=float(row["tp"]),
                setup=row["setup_reason"],
                row_id=row["id"],
            )
        except Exception as e:  # pragma: no cover — notify failure must not break webhook
            self.log_message("NOTIFY_ENTRY_FAILED #%s: %s", row["id"], e)

        # P5b quality filter — only fire daemon on high-conviction signals.
        # Default thresholds: score>=30 AND tqi>=0.4 (matches dogfood 12.2% pass rate).
        # Override via P5B_MIN_SCORE / P5B_MIN_TQI env.
        _min_score = float(os.environ.get("P5B_MIN_SCORE", "30"))
        _min_tqi = float(os.environ.get("P5B_MIN_TQI", "0.4"))
        _signal_score = float(p.get("score") or 0)
        _signal_tqi = float(p.get("tqi") or 0)
        _quality_ok = _signal_score >= _min_score and _signal_tqi >= _min_tqi

        # P5b daemon auto-exec: only fire if notify wasn't rate-limited / cap-aborted
        # AND quality filter passes AND env P5B_AUTOEXEC_ENABLED=1
        if (notify_result and not notify_result.get("skipped")
                and _quality_ok
                and os.environ.get("P5B_AUTOEXEC_ENABLED") == "1"):
            try:
                import urllib.request as _urlreq
                trade_body = json.dumps({
                    "action": "buy" if side == "long" else "sell",
                    "ticker": row["symbol"],
                    "qty": 1,
                    "sl": float(p.get("sl") or row["sl"]) if (p.get("sl") or row["sl"]) else None,
                    "tp": float(p.get("tp1") or row["tp"]) if (p.get("tp1") or row["tp"]) else None,
                }).encode()
                req = _urlreq.Request(
                    "http://127.0.0.1:5556/trade",
                    data=trade_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                # fire-and-forget with short timeout — don't block webhook
                _urlreq.urlopen(req, timeout=15)
                self.log_message("DAEMON_TRADE_FIRED #%s %s sl=%s tp=%s", row["id"], side,
                                 p.get("sl") or row["sl"], p.get("tp1") or row["tp"])
            except Exception as e:  # pragma: no cover — daemon down must not break webhook
                self.log_message("DAEMON_TRADE_FAILED #%s: %s", row["id"], e)
        elif notify_result and not notify_result.get("skipped"):
            if os.environ.get("P5B_AUTOEXEC_ENABLED") != "1":
                self.log_message("DAEMON_TRADE_GATED #%s (P5B_AUTOEXEC_ENABLED!=1)", row["id"])
            elif not _quality_ok:
                self.log_message("DAEMON_TRADE_QUALITY_GATED #%s score=%s tqi=%s (need>=%s/%s)",
                                 row["id"], _signal_score, _signal_tqi, _min_score, _min_tqi)

    def _handle_exit(self, p: dict):
        missing = [k for k in EXIT_REQUIRED if k not in p]
        if missing:
            self._reply(400, {"error": f"missing required fields: {missing}"})
            return
        event = str(p["event"])
        if event not in VALID_EVENTS:
            self._reply(400, {"error": f"event must be one of {sorted(VALID_EVENTS)}"})
            return

        ticker = str(p["ticker"]).upper()
        price = float(p["price"])
        open_row = _find_open_trade_for_ticker(self.db_path, ticker)

        if open_row is None:
            # No matching open trade — log and return 200 so TV doesn't retry
            self._reply(200, {"kind": "ignored",
                              "reason": f"no open trade for {ticker}",
                              "event": event})
            self.log_message("IGNORE %s %s (no open trade)", event, ticker)
            return

        trade_id = open_row["id"]
        milestones = _tp_milestones.setdefault(trade_id, set())

        if event in {"tp1_hit", "tp2_hit", "tp3_hit"}:
            milestones.add(event)
            # Willy keeps the position open on TP milestones; just log.
            # tp3_hit we could auto-close since trail would catch it on
            # next bar anyway, but to preserve exact paper-fill semantics
            # we let the follow-up sl_hit (at trailed level) close the row.
            self._reply(200, {"kind": "milestone",
                              "trade_id": trade_id,
                              "event": event,
                              "milestones": sorted(milestones)})
            self.log_message("MILESTONE #%s %s @ %s (milestones=%s)",
                             trade_id, event, price, sorted(milestones))
            return

        if event == "sl_hit":
            # If any tp milestone was hit before this sl, the "sl" is
            # actually the trailed stop firing — closer to atr_trail
            # semantics than raw sl.
            had_milestone = any(m in milestones for m in
                                ("tp1_hit", "tp2_hit", "tp3_hit"))
            reason = "atr_trail" if had_milestone else "sl"
            try:
                closed = _close_trade(self.db_path, trade_id, price, reason)
            except Exception as e:  # pragma: no cover
                self._reply(500, {"error": f"close failed: {e}"})
                return
            # Pop milestone state
            _tp_milestones.pop(trade_id, None)
            closed["milestones_before_exit"] = sorted(milestones)
            self._reply(200, {"kind": "exit_closed", **closed})
            self.log_message("EXIT #%s %s %s @ %s reason=%s pnl=%+.2f%%",
                             trade_id, ticker, event, price, reason,
                             closed["pnl_pct"])
            try:
                notify_signal.notify_exit(
                    symbol=ticker,
                    exit_reason=reason,
                    pnl_pct=closed.get("pnl_pct"),
                    row_id=trade_id,
                )
            except Exception as e:  # pragma: no cover
                self.log_message("NOTIFY_EXIT_FAILED #%s: %s", trade_id, e)
            return

        # Unreachable, but defensive
        self._reply(400, {"error": f"unhandled event: {event}"})

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
                                description="Local webhook receiver for TV WillyAlgoTrader alerts.")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (default 127.0.0.1; use 0.0.0.0 only "
                        "behind a tunnel you trust)")
    p.add_argument("--port", type=int, default=5555)
    p.add_argument("--db", default=DEFAULT_DB_PATH)
    args = p.parse_args()

    secret = os.environ.get("TV_WEBHOOK_SECRET", "")
    if not secret:
        print("ERROR: set TV_WEBHOOK_SECRET env var (use a 32-char random string).",
              file=sys.stderr)
        return 2

    server = HTTPServer((args.host, args.port), make_handler(secret, args.db))
    print(f"▶ tv_webhook listening on http://{args.host}:{args.port}", flush=True)
    print(f"  POST /tv-signal?secret=<secret>  (entry + exit; detected by `action`/`event`)")
    print(f"  GET  /health")
    print(f"  db: {args.db}")
    print(f"  secret length: {len(secret)} chars")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n▶ shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
