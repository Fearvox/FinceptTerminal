"""
Tests for confluence_scorer (P3.1 redesign — level breakout).

P3 attempt 1 (close NEAR a level) failed the pass gate structurally —
the "near" semantic fought s1_trend_ema's "push through" geometry. P3.1
redefines signal #2 as a direction-aware breakout: long entries require
close > prev_h + 0.25 × ATR (or > VWAP + 0.25 × ATR); short mirrors.

These tests cover the breakout semantic specifically, not the prior
proximity rule. Old tests that asserted proximity behavior were replaced.
"""
from __future__ import annotations

import pytest

from propfirm_engine import confluence_scorer as cs


# ---------------------------------------------------------------------------
# Gate-size rule (FX uses 2-of-2, others use 3-of-3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "asset_class,expected",
    [
        ("fx", 2),
        ("crypto", 3),
        ("equity", 3),
        ("oil", 3),
        ("gold", 3),
    ],
)
def test_score_required_per_asset(asset_class, expected):
    assert cs.score_required(asset_class) == expected


# ---------------------------------------------------------------------------
# Scaffold stubs return safe "no signal" outputs
# ---------------------------------------------------------------------------

def test_htf_trend_bias_scaffold_returns_none():
    # Until iter-2 wires real EMA logic, the helper must return 'none'
    # so the confluence gate cannot fire by accident.
    assert cs.htf_trend_bias([], 0) == "none"


def test_level_breakout_no_history_returns_false():
    # With i=0 there's no prev-session window to compute levels against;
    # function must safely return False regardless of intended direction.
    assert cs.level_breakout([], 0, intended="long", atr_value=1.0) is False
    assert cs.level_breakout([], 0, intended="short", atr_value=1.0) is False


