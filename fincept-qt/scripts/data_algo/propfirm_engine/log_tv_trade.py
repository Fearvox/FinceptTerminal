"""
Propfirm v4 P-MFE — TradingView paper trade logger CLI.

Writes a TradingView paper trade into trade_journal.sqlite.
Supports two lifecycle calls:

  open  — create a trade row with entry filled, exit pending
  close — update a trade row with exit fields (triggers MFE follow-up
          when mfe_tracker.py runs next)

Usage:
  # Open a new paper trade (prints row id for follow-up close)
  python3 -m propfirm_engine.log_tv_trade open \
    --symbol USOIL --side long --entry 87.32 --sl 86.90 --tp 88.49 \
    --setup "willy_long_a" --regime "mixed_norm_vol" \
    --entry-ts "2026-04-20T09:15:00Z"

  # Close an existing trade by id
  python3 -m propfirm_engine.log_tv_trade close \
    --id 7 --exit 88.49 --exit-ts "2026-04-20T14:30:00Z" --reason tp

  # List open (no exit_ts) trades
  python3 -m propfirm_engine.log_tv_trade list-open

CLI-first per Nolan's global rules: every param as flag, `--format json`
for machine-readable output, no interactive prompts.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

from .trade_journal import DEFAULT_DB_PATH, init_schema, connect


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(s: str | None) -> str:
    if s is None or s == "now":
        return _now_utc()
    # Validate parseable
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise SystemExit(f"bad --entry-ts / --exit-ts: {e}")
    return s


def _session_hour_from_ts(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.hour


def cmd_open(args) -> int:
    db = init_schema(args.db)
    entry_ts = _parse_ts(args.entry_ts)
    side = args.side.lower()
    if side not in ("long", "short"):
        raise SystemExit("--side must be long or short")

    # Compute expected pnl_pct at TP (for reference, caller updates on close)
    row = {
        "symbol": args.symbol.upper(),
        "side": side,
        "entry_price": args.entry,
        "sl": args.sl,
        "tp": args.tp,
        "entry_ts": entry_ts,
        "setup_reason": args.setup,
        "regime": args.regime,
        "session_hour": _session_hour_from_ts(entry_ts),
    }
    cols = ", ".join(row.keys())
    placeholders = ", ".join(["?"] * len(row))
    with sqlite3.connect(db) as conn:
        cur = conn.execute(f"INSERT INTO trades ({cols}) VALUES ({placeholders})",
                           tuple(row.values()))
        conn.commit()
        new_id = cur.lastrowid

    if args.format == "json":
        print(json.dumps({"id": new_id, **row}, indent=2))
    else:
        risk_pct = abs(row["entry_price"] - row["sl"]) / row["entry_price"] * 100
        reward_pct = abs(row["tp"] - row["entry_price"]) / row["entry_price"] * 100
        rr = reward_pct / risk_pct if risk_pct > 0 else 0
        print(f"✓ opened trade #{new_id}: {row['symbol']} {side} @ {row['entry_price']}  "
              f"SL {row['sl']} ({risk_pct:.2f}%)  TP {row['tp']} ({reward_pct:.2f}%)  "
              f"R:R 1:{rr:.2f}")
        print(f"  setup: {args.setup}  regime: {args.regime}  ts: {entry_ts}")
        print(f"  → close with:  python3 -m propfirm_engine.log_tv_trade close "
              f"--id {new_id} --exit <PRICE> --reason <tp|sl|trail|regime_flip|manual>")
    return 0


def cmd_close(args) -> int:
    db = init_schema(args.db)
    exit_ts = _parse_ts(args.exit_ts)
    reason = args.reason
    valid = {"tp", "sl", "atr_trail", "regime_flip", "session_close", "manual"}
    if reason not in valid:
        raise SystemExit(f"--reason must be one of {sorted(valid)}")

    with connect(db) as conn:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (args.id,)).fetchone()
        if row is None:
            raise SystemExit(f"no trade with id={args.id}")
        if row["exit_ts"] is not None:
            raise SystemExit(f"trade #{args.id} already closed at {row['exit_ts']}")
        entry = row["entry_price"]
        side = row["side"]
        exit_price = args.exit
        if side == "long":
            pnl_pct = (exit_price / entry - 1) * 100
        else:
            pnl_pct = (1 - exit_price / entry) * 100

        conn.execute("""
            UPDATE trades SET exit_price=?, exit_ts=?, exit_reason=?, pnl_pct=?
            WHERE id=?
        """, (exit_price, exit_ts, reason, pnl_pct, args.id))
        conn.commit()

    if args.format == "json":
        print(json.dumps({"id": args.id, "exit_price": exit_price,
                          "exit_ts": exit_ts, "reason": reason,
                          "pnl_pct": pnl_pct}, indent=2))
    else:
        tag = "WIN" if pnl_pct > 0 else "LOSS" if pnl_pct < 0 else "FLAT"
        print(f"✓ closed #{args.id} [{tag}]: {row['symbol']} {side} "
              f"{entry} → {exit_price}  PnL {pnl_pct:+.2f}%  reason={reason}")
        print(f"  → MFE follow-up will fire on next mfe_tracker run.")
    return 0


def cmd_list_open(args) -> int:
    db = init_schema(args.db)
    with connect(db) as conn:
        rows = conn.execute("""
            SELECT id, symbol, side, entry_price, sl, tp, entry_ts, setup_reason, regime
            FROM trades WHERE exit_ts IS NULL ORDER BY id
        """).fetchall()
    out = [dict(r) for r in rows]
    if args.format == "json":
        print(json.dumps(out, indent=2))
    else:
        if not out:
            print("(no open trades)")
            return 0
        print(f"{'id':>3}  {'symbol':<8} {'side':<5} {'entry':>9} {'sl':>9} {'tp':>9} "
              f"{'setup':<18} {'regime':<16} {'entry_ts':<21}")
        for r in out:
            print(f"{r['id']:>3}  {r['symbol']:<8} {r['side']:<5} "
                  f"{r['entry_price']:>9.4f} {r['sl'] or 0:>9.4f} {r['tp'] or 0:>9.4f} "
                  f"{(r['setup_reason'] or ''):<18} {(r['regime'] or ''):<16} "
                  f"{r['entry_ts']:<21}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="log_tv_trade",
                                description="Log TradingView paper trades into the propfirm journal.")
    p.add_argument("--db", default=DEFAULT_DB_PATH, help="sqlite path (default: packaged trade_journal.sqlite)")
    p.add_argument("--format", choices=["text", "json"], default="text")
    sub = p.add_subparsers(dest="cmd", required=True)

    op = sub.add_parser("open", help="open a new paper trade row")
    op.add_argument("--symbol", required=True)
    op.add_argument("--side", required=True, choices=["long", "short", "LONG", "SHORT"])
    op.add_argument("--entry", type=float, required=True)
    op.add_argument("--sl", type=float, required=True)
    op.add_argument("--tp", type=float, required=True)
    op.add_argument("--setup", default="willy_signal", help="setup_reason label (e.g. willy_long_a)")
    op.add_argument("--regime", default="", help="regime label from Willy panel (e.g. mixed_norm_vol)")
    op.add_argument("--entry-ts", default="now", help="ISO UTC ts; default now")

    cl = sub.add_parser("close", help="close an existing trade by id")
    cl.add_argument("--id", type=int, required=True)
    cl.add_argument("--exit", type=float, required=True)
    cl.add_argument("--exit-ts", default="now")
    cl.add_argument("--reason", required=True,
                    help="tp | sl | atr_trail | regime_flip | session_close | manual")

    sub.add_parser("list-open", help="list trades with no exit_ts")

    args = p.parse_args()
    if args.cmd == "open":
        return cmd_open(args)
    if args.cmd == "close":
        return cmd_close(args)
    if args.cmd == "list-open":
        return cmd_list_open(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
