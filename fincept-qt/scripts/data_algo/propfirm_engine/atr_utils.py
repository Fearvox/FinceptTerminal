"""
Propfirm v4 P2 — ATR chandelier trailing stop utilities.

`atr(bars, period)` — Wilder's ATR, returns a list[float] aligned to bars.

`chandelier_long_sl(entry_idx, cur_idx, bars, atr_series, mult)` —
    returns the current trailing stop for a long entry: the highest high
    seen since entry (inclusive) minus `mult × ATR[cur_idx]`. Never
    retreats: caller uses max(prev_sl, this_sl) to enforce the ratchet.

`chandelier_short_sl(...)` — symmetric: lowest low since entry plus
    `mult × ATR`. Caller uses min(prev_sl, this_sl).

Default multiplier is 1.5× per spec §4.3. BTC 15m is widened to 2.0×
per spec Risk B via `multiplier_for(symbol, interval)`.

No breakeven move. Ever. (Spec §4.1 — `trade_state_machine.py` deleted
in this phase.)
"""
from __future__ import annotations

from typing import Any

from .vendor.strategy_bake_off import atr as _vendor_atr

Bar = dict[str, Any]


def atr(bars: list[Bar], period: int = 14) -> list[float]:
    """Wilder's ATR. Delegates to the vendor implementation (same math,
    avoids a second copy of the formula)."""
    return _vendor_atr(bars, period)


def multiplier_for(symbol: str, interval: str | None = None,
                   asset_class: str | None = None,
                   default: float = 1.5, btc_15m: float = 2.0) -> float:
    """Return the ATR multiplier for the given (symbol, interval, asset_class).

    Two layers of override over the spec's 1.5× default:

    1. Per-instrument exception (Risk B, original spec): BTC 15m widens
       to 2.0× because low-ATR crypto ranges at 15m granularity trip
       1.5× trails too often. Takes precedence over the asset-class
       table below.

    2. Per-asset-class table (P2 mult experiment, 2026-05-13). Phase 2
       attempts 1+2 both failed pass gate because range regimes (FX,
       gold) have normal intrabar swing ≈ 1.5× ATR — the trail fires
       inside noise even with regime-conditional logic. This table
       widens the multiplier on those asset classes ONCE, no online
       sweep:

         fx     → 2.5×
         gold   → 2.0×
         oil    → 1.5×
         equity → 1.5×
         crypto → 1.5× (1h), already 2.0× for BTC 15m above

       Governance: this table is fixed. Any future change must be a
       separate experiment with its own report; values are not free
       parameters tunable per-run. Callers that don't pass
       `asset_class` still get the spec default.
    """
    if interval == "15m" and symbol.upper().startswith("BTC"):
        return btc_15m
    if asset_class:
        per_asset = {
            "fx": 2.5,
            "gold": 2.0,
            "oil": 1.5,
            "equity": 1.5,
            "crypto": 1.5,
        }
        return per_asset.get(asset_class, default)
    return default


def chandelier_long_sl(entry_idx: int, cur_idx: int,
                       bars: list[Bar], atr_series: list[float],
                       mult: float = 1.5) -> float:
    """Current chandelier SL for a long position opened at entry_idx.

    SL_cur = max_high(entry_idx .. cur_idx) - mult × ATR[cur_idx]

    The caller is responsible for enforcing the ratchet (never retreats)
    by holding max(prev_sl, this_sl). Returns 0.0 if cur_idx < entry_idx
    or indices are out of range (defensive — callers shouldn't hit this)."""
    if cur_idx < entry_idx or cur_idx >= len(bars) or cur_idx >= len(atr_series):
        return 0.0
    highest = max(b["high"] for b in bars[entry_idx:cur_idx + 1])
    return highest - mult * atr_series[cur_idx]


def chandelier_short_sl(entry_idx: int, cur_idx: int,
                        bars: list[Bar], atr_series: list[float],
                        mult: float = 1.5) -> float:
    """Current chandelier SL for a short position opened at entry_idx.

    SL_cur = min_low(entry_idx .. cur_idx) + mult × ATR[cur_idx]

    Caller enforces ratchet with min(prev_sl, this_sl)."""
    if cur_idx < entry_idx or cur_idx >= len(bars) or cur_idx >= len(atr_series):
        return 0.0
    lowest = min(b["low"] for b in bars[entry_idx:cur_idx + 1])
    return lowest + mult * atr_series[cur_idx]
