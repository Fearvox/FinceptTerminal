"""
Phase 5 pass-gate runner — 8-scenario matrix, P1+P3+P5 enabled.

Adds the P.N.-style sizing layer (skip-day block after 2 consecutive
losses) on top of P3 confluence. Evaluates spec §4.3 P5 pass gate:

  N ≥ 30 pooled paper trades
  AND (WR ≥ 65% OR R:R ≥ 1.8)

Reference baseline (P3.1 backtest at v4.p3):
  N = 29, WR = 75.9%, mean leakage +1.331R

P5 result will likely show very few new pn_sizing blocks because
P3-confluence is already so selective that consecutive losses on the
same UTC day across an asset are rare. The gate evaluation is honest
either way — sample size (N≥30) is the more probable failure mode
on this dataset.

Usage:
  python3 -m propfirm_engine.run_phase5_backtest
"""
from __future__ import annotations

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

WR_THRESHOLD = 65.0          # WR ≥ 65% target
RR_THRESHOLD = 1.8           # R:R ratio ≥ 1.8 target (OR-shape with WR)
SAMPLE_WINDOW = 30           # spec §4.3 "N ≥ 30 paper trades"


def _run_one(symbol: str, interval: str, nbars: int, asset_class: str):
    bars = fetch_any(symbol, interval, nbars)
    if len(bars) < 200:
        return {"error": f"insufficient bars ({len(bars)})"}

    cfg = PropfirmConfig(
        session_filter_on=True,
        confluence_gate_on=True,
        pn_sizing_on=True,
        asset_class=asset_class,
    )
    res = PropfirmEngine(cfg).run(bars, symbol)
    summary = res.summary()

    return {
        "symbol": symbol,
        "interval": interval,
        "asset_class": asset_class,
        "bars": len(bars),
        "trades": summary.get("trades", 0),
        "wr_pct": summary.get("win_rate", 0.0),
        "sharpe": summary.get("sharpe", 0.0),
        "total_pnl": summary.get("total_pnl", 0.0),
        "filtered_confluence": summary.get("filtered_confluence", 0),
        "pn_sizing_blocked": summary.get("pn_sizing_blocked", 0),
        "per_trade_pnls": [t["pnl_pct"] for t in res.trades],
    }


def _aggregate(results: list[dict]) -> dict:
    """Pool trades across scenarios, compute WR + RR + gate verdict."""
    pooled_pnls: list[float] = []
    total_pn_blocks = 0
    for r in results:
        if "error" in r:
            continue
        pooled_pnls.extend(r.get("per_trade_pnls", []))
        total_pn_blocks += r.get("pn_sizing_blocked", 0)

    n = len(pooled_pnls)
    if n == 0:
        return {
            "pooled_total_trades": 0,
            "total_pn_sizing_blocks": total_pn_blocks,
            "pass_gate": False,
            "reason": "no trades across all scenarios",
        }

    wins = [p for p in pooled_pnls if p > 0]
    losses = [p for p in pooled_pnls if p < 0]
    wr = len(wins) / n * 100.0

    # R:R = mean(win pnl) / mean(|loss pnl|). When either bucket empty,
    # fall back to a degenerate value documented below.
    if losses:
        mean_win = sum(wins) / len(wins) if wins else 0.0
        mean_loss_abs = sum(abs(p) for p in losses) / len(losses)
        rr = mean_win / mean_loss_abs if mean_loss_abs > 0 else float("inf")
    else:
        # No losers at all — RR is "infinite" by ratio; treat as PASS-eligible
        # if there's any winner, else 0.
        rr = float("inf") if wins else 0.0

    enough_sample = n >= SAMPLE_WINDOW
    wr_pass = wr >= WR_THRESHOLD
    rr_pass = rr >= RR_THRESHOLD
    # Spec gate: N>=30 AND (WR>=65% OR RR>=1.8)
    pass_gate = enough_sample and (wr_pass or rr_pass)

    return {
        "pooled_total_trades": n,
        "sample_window": SAMPLE_WINDOW,
        "enough_sample": enough_sample,
        "win_count": len(wins),
        "loss_count": len(losses),
        "wr_pct": wr,
        "wr_threshold": WR_THRESHOLD,
        "wr_pass": wr_pass,
        "rr": rr,
        "rr_threshold": RR_THRESHOLD,
        "rr_pass": rr_pass,
        "total_pn_sizing_blocks": total_pn_blocks,
        "pass_gate": pass_gate,
    }


def main():
    t0 = time.time()
    out = {"scenarios": [], "started_at": t0, "phase": "p5a"}
    for sym, itv, n, cls in SCENARIOS:
        label = f"{sym} {itv}"
        try:
            r = _run_one(sym, itv, n, cls)
            out["scenarios"].append(r)
            if "error" in r:
                print(f"  {label:<20} ERR {r['error']}", flush=True)
                continue
            print(
                f"  {label:<20} "
                f"trades={r['trades']:>3} "
                f"WR={r['wr_pct']:>5.1f}% "
                f"sharpe={r['sharpe']:>+6.2f} "
                f"pnl={r['total_pnl']:>+6.2f}% "
                f"pn_blocks={r['pn_sizing_blocked']:>3}",
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
    print(f"  Pooled trades: {agg.get('pooled_total_trades', 0)}")
    if agg.get("pooled_total_trades", 0) > 0:
        print(f"  Sample (need ≥{agg['sample_window']}):  "
              f"enough={agg['enough_sample']}")
        print(f"  Win rate:        {agg['wr_pct']:>5.1f}%  "
              f"(target ≥{agg['wr_threshold']}%, "
              f"{'PASS' if agg['wr_pass'] else 'FAIL'})")
        rr_display = "inf" if agg['rr'] == float('inf') else f"{agg['rr']:.2f}"
        print(f"  R:R ratio:       {rr_display}   "
              f"(target ≥{agg['rr_threshold']}, "
              f"{'PASS' if agg['rr_pass'] else 'FAIL'})")
        print(f"  pn_sizing blocks: {agg['total_pn_sizing_blocks']} "
              "(skip-day fired)")
    print()
    print(f"  Pass gate (N≥{SAMPLE_WINDOW} AND (WR≥{WR_THRESHOLD}% OR "
          f"RR≥{RR_THRESHOLD})): "
          f"{'PASS' if agg.get('pass_gate') else 'FAIL'}")
    print("=" * 70)

    os.makedirs(os.path.join(os.path.dirname(__file__), "reports"), exist_ok=True)
    out_path = os.path.join(os.path.dirname(__file__), "reports", "phase_5_raw.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n  raw results → {out_path}", flush=True)
    return 0 if agg.get("pass_gate") else 1


if __name__ == "__main__":
    sys.exit(main())
