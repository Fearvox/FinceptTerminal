"""
Propfirm v4 P-MFE — post-exit MFE/MAE follow-up tracker.

For every closed trade with empty mfe_100 column, pull the bars that
came AFTER exit_ts from Yahoo/Binance (up to +100 bars), compute the
MFE and MAE at offsets +10, +50, +100, and persist back into the
trade_journal columns.

MFE (Maximum Favorable Excursion) answers: "how much further did this
move go in the direction I was betting on, *after* I exited?" Big MFE
on winners means we exited too early (leaving money on the table).

MAE (Maximum Adverse Excursion) answers: "how much did it go against
me *after* I exited?" Big MAE means the exit protected us from
additional drawdown (good).

Both are reported in R units (multiples of the initial per-trade risk,
i.e. |entry − sl|). If sl is missing, falls back to % of entry price.

Usage:
  python3 -m propfirm_engine.mfe_tracker           # follow up all pending
  python3 -m propfirm_engine.mfe_tracker --dry-run # report, don't write
  python3 -m propfirm_engine.mfe_tracker --id 7    # single trade only
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

from .trade_journal import DEFAULT_DB_PATH, connect
from .vendor.regime_dual_engine import fetch_any


# ── Symbol routing to bar source ─────────────────────────────────────────
# The TV paper account trades CFDs on the underlying; we measure MFE on
# the underlying spot/futures price from Yahoo/Binance, which is close
# enough (paper fills also happen at the underlying price, not CFD spread).

_TV_TO_SOURCE_SYMBOL: dict[str, tuple[str, str]] = {
    "USOIL":    ("CL=F",      "1h"),    # WTI Crude front-month futures
    "USOILCASH":("CL=F",      "1h"),
    "UKOIL":    ("BZ=F",      "1h"),    # Brent
    "XAUUSD":   ("GC=F",      "1h"),
    "GOLD":     ("GC=F",      "1h"),
    "XAGUSD":   ("SI=F",      "1h"),
    "SILVER":   ("SI=F",      "1h"),
    "EURUSD":   ("EURUSD=X",  "1h"),
    "GBPUSD":   ("GBPUSD=X",  "1h"),
    "USDJPY":   ("USDJPY=X",  "1h"),
    "SPX":      ("SPY",       "1h"),
    "NDX":      ("QQQ",       "1h"),
    "BTCUSD":   ("BTCUSDT",   "1h"),
    "ETHUSD":   ("ETHUSDT",   "1h"),
}


def resolve_source(tv_symbol: str) -> tuple[str, str]:
    key = tv_symbol.upper()
    if key in _TV_TO_SOURCE_SYMBOL:
        return _TV_TO_SOURCE_SYMBOL[key]
    # Fallbacks — treat as crypto pair or yahoo ticker as-is
    if key.endswith("USDT"):
        return (key, "1h")
    return (key, "1h")


def _ts_to_epoch(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def _seconds_per_bar(interval: str) -> int:
    return {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "4h": 14400, "1d": 86400}.get(interval, 3600)


# ── MFE / MAE core ───────────────────────────────────────────────────────

def compute_mfe_mae(side: str, entry_price: float, sl: float | None,
                    post_exit_bars: list[dict[str, Any]],
                    offsets: tuple[int, ...] = (10, 50, 100)
                    ) -> dict[str, float | None]:
    """Return {"mfe_10": r, "mae_10": r, ...} in R units (or % if no sl).

    R denominator: |entry - sl| / entry  (relative risk). Falls back to
    1% if sl is missing so values stay comparable.
    """
    risk = abs(entry_price - sl) / entry_price if (sl and sl > 0) else 0.01
    out: dict[str, float | None] = {}

    for k in offsets:
        window = post_exit_bars[:k]
        if not window:
            out[f"mfe_{k}"] = None
            out[f"mae_{k}"] = None
            continue
        if side == "long":
            best_high = max(b["high"] for b in window)
            worst_low = min(b["low"] for b in window)
            mfe_pct = (best_high / entry_price - 1)
            mae_pct = (worst_low / entry_price - 1)   # negative for long
        else:  # short
            best_low = min(b["low"] for b in window)
            worst_high = max(b["high"] for b in window)
            mfe_pct = (1 - best_low / entry_price)
            mae_pct = (1 - worst_high / entry_price)  # negative for short
        out[f"mfe_{k}"] = mfe_pct / risk
        out[f"mae_{k}"] = mae_pct / risk
    return out


# ── Pipeline ─────────────────────────────────────────────────────────────

def process_trade(trade_row: sqlite3.Row, dry_run: bool = False) -> dict[str, Any]:
    tv_sym = trade_row["symbol"]
    src_sym, interval = resolve_source(tv_sym)
    secs_per_bar = _seconds_per_bar(interval)

    exit_ts = trade_row["exit_ts"]
    if exit_ts is None:
        return {"id": trade_row["id"], "skipped": "no exit_ts"}

    exit_epoch = _ts_to_epoch(exit_ts)
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    bars_since = (now_epoch - exit_epoch) // secs_per_bar

    if bars_since < 10:
        return {"id": trade_row["id"], "skipped": f"only {bars_since} bars since exit; need ≥10"}

    # Fetch enough bars to cover (time since exit + buffer)
    nbars = max(200, min(2000, bars_since + 150))
    try:
        bars = fetch_any(src_sym, interval, nbars)
    except Exception as e:
        return {"id": trade_row["id"], "error": f"fetch {src_sym} failed: {e}"}

    # Find the first bar strictly AFTER exit_ts
    post_exit = [b for b in bars if b.get("ts", 0) > exit_epoch]
    if not post_exit:
        return {"id": trade_row["id"], "skipped": f"no bars after {exit_ts} in fetched range"}

    side = trade_row["side"]
    entry = trade_row["entry_price"]
    sl = trade_row["sl"]
    mfe_mae = compute_mfe_mae(side, entry, sl, post_exit)

    # Only overwrite columns that are currently NULL (don't clobber reruns)
    updates: dict[str, float | None] = {}
    for k, v in mfe_mae.items():
        if trade_row[k] is None and v is not None:
            updates[k] = v

    if not updates:
        return {"id": trade_row["id"], "skipped": "already fully populated"}

    record = {
        "id": trade_row["id"], "symbol": tv_sym, "source": src_sym,
        "side": side, "entry": entry, "exit": trade_row["exit_price"],
        "post_exit_bars_available": len(post_exit),
        **{k: round(v, 3) if isinstance(v, float) else v for k, v in mfe_mae.items()},
        "written": list(updates.keys()) if not dry_run else [],
    }
    return record, updates


def run_followup(db_path: str = DEFAULT_DB_PATH, single_id: int | None = None,
                 dry_run: bool = False) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with connect(db_path) as conn:
        if single_id is not None:
            rows = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND exit_ts IS NOT NULL",
                (single_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM trades
                WHERE exit_ts IS NOT NULL AND mfe_100 IS NULL
                ORDER BY id
            """).fetchall()

        for row in rows:
            out = process_trade(row, dry_run=dry_run)
            # process_trade returns tuple on success, dict on skip/error
            if isinstance(out, tuple):
                rec, updates = out
                if not dry_run:
                    sets = ", ".join(f"{k}=?" for k in updates.keys())
                    conn.execute(f"UPDATE trades SET {sets} WHERE id=?",
                                 (*updates.values(), row["id"]))
                results.append(rec)
            else:
                results.append(out)
        if not dry_run:
            conn.commit()

    # Aggregate leakage stat over last 30 fully-populated trades
    with connect(db_path) as conn:
        closed = conn.execute("""
            SELECT pnl_pct, mfe_100, mae_100, side, entry_price, sl
            FROM trades
            WHERE exit_ts IS NOT NULL AND mfe_100 IS NOT NULL
            ORDER BY id DESC LIMIT 30
        """).fetchall()
    leakage_values = []
    for r in closed:
        risk = abs(r["entry_price"] - (r["sl"] or 0)) / r["entry_price"] if r["sl"] else 0.01
        realized_r = (r["pnl_pct"] / 100) / risk if risk > 0 else 0
        leak = r["mfe_100"] - realized_r
        leakage_values.append(leak)
    mean_leak = (sum(leakage_values) / len(leakage_values)) if leakage_values else None

    return {
        "processed": len(results),
        "results": results,
        "closed_with_mfe_100_count": len(closed),
        "mean_mfe_leakage_R_last30": mean_leak,
        "leakage_target_lt_0_5R": (mean_leak < 0.5) if mean_leak is not None else None,
    }


