"""
Propfirm v4 engine — P1 version.

Single class `PropfirmEngine` with incremental phase flags. This P1
revision only wires `session_filter_on`; later phases extend:
  P2 atr_trail_on        — ATR chandelier trail
  P3 confluence_gate_on  — 3-of-3 entry gate
  P4 mfe_log_on          — post-exit counterfactual log
  P5 pn_sizing_on        — fixed 1% risk, skip-after-losses

The core loop is forked from vendor.regime_v3_volume.run_v3_engine so
that per-bar filters can be applied natively (post-hoc filtering is NOT
equivalent because the state machine's "no entry while in-position" rule
means a skipped trade frees up the position slot for the next signal).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from .vendor import regime_v3_volume as _v3
from .vendor.regime_dual_engine import adx, bb_width
from .vendor.strategy_bake_off import s1_trend_ema, s6_mtf_combo
from . import session_filter as _sf


@dataclass
class PropfirmConfig:
    # P1
    session_filter_on: bool = False
    # Reserved for later phases (not yet wired):
    atr_trail_on: bool = False
    confluence_gate_on: bool = False
    mfe_log_on: bool = False
    pn_sizing_on: bool = False

    # Risk params (shared across phases)
    sl: float = 0.015
    tp: float = 0.03

    # Asset class override (if None, auto-classify from symbol)
    asset_class: str | None = None


@dataclass
class PropfirmResult:
    name: str
    symbol: str
    trades: list[dict[str, Any]] = field(default_factory=list)
    regime_count: dict[str, int] = field(default_factory=dict)
    filtered_out: int = 0  # bars where session filter blocked an entry

    def summary(self) -> dict[str, Any]:
        if not self.trades:
            return {"name": self.name, "symbol": self.symbol, "trades": 0,
                    "regime_count": self.regime_count,
                    "filtered_out": self.filtered_out}
        pnls = [t["pnl_pct"] for t in self.trades]
        wins = sum(1 for p in pnls if p > 0)
        total = sum(pnls)
        sharpe = ((statistics.mean(pnls) / statistics.stdev(pnls)) * math.sqrt(252)
                  if len(pnls) > 1 and statistics.stdev(pnls) > 0 else 0.0)
        cum = 0.0; peak = 0.0; max_dd = 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        return {
            "name": self.name,
            "symbol": self.symbol,
            "trades": len(self.trades),
            "win_rate": wins / len(self.trades) * 100,
            "total_pnl": total,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "regime_count": self.regime_count,
            "filtered_out": self.filtered_out,
        }


class PropfirmEngine:
    """Incremental propfirm-style engine. Each phase flips one flag."""

    def __init__(self, config: PropfirmConfig | None = None):
        self.cfg = config or PropfirmConfig()

    def run(self, bars: list[dict[str, Any]], symbol: str) -> PropfirmResult:
        """Run the engine on a bars list. Returns a PropfirmResult."""
        cfg = self.cfg
        asset = cfg.asset_class or symbol
        result = PropfirmResult(name=f"propfirm_v4_p1", symbol=symbol)

        closes = [b["close"] for b in bars]
        adx_s = adx(bars, 14)
        bbw_s = bb_width(closes, 20)
        vd_s = _v3.volume_delta(bars, 20)

        pos = 0
        entry = 0.0
        active = None  # regime label of the active position

        for i in range(50, len(bars) - 1):
            bar = bars[i]
            regime = _v3.classify_v3(bars, i, adx_s, bbw_s, vd_s)
            result.regime_count[regime] = result.regime_count.get(regime, 0) + 1

            # --- P1 session + news filter (applied to entries only) ---
            tradeable_now = True
            if cfg.session_filter_on:
                ts = bar.get("ts")
                if ts is not None:
                    tradeable_now = _sf.is_tradeable(ts, asset)

            # Exit logic runs regardless of session filter — once in a
            # position, SL/TP/regime-flip still apply even outside hours.
            if pos != 0:
                incompat = False
                if regime in ("skip", "avoid"):
                    incompat = True
                elif active == "strong_long" and regime in ("strong_short", "range_divergent"):
                    incompat = True
                elif active == "strong_short" and regime == "strong_long":
                    incompat = True
                if incompat:
                    pnl = ((bar["close"] / entry - 1) * 100 if pos == 1
                           else (1 - bar["close"] / entry) * 100)
                    result.trades.append({
                        "side": "L" if pos == 1 else "S",
                        "regime": active,
                        "entry": entry, "exit": bar["close"],
                        "pnl_pct": pnl, "reason": "regime_incompat",
                        "ts": bar.get("ts"),
                    })
                    pos = 0
                    active = None

            if pos != 0:
                if pos == 1:
                    if bar["low"] <= entry * (1 - cfg.sl):
                        result.trades.append({
                            "side": "L", "regime": active, "entry": entry,
                            "exit": entry * (1 - cfg.sl),
                            "pnl_pct": -cfg.sl * 100, "reason": "SL",
                            "ts": bar.get("ts"),
                        })
                        pos = 0; active = None
                    elif bar["high"] >= entry * (1 + cfg.tp):
                        result.trades.append({
                            "side": "L", "regime": active, "entry": entry,
                            "exit": entry * (1 + cfg.tp),
                            "pnl_pct": cfg.tp * 100, "reason": "TP",
                            "ts": bar.get("ts"),
                        })
                        pos = 0; active = None
                elif pos == -1:
                    if bar["high"] >= entry * (1 + cfg.sl):
                        result.trades.append({
                            "side": "S", "regime": active, "entry": entry,
                            "exit": entry * (1 + cfg.sl),
                            "pnl_pct": -cfg.sl * 100, "reason": "SL",
                            "ts": bar.get("ts"),
                        })
                        pos = 0; active = None
                    elif bar["low"] <= entry * (1 - cfg.tp):
                        result.trades.append({
                            "side": "S", "regime": active, "entry": entry,
                            "exit": entry * (1 - cfg.tp),
                            "pnl_pct": cfg.tp * 100, "reason": "TP",
                            "ts": bar.get("ts"),
                        })
                        pos = 0; active = None
                continue  # while in-position, do not consider new entries

            # --- Entry logic ---
            if not tradeable_now:
                result.filtered_out += 1
                continue

            signal = None
            target_pos = 0
            if regime == "strong_long":
                signal = s1_trend_ema(bars, i, {})
                if signal == "long":
                    target_pos = 1
            elif regime == "strong_short":
                signal = s1_trend_ema(bars, i, {})
                if signal == "short":
                    target_pos = -1
            elif regime == "mm_range":
                signal = s6_mtf_combo(bars, i, {})
                if signal == "long":
                    target_pos = 1
                elif signal == "short":
                    target_pos = -1
            elif regime == "weak_trend":
                signal = s1_trend_ema(bars, i, {})
                if signal == "long":
                    target_pos = 1
                elif signal == "short":
                    target_pos = -1
            # range_divergent / avoid / skip → no trade

            if target_pos != 0:
                pos = target_pos
                entry = bar["close"]
                active = regime

        return result


def run_baseline_vs_filtered(bars: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    """Convenience: run once with filter off (v3 baseline) and once with
    filter on, return both summaries side-by-side."""
    off = PropfirmEngine(PropfirmConfig(session_filter_on=False)).run(bars, symbol).summary()
    on = PropfirmEngine(PropfirmConfig(session_filter_on=True)).run(bars, symbol).summary()
    return {"baseline": off, "filtered": on}


if __name__ == "__main__":
    # Smoke test on a small synthetic bar set.
    import time
    now = int(time.time())
    bars = [{
        "ts": now + i * 3600,
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
        "volume": 1000.0,
    } for i in range(200)]
    eng = PropfirmEngine(PropfirmConfig(session_filter_on=True))
    r = eng.run(bars, "BTCUSDT").summary()
    print(r)
