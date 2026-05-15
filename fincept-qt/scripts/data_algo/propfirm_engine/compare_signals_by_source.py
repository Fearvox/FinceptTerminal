#!/usr/bin/env python3
"""Compare signal performance by source — pulled from trade_journal.sqlite.

Groups entries by the `source` token at the start of `setup_reason`
(e.g. willy, smc, fincept), computes per-source counts, win rate,
R-multiple, exit-reason breakdown, and activity window.

Usage:
  compare_signals_by_source.py                 # full history
  compare_signals_by_source.py --since 24h     # last 24 hours
  compare_signals_by_source.py --since 7d
  compare_signals_by_source.py --symbol BTCUSDC.P
  compare_signals_by_source.py --format json   # for piping into jq

Designed CLI-first: zero interactive prompts, all params as flags.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_DEFAULT = Path(__file__).parent / "trade_journal.sqlite"


def parse_source(setup_reason: str | None) -> str:
    if not setup_reason:
        return "unknown"
    return setup_reason.split("_", 1)[0]


def parse_since(s: str | None) -> str | None:
    """Convert '24h' / '7d' / '30m' to absolute UTC ISO timestamp."""
    if not s:
        return None
    s = s.strip().lower()
    unit = s[-1]
    try:
        n = int(s[:-1])
    except ValueError:
        raise SystemExit(f"bad --since value '{s}', expected NN[h|d|m]")
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "m": timedelta(minutes=n)}.get(unit)
    if delta is None:
        raise SystemExit(f"bad --since unit '{unit}', expected h / d / m")
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_rows(db: Path, since: str | None, symbol: str | None) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    where, params = [], []
    if since:
        where.append("entry_ts >= ?")
        params.append(since)
    if symbol:
        where.append("symbol LIKE ?")
        params.append(f"%{symbol}%")
    sql = "SELECT * FROM trades"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id ASC"
    rows = [dict(r) for r in conn.execute(sql, params)]
    conn.close()
    return rows


def r_multiple(t: dict) -> float | None:
    """Realized R: pnl_pct / risk_pct (signed). Returns None if undefined."""
    if t["pnl_pct"] is None or t["sl"] is None or t["entry_price"] is None:
        return None
    entry = t["entry_price"]
    sl = t["sl"]
    if t["side"] == "long":
        risk_pct = (entry - sl) / entry * 100
    else:
        risk_pct = (sl - entry) / entry * 100
    if risk_pct <= 0:
        return None
    return t["pnl_pct"] / risk_pct


def summarize(rows: list[dict]) -> list[dict]:
    by_src: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_src[parse_source(r["setup_reason"])].append(r)

    out = []
    for src in sorted(by_src.keys(), key=lambda k: -len(by_src[k])):
        ts = by_src[src]
        closed = [t for t in ts if t["exit_ts"] is not None]
        opens = [t for t in ts if t["exit_ts"] is None]

        sl_hits = [t for t in closed if t["exit_reason"] == "sl"]
        tp_hits = [t for t in closed if t["exit_reason"] == "tp"]
        trail_hits = [t for t in closed if t["exit_reason"] == "atr_trail"]
        wins = [t for t in closed if (t["pnl_pct"] or 0) > 0]
        rs = [r for r in (r_multiple(t) for t in closed) if r is not None]

        out.append({
            "source": src,
            "n_total": len(ts),
            "n_closed": len(closed),
            "n_open": len(opens),
            "win_pct": (100.0 * len(wins) / len(closed)) if closed else None,
            "avg_r": (sum(rs) / len(rs)) if rs else None,
            "sum_r": sum(rs) if rs else 0.0,
            "sl_pct": (100.0 * len(sl_hits) / len(closed)) if closed else None,
            "tp_pct": (100.0 * len(tp_hits) / len(closed)) if closed else None,
            "trail_pct": (100.0 * len(trail_hits) / len(closed)) if closed else None,
            "first_ts": ts[0]["entry_ts"],
            "last_ts": ts[-1]["entry_ts"],
        })
    return out


def fmt_pct(v: float | None) -> str:
    return f"{v:5.1f}%" if v is not None else "  —  "


def fmt_r(v: float | None) -> str:
    return f"{v:+6.2f}R" if v is not None else "   —  "


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("(no data)")
        return
    header = f"{'source':<10} {'N':>4} {'closed':>6} {'open':>5} {'win%':>6} {'avgR':>7} {'sumR':>8} {'sl%':>6} {'tp%':>6} {'trail%':>6}  {'first':>20}  {'last':>20}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['source']:<10} "
            f"{r['n_total']:>4} "
            f"{r['n_closed']:>6} "
            f"{r['n_open']:>5} "
            f"{fmt_pct(r['win_pct'])} "
            f"{fmt_r(r['avg_r'])} "
            f"{r['sum_r']:>+7.2f}R "
            f"{fmt_pct(r['sl_pct'])} "
            f"{fmt_pct(r['tp_pct'])} "
            f"{fmt_pct(r['trail_pct'])}  "
            f"{(r['first_ts'] or '')[:19]:>20}  "
            f"{(r['last_ts'] or '')[:19]:>20}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DB_DEFAULT, help="path to trade_journal.sqlite")
    ap.add_argument("--since", default=None, help="window (e.g. 24h, 7d, 30m). Default: all history.")
    ap.add_argument("--symbol", default=None, help="filter by symbol substring (e.g. BTCUSDC, GOLD)")
    ap.add_argument("--format", choices=("table", "json"), default="table")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"ERROR: {args.db} not found", file=sys.stderr)
        return 1

    since = parse_since(args.since)
    rows = fetch_rows(args.db, since=since, symbol=args.symbol)
    summary = summarize(rows)

    if args.format == "json":
        print(json.dumps(summary, indent=2))
    else:
        scope = []
        if args.since:
            scope.append(f"since={args.since} ({since})")
        if args.symbol:
            scope.append(f"symbol~{args.symbol}")
        scope.append(f"total_rows={len(rows)}")
        print("scope: " + ", ".join(scope))
        print()
        print_table(summary)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
