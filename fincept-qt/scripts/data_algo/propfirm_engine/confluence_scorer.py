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
) -> Direction:
    """SCAFFOLD: returns 'none' until iter-2 implements HTF EMA logic.

    The iter-2 implementation should:
    - Compute EMA over `htf_ema_period` closes ending at bar i
    - Return 'long' if close[i] > EMA[i] AND EMA slope positive
    - Return 'short' if close[i] < EMA[i] AND EMA slope negative
    - Return 'none' otherwise
    """
    # iter-1 scaffold: no signal yet → gate cannot fire on this alone.
    return "none"


def level_proximity(
    bars: list[dict[str, Any]],
    i: int,
    atr_value: float,
    threshold_atr_mult: float = 0.5,
) -> bool:
    """SCAFFOLD: returns False until iter-2 implements VWAP / prior-day H/L.

    The iter-2 implementation should:
    - Compute session VWAP up to bar i (cumulative typical-price × volume / volume)
    - Compute previous trading day's H and L
    - Return True if |close[i] - VWAP| <= threshold_atr_mult * ATR
                 OR if close[i] is within threshold_atr_mult * ATR of yesterday's H or L
    """
    return False


def cvd_slope_aligned(
    bars: list[dict[str, Any]],
    i: int,
    intended: Direction,
    lookback: int = 20,
) -> bool:
    """SCAFFOLD: returns False until iter-2 implements CVD slope.

    The iter-2 implementation should:
    - Build cumulative volume-delta (up_vol − down_vol) over bars[..i]
    - Compute slope over the last `lookback` bars (simple linear regression or
      first-difference average)
    - Return True if slope > 0 and intended == 'long'
               OR slope < 0 and intended == 'short'
    """
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