def main() -> int:
    p = argparse.ArgumentParser(prog="mfe_tracker",
                                description="Populate MFE/MAE columns for closed trades.")
    p.add_argument("--db", default=DEFAULT_DB_PATH)
    p.add_argument("--id", type=int, default=None, help="process a single trade id")
    p.add_argument("--dry-run", action="store_true", help="fetch + compute but don't write back")
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args()

    out = run_followup(args.db, single_id=args.id, dry_run=args.dry_run)

    if args.format == "json":
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"processed {out['processed']} trade(s)")
    for r in out["results"]:
        if "skipped" in r:
            print(f"  #{r['id']} skipped: {r['skipped']}")
        elif "error" in r:
            print(f"  #{r['id']} ERROR: {r['error']}")
        else:
            mfe = lambda k: f"{r[k]:+.2f}R" if r.get(k) is not None else "—"
            print(f"  #{r['id']} {r['symbol']}({r['source']}) {r['side']}  "
                  f"post-exit bars {r['post_exit_bars_available']}  "
                  f"MFE +10/+50/+100: {mfe('mfe_10')}/{mfe('mfe_50')}/{mfe('mfe_100')}  "
                  f"MAE +10/+50/+100: {mfe('mae_10')}/{mfe('mae_50')}/{mfe('mae_100')}  "
                  f"written: {r.get('written') or 'dry-run'}")
    if out["mean_mfe_leakage_R_last30"] is not None:
        status = "✅" if out["leakage_target_lt_0_5R"] else "⚠️"
        print(f"\n{status} mean MFE leakage over last {out['closed_with_mfe_100_count']} "
              f"trades: {out['mean_mfe_leakage_R_last30']:+.3f}R "
              f"(target < 0.5R)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
