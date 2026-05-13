"""
Propfirm v4 — P3 confluence scorer (scaffold).

Per spec §4.3 Phase Breakdown, P3 requires entries to satisfy a 3-of-3
confluence gate:

  1. HTF trend bias — HTF EMA agrees with intended direction
  2. Key level proximity — within 0.5 × ATR of VWAP or previous-day H/L
  3. CVD/volume-delta slope — aligned with intended direction

Risk C in the spec: CVD/volume-delta is noisy on Yahoo tick-volume data
for FX. Mitigation: FX skips signal #3 and uses a 2-of-2 gate (HTF +
level). This is encoded in `score_required()`.

This module is the iter-1/5 SCAFFOLD — interfaces and stubs in place,
real signal logic added in iter-2/5. Until then the helpers return the
neutral / no-signal value so the gate is effectively closed (no entries
fire). That keeps the scaffold safe to wire into engine.py without
accidentally producing a noisier engine than P1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Direction = Literal["long", "short", "none"]


@dataclass
class ConfluenceResult:
    """Per-bar evaluation of the 3-of-3 (or 2-of-2 on FX) gate."""
    direction: Direction       # intended trade direction (or "none" if regime says skip)
    htf_bias_aligned: bool     # signal 1
    level_proximity: bool      # signal 2
    cvd_aligned: bool          # signal 3 (skipped on FX)
    required: int              # how many signals must align (2 for FX, 3 otherwise)
    score: int                 # number of aligned signals

    @property
    def passes(self) -> bool:
        return self.score >= self.required


def _is_fx(asset_class: str) -> bool:
    """FX assets skip CVD per spec Risk C."""
    return asset_class == "fx"


def score_required(asset_class: str) -> int:
    """Number of confluence signals required to enter, per spec §4.3.

    FX: 2 (HTF + level), others: 3 (HTF + level + CVD).
    """
    return 2 if _is_fx(asset_class) else 3


def htf_trend_bias(
    bars: list[dict[str, Any]],
    i: int,
    htf_ema_period: int = 50,
    slope_lookback: int = 5,
) -> Direction:
    """HTF trend bias via EMA(htf_ema_period) on closes ending at bar i.

    'long' iff close[i] > EMA[i] AND EMA[i] > EMA[i - slope_lookback]
    'short' iff close[i] < EMA[i] AND EMA[i] < EMA[i - slope_lookback]
    'none' on insufficient history or mixed signal.

    We compute the EMA incrementally on the relevant tail to avoid O(N²)
    pathology when called per-bar in the engine loop. The implementation
    keeps a running EMA seeded with the SMA of the first `htf_ema_period`
    closes before bar i.
    """
    if i < htf_ema_period + slope_lookback:
        return "none"

    closes = [bars[k]["close"] for k in range(i - htf_ema_period - slope_lookback + 1, i + 1)]
    alpha = 2.0 / (htf_ema_period + 1)
    # Seed EMA with the SMA of the first `htf_ema_period` closes.
    ema = sum(closes[:htf_ema_period]) / htf_ema_period
    # Roll forward through the remaining `slope_lookback` bars + the
    # current bar so we end at EMA[i] and have EMA[i - slope_lookback].
    ema_prev = None
    for j, c in enumerate(closes[htf_ema_period:], start=htf_ema_period):
        if j == htf_ema_period + slope_lookback - 1:
            ema_prev = ema
        ema = alpha * c + (1 - alpha) * ema
    if ema_prev is None:
        return "none"

    close_now = bars[i]["close"]
    if close_now > ema and ema > ema_prev:
        return "long"
    if close_now < ema and ema < ema_prev:
        return "short"
    return "none"


def level_proximity(
    bars: list[dict[str, Any]],
    i: int,
    atr_value: float,
    threshold_atr_mult: float = 0.5,
    prev_session_bars: int = 24,
) -> bool:
    """Within threshold_atr_mult × ATR of a rolling-VWAP or prev-session H/L.

    "Prev session H/L" is approximated as the high/low over the `prev_session_bars`
    bars ending at bar i-1 (i.e. excluding the current bar itself, so we're not
    comparing a level to itself).

    Rolling VWAP is computed cumulatively over those same `prev_session_bars` bars
    using typical price (h+l+c)/3 × volume / volume. Bars without volume contribute
    zero (VWAP collapses to last typical price if all volumes are zero — safe).
    """
    if i < prev_session_bars or atr_value <= 0:
        return False

    window = bars[i - prev_session_bars:i]  # excludes bar i
    if not window:
        return False

    highs = [b["high"] for b in window]
    lows = [b["low"] for b in window]
    prev_h = max(highs)
    prev_l = min(lows)

    # Rolling VWAP across the same window
    num = 0.0
    den = 0.0
    for b in window:
        v = b.get("volume", 0) or 0
        if v <= 0:
            continue
        typ = (b["high"] + b["low"] + b["close"]) / 3
        num += typ * v
        den += v
    vwap = (num / den) if den > 0 else window[-1]["close"]

    close_now = bars[i]["close"]
    thresh = threshold_atr_mult * atr_value
    near_vwap = abs(close_now - vwap) <= thresh
    near_high = abs(close_now - prev_h) <= thresh
    near_low = abs(close_now - prev_l) <= thresh
    return near_vwap or near_high or near_low


def cvd_slope_aligned(
    bars: list[dict[str, Any]],
    i: int,
    intended: Direction,
    lookback: int = 20,
) -> bool:
    """Cumulative volume-delta slope aligned with intended direction.

    Volume-delta per bar: +volume if close>open, -volume if close<open, else 0.
    CVD = running sum. Slope proxy = CVD[i] - CVD[i - lookback]. If volume data
    is missing/zero throughout (e.g. some Yahoo FX series), the slope is 0 and
    we return False — which is correct: the FX path doesn't use this signal
    anyway (the caller skips it via `_is_fx`).
    """
    if intended == "none" or i < lookback:
        return False

    cvd_now = 0.0
    cvd_then = 0.0
    for k, b in enumerate(bars[: i + 1]):
        v = b.get("volume", 0) or 0
        if v <= 0:
            continue
        co = b["close"]
        op = b["open"]
        signed = v if co > op else (-v if co < op else 0)
        cvd_now += signed
        if k == i - lookback:
            cvd_then = cvd_now
    slope = cvd_now - cvd_then
    if intended == "long":
        return slope > 0
    if intended == "short":
        return slope < 0
    return False


def evaluate(
    bars: list[dict[str, Any]],
    i: int,
    intended: Direction,
    atr_value: float,
    asset_class: str,
) -> ConfluenceResult:
    """Score the confluence gate for a candidate entry at bar i.

    Returns a ConfluenceResult; engine.py should treat `result.passes` as
    the entry-permission boolean.
    """
    required = score_required(asset_class)

    htf = htf_trend_bias(bars, i)
    htf_aligned = (htf == intended) and (intended != "none")

    level_ok = level_proximity(bars, i, atr_value)

    if _is_fx(asset_class):
        cvd_ok = True  # signal 3 not used for FX; mark True so it doesn't drag the score
        score = int(htf_aligned) + int(level_ok)
    else:
        cvd_ok = cvd_slope_aligned(bars, i, intended)
        score = int(htf_aligned) + int(level_ok) + int(cvd_ok)

    return ConfluenceResult(
        direction=intended,
        htf_bias_aligned=htf_aligned,
        level_proximity=level_ok,
        cvd_aligned=cvd_ok,
        required=required,
        score=score,
    )
