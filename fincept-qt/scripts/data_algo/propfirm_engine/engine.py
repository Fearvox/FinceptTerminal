"""
Propfirm v4 engine — P2 version.

Single class `PropfirmEngine` with incremental phase flags:
  P1 session_filter_on   — UTC hour masks + news-window skip
  P2 atr_trail_on        — ATR chandelier trail (replaces fixed SL)
  P3 confluence_gate_on  — 3-of-3 entry gate (reserved)
  P4 mfe_log_on          — post-exit counterfactual log (reserved)
  P5 pn_sizing_on        — fixed 1% risk, skip-after-losses (reserved)

Core loop is forked from vendor.regime_v3_volume.run_v3_engine so that
per-bar filters can be applied natively. ATR trail replaces the fixed
SL with a ratcheting chandelier stop; TP stays as a hard cap upside.

No breakeven move, ever (spec §4.1). `trade_state_machine.py` was
deleted in this phase along with its BE logic.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from . import atr_utils as _atr
from . import session_filter as _sf
from .vendor import regime_v3_volume as _v3
from .vendor.regime_dual_engine import adx, bb_width
from .vendor.strategy_bake_off import s1_trend_ema, s6_mtf_combo


# Regimes where ATR chandelier trailing is allowed. P2 attempt-1 (2026-04-20)
# applied trail unconditionally and damaged 6 of 7 positive-baseline scenarios:
# range regimes (mm_range, range_divergent) have normal intrabar swing ≈ 1.5×
# ATR, so the trail fires inside noise and cuts winners. P2.1 (this redesign)
# restricts trail to trend-y regimes only; range regimes fall back to fixed SL.
# Single-line check, zero new parameters. L2-compatible (no multiplier sweep).
TREND_REGIMES = frozenset({"strong_long", "strong_short", "weak_trend"})


@dataclass
class PropfirmConfig:
    # P1
    session_filter_on: bool = False
    # P2
    atr_trail_on: bool = False
    atr_period: int = 14
    atr_mult: float = 1.5          # auto-widened for BTC 15m unless override
    atr_mult_override: float | None = None
    # Reserved (not yet wired)
    confluence_gate_on: bool = False
    mfe_log_on: bool = False
    pn_sizing_on: bool = False

    # Risk params (fixed SL fallback used only when atr_trail_on=False)
    sl: float = 0.015
    tp: float = 0.03

    # Asset routing
    asset_class: str | None = None
    interval: str | None = None    # "1h" / "15m" etc — used for ATR multiplier routing


@dataclass
class PropfirmResult:
    name: str
    symbol: str
    trades: list[dict[str, Any]] = field(default_factory=list)
    regime_count: dict[str, int] = field(default_factory=dict)
    filtered_out: int = 0

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
        exit_reasons: dict[str, int] = {}
        for t in self.trades:
            k = t.get("reason", "?")
            exit_reasons[k] = exit_reasons.get(k, 0) + 1
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
            "exit_reasons": exit_reasons,
        }


class PropfirmEngine:
    """Incremental propfirm-style engine. Each phase flips one flag."""

    def __init__(self, config: PropfirmConfig | None = None):
        self.cfg = config or PropfirmConfig()

    def _resolved_mult(self, symbol: str) -> float:
        cfg = self.cfg
        if cfg.atr_mult_override is not None:
            return cfg.atr_mult_override
        return _atr.multiplier_for(
            symbol,
            cfg.interval,
            asset_class=cfg.asset_class,
            default=cfg.atr_mult,
        )

    def run(self, bars: list[dict[str, Any]], symbol: str) -> PropfirmResult:
        cfg = self.cfg
        asset = cfg.asset_class or symbol
        phase_label = "p2" if cfg.atr_trail_on else ("p1" if cfg.session_filter_on else "p0")
        result = PropfirmResult(name=f"propfirm_v4_{phase_label}", symbol=symbol)

        closes = [b["close"] for b in bars]
        adx_s = adx(bars, 14)
        bbw_s = bb_width(closes, 20)
        vd_s = _v3.volume_delta(bars, 20)
        atr_s = _atr.atr(bars, cfg.atr_period) if cfg.atr_trail_on else None
        mult = self._resolved_mult(symbol) if cfg.atr_trail_on else 0.0

        pos = 0
        entry = 0.0
        entry_idx = -1
        trail_sl = 0.0          # ratcheting chandelier SL (active only in P2+)
        active = None

        for i in range(50, len(bars) - 1):
            bar = bars[i]
            regime = _v3.classify_v3(bars, i, adx_s, bbw_s, vd_s)
            result.regime_count[regime] = result.regime_count.get(regime, 0) + 1

            # --- P1 session + news filter (entries only) ---
            tradeable_now = True
            if cfg.session_filter_on:
                ts = bar.get("ts")
                if ts is not None:
                    tradeable_now = _sf.is_tradeable(ts, asset)

            # --- Exit logic: regime-incompat first ---
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
                        "side": "L" if pos == 1 else "S", "regime": active,
                        "entry": entry, "exit": bar["close"],
                        "pnl_pct": pnl, "reason": "regime_incompat",
                        "ts": bar.get("ts"),
                    })
                    pos = 0; active = None; entry_idx = -1

            # --- Exit logic: SL / trail / TP ---
            if pos != 0:
                # P2.1: trail only in trend regimes; range falls back to fixed SL.
                if cfg.atr_trail_on and atr_s is not None and active in TREND_REGIMES:
                    # Update ratcheting chandelier SL
                    if pos == 1:
                        new_sl = _atr.chandelier_long_sl(entry_idx, i, bars, atr_s, mult)
                        if new_sl > trail_sl:
                            trail_sl = new_sl
                    else:
                        new_sl = _atr.chandelier_short_sl(entry_idx, i, bars, atr_s, mult)
                        if new_sl < trail_sl or trail_sl == 0.0:
                            trail_sl = new_sl

                    # Check trail hit first, then TP
                    if pos == 1:
                        if bar["low"] <= trail_sl:
                            pnl = (trail_sl / entry - 1) * 100
                            result.trades.append({
                                "side": "L", "regime": active, "entry": entry,
                                "exit": trail_sl, "pnl_pct": pnl, "reason": "atr_trail",
                                "ts": bar.get("ts"),
                            })
                            pos = 0; active = None; entry_idx = -1
                        elif bar["high"] >= entry * (1 + cfg.tp):
                            result.trades.append({
                                "side": "L", "regime": active, "entry": entry,
                                "exit": entry * (1 + cfg.tp),
                                "pnl_pct": cfg.tp * 100, "reason": "tp",
                                "ts": bar.get("ts"),
                            })
                            pos = 0; active = None; entry_idx = -1
                    elif pos == -1:
                        if bar["high"] >= trail_sl:
                            pnl = (1 - trail_sl / entry) * 100
                            result.trades.append({
                                "side": "S", "regime": active, "entry": entry,
                                "exit": trail_sl, "pnl_pct": pnl, "reason": "atr_trail",
                                "ts": bar.get("ts"),
                            })
                            pos = 0; active = None; entry_idx = -1
                        elif bar["low"] <= entry * (1 - cfg.tp):
                            result.trades.append({
                                "side": "S", "regime": active, "entry": entry,
                                "exit": entry * (1 - cfg.tp),
                                "pnl_pct": cfg.tp * 100, "reason": "tp",
                                "ts": bar.get("ts"),
                            })
                            pos = 0; active = None; entry_idx = -1
                else:
                    # Fixed SL/TP (P0/P1 behavior)
                    if pos == 1:
                        if bar["low"] <= entry * (1 - cfg.sl):
                            result.trades.append({
                                "side": "L", "regime": active, "entry": entry,
                                "exit": entry * (1 - cfg.sl),
                                "pnl_pct": -cfg.sl * 100, "reason": "sl",
                                "ts": bar.get("ts"),
                            })
                            pos = 0; active = None; entry_idx = -1
                        elif bar["high"] >= entry * (1 + cfg.tp):
                            result.trades.append({
                                "side": "L", "regime": active, "entry": entry,
                                "exit": entry * (1 + cfg.tp),
                                "pnl_pct": cfg.tp * 100, "reason": "tp",
                                "ts": bar.get("ts"),
                            })
                            pos = 0; active = None; entry_idx = -1
                    elif pos == -1:
                        if bar["high"] >= entry * (1 + cfg.sl):
                            result.trades.append({
                                "side": "S", "regime": active, "entry": entry,
                                "exit": entry * (1 + cfg.sl),
                                "pnl_pct": -cfg.sl * 100, "reason": "sl",
                                "ts": bar.get("ts"),
                            })
                            pos = 0; active = None; entry_idx = -1
                        elif bar["low"] <= entry * (1 - cfg.tp):
                            result.trades.append({
                                "side": "S", "regime": active, "entry": entry,
                                "exit": entry * (1 - cfg.tp),
                                "pnl_pct": cfg.tp * 100, "reason": "tp",
                                "ts": bar.get("ts"),
                            })
                            pos = 0; active = None; entry_idx = -1
                continue  # while in-position, no new entries

            # --- Entry logic ---
            if not tradeable_now:
                result.filtered_out += 1
                continue

            target_pos = 0
            signal = None
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

            if target_pos != 0:
                pos = target_pos
                entry = bar["close"]
                entry_idx = i
                active = regime
                # Initialize trail_sl at entry for P2
                if cfg.atr_trail_on and atr_s is not None:
                    a = atr_s[i] if i < len(atr_s) else 0.0
                    trail_sl = entry - mult * a if pos == 1 else entry + mult * a

        return result


def run_baseline_vs_filtered(bars, symbol, **kw):
    """Legacy convenience (kept for backward compat): filter off vs on."""
    off = PropfirmEngine(PropfirmConfig(session_filter_on=False, **kw)).run(bars, symbol).summary()
    on = PropfirmEngine(PropfirmConfig(session_filter_on=True, **kw)).run(bars, symbol).summary()
    return {"baseline": off, "filtered": on}


if __name__ == "__main__":
    import time
    now = int(time.time())
    bars = [{
        "ts": now + i * 3600,
        "open": 100.0 + i * 0.1,
        "high": 101.0 + i * 0.1,
        "low": 99.0 + i * 0.1,
        "close": 100.5 + i * 0.1,
        "volume": 1000.0,
    } for i in range(300)]
    eng = PropfirmEngine(PropfirmConfig(session_filter_on=True, atr_trail_on=True))
    r = eng.run(bars, "BTCUSDT").summary()
    print(r)