def test_level_breakout_returns_false_on_none_direction():
    # 'none' direction => no breakout to evaluate
    bars = [{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
            for _ in range(30)]
    assert cs.level_breakout(bars, i=29, intended="none", atr_value=1.0) is False


def test_cvd_slope_aligned_scaffold_returns_false():
    assert cs.cvd_slope_aligned([], 0, intended="long") is False


# ---------------------------------------------------------------------------
# evaluate() integration on the scaffold — should NEVER pass the gate yet
# ---------------------------------------------------------------------------

def test_evaluate_crypto_empty_bars_does_not_pass():
    r = cs.evaluate(bars=[], i=0, intended="long", atr_value=1.0, asset_class="crypto")
    assert r.required == 3
    assert r.score == 0          # no history → all signals fail
    assert r.passes is False
    assert r.htf_bias_aligned is False
    assert r.level_breakout is False
    assert r.cvd_aligned is False


def test_evaluate_fx_skips_cvd_but_still_does_not_pass_on_empty_bars():
    r = cs.evaluate(bars=[], i=0, intended="long", atr_value=1.0, asset_class="fx")
    assert r.required == 2
    # cvd_aligned is set True for FX (skipped, not penalised),
    # but htf and level are both False on empty bars, so score = 0.
    assert r.cvd_aligned is True
    assert r.htf_bias_aligned is False
    assert r.level_breakout is False
    assert r.score == 0
    assert r.passes is False


def test_evaluate_none_direction_keeps_htf_false():
    """When `intended` is 'none' the gate cannot pass regardless of HTF."""
    r = cs.evaluate(bars=[], i=0, intended="none", atr_value=1.0, asset_class="crypto")
    assert r.htf_bias_aligned is False
    assert r.passes is False


# ---------------------------------------------------------------------------
# Behavioral tests for the iter-2/5 helper implementations
# ---------------------------------------------------------------------------

def _bar(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def test_htf_trend_bias_long_on_uptrend():
    # Steadily rising closes: EMA[i] > EMA[i-5], close > EMA → 'long'
    bars = [_bar(100 + j*0.1, 101 + j*0.1, 99 + j*0.1, 100.5 + j*0.1) for j in range(70)]
    assert cs.htf_trend_bias(bars, i=69, htf_ema_period=50) == "long"


def test_htf_trend_bias_short_on_downtrend():
    bars = [_bar(100 - j*0.1, 101 - j*0.1, 99 - j*0.1, 100.5 - j*0.1) for j in range(70)]
    assert cs.htf_trend_bias(bars, i=69, htf_ema_period=50) == "short"


def test_htf_trend_bias_none_on_insufficient_history():
    bars = [_bar(100, 101, 99, 100.5) for _ in range(30)]
    assert cs.htf_trend_bias(bars, i=29, htf_ema_period=50) == "none"


def test_level_breakout_long_true_when_close_above_prev_high_plus_threshold():
    # 24-bar window with prev_h=105. Current bar closes at 110.
    # threshold = 0.25 * ATR(2) = 0.5; 110 > 105 + 0.5 → True for long.
    bars = [_bar(100, 105, 99, 102) for _ in range(24)]
    bars.append(_bar(105, 111, 104, 110))
    assert cs.level_breakout(bars, i=24, intended="long",
                             atr_value=2.0, prev_session_bars=24) is True


def test_level_breakout_long_false_when_close_only_AT_prev_high():
    # Old P3 attempt-1 semantic ("near") would return True here. New
    # P3.1 semantic requires close to be BEYOND prev_h by ≥ threshold.
    # Design window so VWAP ≈ prev_h (close=high=104.9, low=104.5):
    # both reference levels are ~104.9; current close=105 is only
    # 0.1 above, threshold = 0.25 × ATR(2) = 0.5 — not enough → False.
    bars = [_bar(104.7, 104.9, 104.5, 104.9, v=1000) for _ in range(24)]
    bars.append(_bar(104.8, 105.0, 104.7, 105.0, v=1000))
    assert cs.level_breakout(bars, i=24, intended="long",
                             atr_value=2.0, prev_session_bars=24) is False


def test_level_breakout_short_true_when_close_below_prev_low_minus_threshold():
    # prev_l=95; close at 90 < 95 - 0.5 → True for short
    bars = [_bar(100, 105, 95, 100) for _ in range(24)]
    bars.append(_bar(95, 96, 89, 90))
    assert cs.level_breakout(bars, i=24, intended="short",
                             atr_value=2.0, prev_session_bars=24) is True


def test_level_breakout_long_false_for_short_direction_breakout():
    # Sanity: if the price breaks DOWN but we asked for 'long' breakout,
    # the level signal must return False (direction-aware).
    bars = [_bar(100, 105, 95, 100) for _ in range(24)]
    bars.append(_bar(95, 96, 89, 90))
    assert cs.level_breakout(bars, i=24, intended="long",
                             atr_value=2.0, prev_session_bars=24) is False


def test_level_breakout_false_when_inside_range():
    # 24-bar window of stable ~100 prices, current bar stays inside range
    bars = [_bar(100, 100.5, 99.5, 100) for _ in range(24)]
    bars.append(_bar(100, 100.3, 99.8, 100.1))
    assert cs.level_breakout(bars, i=24, intended="long",
                             atr_value=0.5, prev_session_bars=24) is False
    assert cs.level_breakout(bars, i=24, intended="short",
                             atr_value=0.5, prev_session_bars=24) is False


def test_cvd_slope_aligned_long_on_buy_pressure():
    # 21 up-bars (close > open with volume) → cumulative volume-delta strongly positive
    bars = [_bar(100, 102, 99, 101, v=1000) for _ in range(21)]
    assert cs.cvd_slope_aligned(bars, i=20, intended="long", lookback=20) is True


def test_cvd_slope_aligned_returns_false_on_sell_pressure_for_long():
    bars = [_bar(100, 101, 98, 99, v=1000) for _ in range(21)]
    assert cs.cvd_slope_aligned(bars, i=20, intended="long", lookback=20) is False


def test_cvd_slope_aligned_returns_false_on_zero_volume_data():
    # FX-style data with volume=0 → CVD slope always 0 → returns False.
    # Caller (evaluate) skips CVD for FX anyway, but the function itself must
    # not throw or return spurious True.
    bars = [_bar(100, 101, 99, 100.5, v=0) for _ in range(21)]
    assert cs.cvd_slope_aligned(bars, i=20, intended="long", lookback=20) is False


def test_evaluate_passes_on_aligned_uptrend_with_breakout():
    # Build a rising series, then add one bar that breaks DECISIVELY
    # above the prior 24-bar high. HTF says long + level_breakout fires
    # long + CVD slope long → all 3 signals align.
    bars = [_bar(100 + j*0.1, 101 + j*0.1, 99 + j*0.1, 100.5 + j*0.1) for j in range(70)]
    # Bar 70 closes well above the prior bar's high to trigger level_breakout
    last_high = bars[-1]["high"]
    bars.append(_bar(last_high + 0.5, last_high + 2.0, last_high + 0.3,
                     last_high + 1.5, v=2000))
    r = cs.evaluate(bars=bars, i=70, intended="long", atr_value=1.0, asset_class="crypto")
    assert r.htf_bias_aligned is True
    assert r.level_breakout is True
    assert r.cvd_aligned is True
    assert r.score == 3
    assert r.passes is True
