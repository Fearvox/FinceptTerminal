#!/usr/bin/env python3
"""Retroactively close willy/smc open trades using the journal as a tick stream.

For every open trade in `trade_journal.sqlite`, replay later same-symbol
entries (any source) and use their `entry_price` as price ticks. If any
tick breaches the open trade's SL or hits TP, close at that level with
exit_ts = the tick's entry_ts.

This is the historical counterpart to the inline _sweep_open_trades_at_price()
hook in tv_webhook.py: same logic, applied to data that pre-dated the hook.

CLI-first; --dry-run is the default for safety.

Usage:
  backfill_open_exits.py                     # dry-run: prints projected closes
  backfill_open_exits.py --apply             # actually write to db
  backfill_open_exits.py --source willy      # only consider willy as candidates
  backfill_open_exits.py --apply --quiet     # no per-trade detail
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB_DEFAULT = Path(__file__).parent / "trade_journal.sqlite"


def _source_prefix(setup_reason: str | None) -> str:
    if not setup_reason:
        return "unknown"
    return setup_reason.split("_", 1)[0]


def _check_breach(side: str, sl: float | None, tp: float | None,
                  tick_price: float) -> tuple[str | None, float | None]:
    """Return (reason, exit_price) if tick_price breaches SL or hits TP, else (None, None)."""
    if side == "long":
        if sl is not None and tick_price <= sl:
            return "sl", sl
        if tp is not None and tick_price >= tp:
            return "tp", tp
    else:  # short
        if sl is not None and tick_price >= sl:
            return "sl", sl
        if tp is not None and tick_price <= tp:
            return "tp", tp
    return None, None


def replay(db_path: Path, source_filter: str | None = None) -> list[dict]:
    """Walk the journal in time order, simulating ticks. Returns proposed closes."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM trades ORDER BY id ASC"))
    conn.close()

    open_by_symbol: dict[str, list[dict]] = defaultdict(list)
    proposals: list[dict] = []

    for raw in rows:
        r = dict(raw)
        sym = r["symbol"]
        tick_price = r["entry_price"]
        tick_ts = r["entry_ts"]

        # First, use this row's price as a tick check for any currently-open
        # trade on the same symbol. Process oldest open first.
        survivors: list[dict] = []
        for ot in open_by_symbol[sym]:
            reason, exit_price = _check_breach(ot["side"], ot["sl"], ot["tp"], tick_price)
            if reason is None:
                survivors.append(ot)
                continue
            # Don't apply filter to OPEN candidates by source — closes are universal.
            # But we record the source so summary can filter on it.
            entry = ot["entry_price"]
            if ot["side"] == "long":
                pnl_pct = (exit_price / entry - 1) * 100
            else:
                pnl_pct = (1 - exit_price / entry) * 100
            proposals.append({
                "trade_id": ot["id"],
                "symbol": sym,
                "side": ot["side"],
                "source": _source_prefix(ot["setup_reason"]),
                "entry_price": entry,
                "entry_ts": ot["entry_ts"],
                "exit_price": exit_price,
                "exit_ts": tick_ts,
                "exit_reason": reason,
                "pnl_pct": pnl_pct,
                "trigger_trade_id": r["id"],
                "trigger_source": _source_prefix(r["setup_reason"]),
            })
        open_by_symbol[sym] = survivors

        # Then track this row as a new open (only if it's actually open and
        # passes the source filter — closed rows shouldn't go into the pool).
        if r["exit_ts"] is None:
            if source_filter is None or _source_prefix(r["setup_reason"]) == source_filter:
                open_by_symbol[sym].append(r)

    return proposals


def apply_closes(db_path: Path, proposals: list[dict]) -> int:
    """Write proposed closes to db. Idempotent: only updates rows still open."""
    n = 0
    conn = sqlite3.connect(db_path)
    try:
        for p in proposals:
            cur = conn.execute("""
                UPDATE trades
                SET exit_price=?, exit_ts=?, exit_reason=?, pnl_pct=?
                WHERE id=? AND exit_ts IS NULL
            """, (p["exit_price"], p["exit_ts"], p["exit_reason"], p["pnl_pct"], p["trade_id"]))
            n += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return n


def summarize(proposals: list[dict]) -> None:
    if not proposals:
        print("(no proposed closes)")
        return
    by_src: dict[str, dict] = defaultdict(lambda: {"n": 0, "sl": 0, "tp": 0, "sumR_proxy": 0.0})
    for p in proposals:
        s = by_src[p["source"]]
        s["n"] += 1
        s[p["exit_reason"]] += 1
        s["sumR_proxy"] += p["pnl_pct"]  # rough R proxy w/o per-trade risk normalization
    print(f"{'source':<12} {'closes':>7} {'sl':>5} {'tp':>5} {'sum_pnl%':>10}")
    print("-" * 44)
    for src in sorted(by_src.keys(), key=lambda k: -by_src[k]["n"]):
        s = by_src[src]
        print(f"{src:<12} {s['n']:>7} {s['sl']:>5} {s['tp']:>5} {s['sumR_proxy']:>+9.2f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DB_DEFAULT)
    ap.add_argument("--apply", action="store_true", help="Write to db (default: dry-run)")
    ap.add_argument("--source", default=None, help="Only consider this source as open-candidate")
    ap.add_argument("--quiet", action="store_true", help="Suppress per-trade detail; summary only")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"ERROR: {args.db} not found", file=sys.stderr)
        return 1

    proposals = replay(args.db, source_filter=args.source)
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}  Source filter: {args.source or '(all)'}")
    print(f"Proposed closes: {len(proposals)}")
    print()

    if not args.quiet and proposals:
        print(f"{'#':>5} {'source':<10} {'symbol':<28} {'side':<6} {'reason':<6} "
              f"{'entry':>10} {'exit':>10} {'pnl%':>7}  {'entry_ts':<20} -> {'exit_ts':<20}")
        for p in proposals[:200]:
            print(f"{p['trade_id']:>5} {p['source']:<10} {p['symbol']:<28} {p['side']:<6} "
                  f"{p['exit_reason']:<6} {p['entry_price']:>10.4f} {p['exit_price']:>10.4f} "
                  f"{p['pnl_pct']:>+6.2f}%  {p['entry_ts'][:19]:<20} -> {p['exit_ts'][:19]:<20}")
        if len(proposals) > 200:
            print(f"... ({len(proposals) - 200} more rows omitted; rerun with --quiet for summary only)")
        print()

    summarize(proposals)
    print()

    if args.apply and proposals:
        n = apply_closes(args.db, proposals)
        print(f"✓ Applied {n} closes to {args.db}")
    elif proposals:
        print("(dry-run — pass --apply to write)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
