"""
Phase 2 pass-gate runner — P1 (session filter only) vs P1+P2 (session + ATR trail).

Pass gate (spec §4.3 + plan §Phase 2):
  A. P2 Sharpe ≥ P1 Sharpe × 1.15 on ≥ 5/8 scenarios
  B. Hard floor: no scenario with P2 Sharpe < P1 Sharpe × 0.90

Additional P10-requested check (from phase_1 handoff):
  C. Baseline integrity: count how many scenarios remain Sharpe-negative
     after P1+P2. If ≥ 4, flag for P10 redesign decision.

Usage:
  python3 -m propfirm_engine.run_phase2_backtest
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


def _run_one(symbol: str, interval: str, nbars: int, asset_class: str):
    bars = fetch_any(symbol, interval, nbars)
    if len(bars) < 200:
        return {"error": f"insufficient bars ({len(bars)})"}

    p1 = PropfirmEngine(PropfirmConfig(
        session_filter_on=True, atr_trail_on=False,
        asset_class=asset_class, interval=interval,
    )).run(bars, symbol).summary()

    p2 = PropfirmEngine(PropfirmConfig(
        session_filter_on=True, atr_trail_on=True,
        asset_class=asset_class, interval=interval,
    )).run(bars, symbol).summary()

    p1_sh = p1.get("sharpe", 0.0)
    p2_sh = p2.get("sharpe", 0.0)

    # Targets for gates A and B (handle negative-baseline sign semantics)
    def _target_a(base: float) -> float:
        # "≥ base × 1.15" — when base is negative, 1.15× makes it MORE negative
        # which is easier to beat. Strict reading of spec gives that; we stay
        # strict here and let the baseline-integrity flag catch the nuance.
        return base * 1.15

    def _floor_b(base: float) -> float:
        return base * 0.90

    target_a = _target_a(p1_sh)
    floor_b = _floor_b(p1_sh)

    # For negative baselines, "P2 >= P1*1.15" is mathematically loose. Report
    # a stricter secondary signal: did P2 actually improve Sharpe at all?
    sh_improved = p2_sh > p1_sh

    return {
        "symbol": symbol, "interval": interval, "asset_class": asset_class,
        "bars": len(bars),
        "p1": p1,
        "p2": p2,
        "p1_sharpe": p1_sh,
        "p2_sharpe": p2_sh,
        "gate_a_target": target_a,
        "gate_a_pass": p2_sh >= target_a - 1e-9,
        "gate_b_floor": floor_b,
        "gate_b_pass": p2_sh >= floor_b - 1e-9,
        "sharpe_improved_strict": sh_improved,
        "p2_still_negative": p2_sh < 0,
    }


def main():
    t0 = time.time()
    out = {"scenarios": [], "started_at": t0, "phase": "p2"}
    for sym, itv, n, cls in SCENARIOS:
        label = f"{sym} {itv}"
        try:
            r = _run_one(sym, itv, n, cls)
            out["scenarios"].append(r)
            if "error" in r:
                print(f"  {label:<20} ERR {r['error']}", flush=True)
                continue
            print(f"  {label:<20} P1: {r['p1']['trades']:>4}t sh {r['p1_sharpe']:>5.2f}  "
                  f"|  P2: {r['p2']['trades']:>4}t sh {r['p2_sharpe']:>5.2f}  "
                  f"| GateA(≥{r['gate_a_target']:>5.2f})={r['gate_a_pass']!s:<5} "
                  f"GateB(≥{r['gate_b_floor']:>5.2f})={r['gate_b_pass']!s:<5} "
                  f"improved={r['sharpe_improved_strict']!s:<5} neg={r['p2_still_negative']!s}",
                  flush=True)
        except Exception as e:
            tb = traceback.format_exc()
            out["scenarios"].append({"symbol": sym, "interval": itv, "error": str(e), "tb": tb})
            print(f"  {label:<20} EXC {e}", flush=True)

    ok = [s for s in out["scenarios"] if "error" not in s]
    n_total = len(ok)
    gate_a_hits = sum(1 for s in ok if s["gate_a_pass"])
    gate_b_floor_hits = sum(1 for s in ok if not s["gate_b_pass"])
    strict_improved = sum(1 for s in ok if s["sharpe_improved_strict"])
    still_negative = sum(1 for s in ok if s["p2_still_negative"])

    gate_a = gate_a_hits >= max(1, (5 * n_total) // 8)
    gate_b = gate_b_floor_hits == 0
    baseline_concern = still_negative >= 4

    out["pass_gate"] = {
        "scenarios_evaluated": n_total,
        "gate_a_hits": gate_a_hits,
        "gate_a_pass": gate_a,
        "gate_b_floor_violations": gate_b_floor_hits,
        "gate_b_pass": gate_b,
        "sharpe_strict_improved_count": strict_improved,
        "p2_still_negative_count": still_negative,
        "baseline_integrity_concern": baseline_concern,
        "overall_pass": gate_a and gate_b,
    }
    out["elapsed_sec"] = time.time() - t0

    print()
    print("=" * 70)
    print(f"  Gate A (P2 Sh ≥ P1 Sh × 1.15, ≥5/8): "
          f"{gate_a_hits}/{n_total} → {'PASS' if gate_a else 'FAIL'}")
    print(f"  Gate B (no scenario P2 Sh < P1 Sh × 0.90): "
          f"{gate_b_floor_hits} violations → {'PASS' if gate_b else 'FAIL'}")
    print(f"  Strict improvement (P2 > P1 Sharpe): {strict_improved}/{n_total}")
    print(f"  Baseline integrity: {still_negative}/{n_total} still Sharpe-negative "
          f"→ {'⚠️ FLAG P10' if baseline_concern else 'OK'}")
    print(f"  Overall: {'PASS' if out['pass_gate']['overall_pass'] else 'FAIL'}")
    print("=" * 70)

    os.makedirs(os.path.join(os.path.dirname(__file__), "reports"), exist_ok=True)
    out_path = os.path.join(os.path.dirname(__file__), "reports", "phase_2_raw.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n  raw results → {out_path}", flush=True)
    return 0 if out["pass_gate"]["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
