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
from . import atr_utils as _atr
from . import confluence_scorer as _cs
from . import mfe_tracker as _mfe


def compose_scale_out_pnl(half_closed: bool, half_pnl: float,
                          remainder_pnl: float, fraction: float) -> float:
    """Combine the locked-in half-close pnl with the remainder's exit pnl.

    Used by the P4.1 scale-out exit path. If the scale-out trigger never
    fired (half_closed is False), the trade exits as a single unit and
    we just return remainder_pnl. Otherwise the final realized pnl is
    `half_pnl + (1 − fraction) × remainder_pnl` — the locked-in fraction
    is fully recognized regardless of how the remainder eventually exits.

    All values are in percentage points. `fraction` is the share of the
    original position that was closed at +1R (typically 0.5).
    """
    if not half_closed:
        return remainder_pnl
    return half_pnl + (1.0 - fraction) * remainder_pnl


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

    # P4.1 scale-out experiment: when scale_out_at_1R is True, the engine
    # closes `scale_out_fraction` of the position at +1R (= +cfg.sl) and
    # lets the remainder run to a wider TP (`remainder_tp`). SL stays at
    # -cfg.sl on the remainder (NO breakeven move; spec §4.1 rail).
    # Rationale: P4 measured mean +1.331R leakage with fixed TP=3pp. The
    # wider remainder_tp captures the upside tail while the half-close
    # locks in 0.5R guaranteed once a trade goes our way.
    scale_out_at_1R: bool = False
    scale_out_fraction: float = 0.5         # close half the position at +1R
    remainder_tp: float = 0.045             # +3R = +4.5% on the remaining half

    # Asset class override (if None, auto-classify from symbol)
    asset_class: str | None = None


@dataclass
class PropfirmResult:
    name: str
    symbol: str
    trades: list[dict[str, Any]] = field(default_factory=list)
    regime_count: dict[str, int] = field(default_factory=dict)
    filtered_out: int = 0  # bars where session filter blocked an entry
    filtered_confluence: int = 0  # bars where P3 confluence gate blocked entry
    mfe_tracker: "_mfe.MfeTracker | None" = None  # P4: post-exit follow-up log

    def summary(self) -> dict[str, Any]:
        if not self.trades:
            return {"name": self.name, "symbol": self.symbol, "trades": 0,
                    "regime_count": self.regime_count,
                    "filtered_out": self.filtered_out,
                    "filtered_confluence": self.filtered_confluence}
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
        out = {
            "name": self.name,
            "symbol": self.symbol,
            "trades": len(self.trades),
            "win_rate": wins / len(self.trades) * 100,
            "total_pnl": total,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "regime_count": self.regime_count,
            "filtered_out": self.filtered_out,
            "filtered_confluence": self.filtered_confluence,
        }
        if self.mfe_tracker is not None:
            # P4: surface leakage so backtest runners and tests can read
            # it without reaching into the tracker. Default last_n=30 per
            # spec §4.3; runners may compute their own slice if needed.
            out["mfe_leakage"] = self.mfe_tracker.leakage_summary(last_n=30)
        return out


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
        # P3 confluence gate inputs (ATR + asset class). Computed once per run.
        atr_s = _atr.atr(bars, 14) if cfg.confluence_gate_on else None
        asset_class_for_cs = _sf.classify_symbol(symbol) if cfg.confluence_gate_on else None
        # P4 MFE tracker — purely observational, never changes trade decisions.
        tracker = _mfe.MfeTracker() if cfg.mfe_log_on else None
        if tracker is not None:
            result.mfe_tracker = tracker

        def _log_exit(exit_idx: int, exit_price: float, side: int, pnl_pct: float) -> None:
            """Register a just-closed trade with the MFE tracker if enabled.
            `side` is +1 for long, -1 for short. No-op when mfe_log_on is False
            so the call site stays single-line at each of the 5 exit branches."""
            if tracker is None:
                return
            trade_id = f"t-{exit_idx}-{'L' if side == 1 else 'S'}"
            tracker.register(
                trade_id=trade_id,
                exit_idx=exit_idx,
                exit_price=exit_price,
                direction="long" if side == 1 else "short",
                realized_pnl_pct=pnl_pct,
            )

        pos = 0
        entry = 0.0
        active = None  # regime label of the active position

        for i in range(50, len(bars) - 1):
            bar = bars[i]
            regime = _v3.classify_v3(bars, i, adx_s, bbw_s, vd_s)
            result.regime_count[regime] = result.regime_count.get(regime, 0) + 1

            # P4: advance MFE tracker on this bar BEFORE any exit-this-bar
            # logic. Newly-registered trades from a prior bar still see
            # this bar as elapsed=1; trades closed on THIS bar will be
            # registered below and start tracking from the NEXT bar.
            if tracker is not None:
                tracker.advance(bars, i)

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
                    _log_exit(exit_idx=i, exit_price=bar["close"], side=pos, pnl_pct=pnl)
                    pos = 0
                    active = None

            if pos != 0:
                if pos == 1:
                    if bar["low"] <= entry * (1 - cfg.sl):
                        exit_px = entry * (1 - cfg.sl)
                        result.trades.append({
                            "side": "L", "regime": active, "entry": entry,
                            "exit": exit_px,
                            "pnl_pct": -cfg.sl * 100, "reason": "SL",
                            "ts": bar.get("ts"),
                        })
                        _log_exit(i, exit_px, side=1, pnl_pct=-cfg.sl * 100)
                        pos = 0; active = None
                    elif bar["high"] >= entry * (1 + cfg.tp):
                        exit_px = entry * (1 + cfg.tp)
                        result.trades.append({
                            "side": "L", "regime": active, "entry": entry,
                            "exit": exit_px,
                            "pnl_pct": cfg.tp * 100, "reason": "TP",
                            "ts": bar.get("ts"),
                        })
                        _log_exit(i, exit_px, side=1, pnl_pct=cfg.tp * 100)
                        pos = 0; active = None
                elif pos == -1:
                    if bar["high"] >= entry * (1 + cfg.sl):
                        exit_px = entry * (1 + cfg.sl)
                        result.trades.append({
                            "side": "S", "regime": active, "entry": entry,
                            "exit": exit_px,
                            "pnl_pct": -cfg.sl * 100, "reason": "SL",
                            "ts": bar.get("ts"),
                        })
                        _log_exit(i, exit_px, side=-1, pnl_pct=-cfg.sl * 100)
                        pos = 0; active = None
                    elif bar["low"] <= entry * (1 - cfg.tp):
                        exit_px = entry * (1 - cfg.tp)
                        result.trades.append({
                            "side": "S", "regime": active, "entry": entry,
                            "exit": exit_px,
                            "pnl_pct": cfg.tp * 100, "reason": "TP",
                            "ts": bar.get("ts"),
                        })
                        _log_exit(i, exit_px, side=-1, pnl_pct=cfg.tp * 100)
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
                # --- P3 confluence gate (HTF + level + CVD) ---
                if cfg.confluence_gate_on:
                    intended = "long" if target_pos == 1 else "short"
                    atr_value = (atr_s[i] if atr_s is not None and i < len(atr_s) else 0.0)
                    conf = _cs.evaluate(
                        bars=bars,
                        i=i,
                        intended=intended,
                        atr_value=atr_value,
                        asset_class=asset_class_for_cs or "crypto",
                    )
                    if not conf.passes:
                        result.filtered_confluence += 1
                        continue

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
