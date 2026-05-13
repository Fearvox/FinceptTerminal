"""
Phase 4 leakage measurement — 8-scenario matrix, P1+P3+P4 enabled.

P4 doesn't change strategy; it logs post-exit excursion of every P3
confluence-gated trade and computes leakage = mean(MFE_100 − realized_pnl).

Pass gate (spec §4.3 + spec §6):
  A. Mean leakage over the last 30 completed trades (POOLED across all
     scenarios) < 0.5R, where R = 1.5pp (engine's default fixed SL).

We don't impose a separate Sharpe regression gate here because P4 is
non-mutating — by construction it cannot regress Sharpe vs the same
config without mfe_log_on. The only failure mode for P4 itself is
insufficient sample (fewer than 30 completed trades across all
scenarios).

Usage:
  python3 -m propfirm_engine.run_phase4_backtest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

from .engine import PropfirmEngine, PropfirmConfig
from .vendor.regime_dual_engine import fetch_any

SCENARIOS = [
    ("BTCUSDT",  "1h",  1500, "crypto"),
    ("ETHUSDT",  "1h",  1500, "crypto"),
    ("SPY",      "1h",  1500, "equity"),
    ("QQQ",      "1h",  1500, "equity"),
    ("GC=F",     "1h",  1500, "gold"),
    ("CL=F",     "1h",  1500, "oil"),
    ("EURUSD=X", "1h",  1500, "fx"),
    ("BTCUSDT",  "15m", 2000, "crypto"),
]

R_UNIT_PCT = 1.5            # 1R in percentage points (matches engine cfg.sl default)
LEAKAGE_THRESHOLD_R = 0.5   # spec §4.3 pass gate threshold
SAMPLE_WINDOW = 30          # spec §4.3 "last 30 trades"


def _run_one(symbol: str, interval: str, nbars: int, asset_class: str,
             scale_out: bool = False):
    bars = fetch_any(symbol, interval, nbars)
    if len(bars) < 200:
        return {"error": f"insufficient bars ({len(bars)})"}

    cfg = PropfirmConfig(
        session_filter_on=True,
        confluence_gate_on=True,
        mfe_log_on=True,
        scale_out_at_1R=scale_out,
        asset_class=asset_class,
    )
    res = PropfirmEngine(cfg).run(bars, symbol)
    summary = res.summary()
    tracker = res.mfe_tracker

    completed = tracker.completed_trades() if tracker else []
    return {
        "symbol": symbol,
        "interval": interval,
        "asset_class": asset_class,
        "bars": len(bars),
        "trades": summary.get("trades", 0),
        "p3_sharpe": summary.get("sharpe", 0.0),
        "p3_wr": summary.get("win_rate", 0.0),
        "tracked_registered": len(tracker.trades) if tracker else 0,
        "tracked_completed": len(completed),
        "tracked_pending": (len(tracker.trades) - len(completed)) if tracker else 0,
        "per_trade_leakage_R": [
            ((t.mfe_100 or 0.0) - t.realized_pnl_pct) / R_UNIT_PCT
            for t in completed
        ],
        "mfe_summary": summary.get("mfe_leakage"),
    }


def _aggregate(results: list[dict]) -> dict:
    """Pool completed-trade leakage R-values across all scenarios."""
    pooled_R = []
    for r in results:
        if "error" in r:
            continue
        pooled_R.extend(r.get("per_trade_leakage_R", []))

    if len(pooled_R) == 0:
        return {
            "pooled_total": 0,
            "pass_gate": False,
            "reason": "no completed P4-tracked trades across scenarios",
        }

    # Spec §4.3: "last 30 trades". Take the most recent SAMPLE_WINDOW.
    sample = pooled_R[-SAMPLE_WINDOW:] if len(pooled_R) >= SAMPLE_WINDOW else pooled_R
    mean_R = sum(sample) / len(sample)
    median_R = sorted(sample)[len(sample) // 2]
    max_R = max(sample)
    min_R = min(sample)
    above = sum(1 for L in sample if L >= LEAKAGE_THRESHOLD_R)

    enough_sample = len(sample) >= SAMPLE_WINDOW
    pass_gate = enough_sample and mean_R < LEAKAGE_THRESHOLD_R

    return {
        "pooled_total": len(pooled_R),
        "sample_window": SAMPLE_WINDOW,
        "sample_used": len(sample),
        "enough_sample": enough_sample,
        "mean_leakage_R": mean_R,
        "median_leakage_R": median_R,
        "max_leakage_R": max_R,
        "min_leakage_R": min_R,
        "trades_above_threshold": above,
        "leakage_threshold_R": LEAKAGE_THRESHOLD_R,
        "r_unit_pct": R_UNIT_PCT,
        "pass_gate": pass_gate,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scale-out",
        action="store_true",
        help="Enable P4.1 scale-out at +1R + remainder_tp on the second half.",
    )
    args = parser.parse_args()

    phase_label = "p4.1" if args.scale_out else "p4"
    raw_filename = f"phase_4{'_1' if args.scale_out else ''}_raw.json"

    t0 = time.time()
    out = {"scenarios": [], "started_at": t0, "phase": phase_label,
           "scale_out": args.scale_out}
    for sym, itv, n, cls in SCENARIOS:
        label = f"{sym} {itv}"
        try:
            r = _run_one(sym, itv, n, cls, scale_out=args.scale_out)
            out["scenarios"].append(r)
            if "error" in r:
                print(f"  {label:<20} ERR {r['error']}", flush=True)
                continue
            print(
                f"  {label:<20} "
                f"trades={r['trades']:>3}  "
                f"tracked={r['tracked_registered']:>3}  "
                f"completed={r['tracked_completed']:>3}  "
                f"pending={r['tracked_pending']:>3}",
                flush=True,
            )
        except Exception as e:
            tb = traceback.format_exc()
            out["scenarios"].append({"symbol": sym, "interval": itv,
                                     "error": str(e), "tb": tb})
            print(f"  {label:<20} EXC {e}", flush=True)

    agg = _aggregate(out["scenarios"])
    out["pass_gate"] = agg
    out["elapsed_sec"] = time.time() - t0

    print()
    print("=" * 70)
    print(f"  Pooled completed P4 trades: {agg.get('pooled_total', 0)}")
    if agg.get("pooled_total", 0) > 0:
        print(f"  Sample (last {agg['sample_window']}):  used {agg['sample_used']}  "
              f"enough={agg['enough_sample']}")
        print(f"  Mean leakage:    {agg['mean_leakage_R']:+.3f} R   "
              f"({agg['mean_leakage_R']*R_UNIT_PCT:+.3f} pp)")
        print(f"  Median:          {agg['median_leakage_R']:+.3f} R")
        print(f"  Max / Min:       {agg['max_leakage_R']:+.3f} / "
              f"{agg['min_leakage_R']:+.3f} R")
        print(f"  Above threshold (≥{agg['leakage_threshold_R']}R): "
              f"{agg['trades_above_threshold']}/{agg['sample_used']}")
    print()
    print(f"  Pass gate (mean leakage < {LEAKAGE_THRESHOLD_R}R, N≥{SAMPLE_WINDOW}): "
          f"{'PASS' if agg.get('pass_gate') else 'FAIL'}")
    print("=" * 70)

    os.makedirs(os.path.join(os.path.dirname(__file__), "reports"), exist_ok=True)
    out_path = os.path.join(os.path.dirname(__file__), "reports", raw_filename)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n  raw results → {out_path}", flush=True)
    return 0 if agg.get("pass_gate") else 1


if __name__ == "__main__":
    sys.exit(main())
