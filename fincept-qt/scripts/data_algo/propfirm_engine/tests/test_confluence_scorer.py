"""
Tests for confluence_scorer (P3 scaffold).

Iter-1/5 of the P3 sweep ships interfaces + safe stubs that all return
the "no signal" value. These tests lock in the *interface contract* and
the FX/non-FX gate-size rule. Iter-2/5 will add behavioral tests for
the real HTF / level / CVD implementations.
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


def test_level_proximity_scaffold_returns_false():
    assert cs.level_proximity([], 0, atr_value=1.0) is False


def test_cvd_slope_aligned_scaffold_returns_false():
    assert cs.cvd_slope_aligned([], 0, intended="long") is False


# ---------------------------------------------------------------------------
# evaluate() integration on the scaffold — should NEVER pass the gate yet
# ---------------------------------------------------------------------------

def test_evaluate_crypto_scaffold_does_not_pass():
    r = cs.evaluate(bars=[], i=0, intended="long", atr_value=1.0, asset_class="crypto")
    assert r.required == 3
    assert r.score == 0          # all three signals stubbed to no-signal
    assert r.passes is False
    assert r.htf_bias_aligned is False
    assert r.level_proximity is False
    assert r.cvd_aligned is False


def test_evaluate_fx_skips_cvd_but_still_does_not_pass_on_scaffold():
    r = cs.evaluate(bars=[], i=0, intended="long", atr_value=1.0, asset_class="fx")
    assert r.required == 2
    # cvd_aligned is set True for FX (skipped, not penalised),
    # but htf and level are both False on the scaffold, so score = 0.
    assert r.cvd_aligned is True
    assert r.htf_bias_aligned is False
    assert r.level_proximity is False
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


def test_level_proximity_true_when_close_at_prev_high():
    bars = [_bar(100, 105, 99, 102) for _ in range(24)]
    bars.append(_bar(102, 106, 101, 105))  # close pinned at the running high
    assert cs.level_proximity(bars, i=24, atr_value=2.0, prev_session_bars=24) is True


def test_level_proximity_false_when_far_from_levels():
    # 24-bar window of stable ~100 prices, then current bar wanders to 130
    bars = [_bar(100, 100.5, 99.5, 100) for _ in range(24)]
    bars.append(_bar(130, 131, 129, 130))
    # ATR=0.5, threshold = 0.25; gap of 30 is way outside any level
    assert cs.level_proximity(bars, i=24, atr_value=0.5, prev_session_bars=24) is False


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


def test_evaluate_passes_on_aligned_uptrend_with_close_at_recent_high():
    # Build a rising series, then the latest bar's close is at the running
    # high — so HTF long + level proximity at prev-high + CVD aligned long.
    bars = [_bar(100 + j*0.1, 101 + j*0.1, 99 + j*0.1, 100.5 + j*0.1) for j in range(70)]
    # Bar 70 closes at the prior bar's high to trigger level_proximity
    last_high = bars[-1]["high"]
    bars.append(_bar(last_high - 0.1, last_high + 0.3, last_high - 0.5, last_high, v=2000))
    r = cs.evaluate(bars=bars, i=70, intended="long", atr_value=1.0, asset_class="crypto")
    assert r.htf_bias_aligned is True
    assert r.level_proximity is True
    assert r.cvd_aligned is True
    assert r.score == 3
    assert r.passes is True
