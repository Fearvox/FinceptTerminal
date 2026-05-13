"""
Phase 1 pass-gate runner — 8-scenario matrix, session filter OFF vs ON.

Produces stdout + JSON dump consumed by reports/phase_1_report.md.
Pass gate (spec §4.3 + plan §Phase 1):
  (a) filtered Sharpe ≥ baseline Sharpe on ≥ 5/8 scenarios (non-regressing)
  (b) filtered trade count ≤ 60% of baseline (↓ ≥ 40%)

Usage:
  python3 -m propfirm_engine.run_phase1_backtest > reports/phase_1_raw.txt
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
    # (symbol, interval, bars, asset_class override)
    ("BTCUSDT",  "1h",  1500, "crypto"),
    ("ETHUSDT",  "1h",  1500, "crypto"),
    ("SPY",      "1h",  1500, "equity"),
    ("QQQ",      "1h",  1500, "equity"),
    ("GC=F",     "1h",  1500, "gold"),
    ("CL=F",     "1h",  1500, "oil"),
    ("EURUSD=X", "1h",  1500, "fx"),
    ("BTCUSDT",  "15m", 2000, "crypto"),
]


def _run_one(symbol: str, interval: str, nbars: int, asset_class: str):
    bars = fetch_any(symbol, interval, nbars)
    if len(bars) < 200:
        return {"error": f"insufficient bars ({len(bars)})"}

    off = PropfirmEngine(PropfirmConfig(session_filter_on=False,
                                        asset_class=asset_class)).run(bars, symbol).summary()
    on = PropfirmEngine(PropfirmConfig(session_filter_on=True,
                                       asset_class=asset_class)).run(bars, symbol).summary()

    trade_ratio = (on["trades"] / off["trades"]) if off["trades"] else float("nan")
    return {
        "symbol": symbol, "interval": interval, "asset_class": asset_class,
        "bars": len(bars),
        "baseline": off,
        "filtered": on,
        "trade_ratio_on_off": trade_ratio,
        "sharpe_non_regressing": on.get("sharpe", 0.0) >= off.get("sharpe", 0.0) - 1e-9,
    }


def main():
    t0 = time.time()
    out = {"scenarios": [], "started_at": t0}
    for sym, itv, n, cls in SCENARIOS:
        label = f"{sym} {itv}"
        try:
            r = _run_one(sym, itv, n, cls)
            out["scenarios"].append(r)
            if "error" in r:
                print(f"  {label:<20} ERR {r['error']}", flush=True)
                continue
            b, f = r["baseline"], r["filtered"]
            print(f"  {label:<20} baseline: {b['trades']:>4}t  sh {b.get('sharpe',0):>5.2f}  "
                  f"pnl {b.get('total_pnl',0):>+6.1f}%  |  filtered: {f['trades']:>4}t  "
                  f"sh {f.get('sharpe',0):>5.2f}  pnl {f.get('total_pnl',0):>+6.1f}%  "
                  f"| ratio {r['trade_ratio_on_off']:.2f}  sh_ok={r['sharpe_non_regressing']}",
                  flush=True)
        except Exception as e:
            tb = traceback.format_exc()
            out["scenarios"].append({"symbol": sym, "interval": itv, "error": str(e), "tb": tb})
            print(f"  {label:<20} EXC {e}", flush=True)

    # Pass-gate aggregation
    ok = [s for s in out["scenarios"] if "error" not in s]
    n_total = len(ok)
    non_regressing = sum(1 for s in ok if s["sharpe_non_regressing"])
    trade_ratios = [s["trade_ratio_on_off"] for s in ok if s["trade_ratio_on_off"] == s["trade_ratio_on_off"]]
    mean_ratio = (sum(trade_ratios) / len(trade_ratios)) if trade_ratios else float("nan")
    gate_a = non_regressing >= max(1, (5 * n_total) // 8)   # ≥ 5/8 scaled
    gate_b = mean_ratio <= 0.60 if trade_ratios else False

    out["pass_gate"] = {
        "scenarios_evaluated": n_total,
        "sharpe_non_regressing_count": non_regressing,
        "gate_a_sharpe_non_regress": gate_a,
        "mean_trade_ratio_on_off": mean_ratio,
        "gate_b_trade_count_drop_40pct": gate_b,
        "overall_pass": gate_a and gate_b,
    }
    out["elapsed_sec"] = time.time() - t0

    print()
    print("=" * 60)
    print(f"  Pass gate A (Sharpe non-regressing ≥ 5/8): "
          f"{non_regressing}/{n_total} → {'PASS' if gate_a else 'FAIL'}")
    print(f"  Pass gate B (trade count ≤ 60% of baseline): "
          f"mean ratio {mean_ratio:.2f} → {'PASS' if gate_b else 'FAIL'}")
    print(f"  Overall: {'PASS' if out['pass_gate']['overall_pass'] else 'FAIL'}")
    print("=" * 60)

    # Dump JSON for report consumption
    os.makedirs(os.path.join(os.path.dirname(__file__), "reports"), exist_ok=True)
    out_path = os.path.join(os.path.dirname(__file__), "reports", "phase_1_raw.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n  raw results → {out_path}", flush=True)
    return 0 if out["pass_gate"]["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
