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
