"""
Tests for P4.1 scale-out helper + config defaults.

This iter (P4.1 iter-1/5) ships the config flags and the small
`compose_scale_out_pnl` helper that the engine's exit branches will
use in iter-3/5. No engine.run() integration yet — that's iter-2 + 3.

The helper math is pure; testing it in isolation lets iter-3 wire it
in without re-deriving the averaging formula at each call site.
"""
from __future__ import annotations

import pytest

from propfirm_engine.engine import PropfirmConfig, compose_scale_out_pnl


# ---------------------------------------------------------------------------
# PropfirmConfig — new defaults (do not change existing P1/P3/P4 behavior)
# ---------------------------------------------------------------------------

def test_scale_out_defaults_are_off():
    """Default config must leave scale_out_at_1R False so P3/P4 runs
    unaffected by P4.1 work."""
    cfg = PropfirmConfig()
    assert cfg.scale_out_at_1R is False
    assert cfg.scale_out_fraction == 0.5
    assert cfg.remainder_tp == 0.045   # +3R when sl=1.5pp


def test_scale_out_remainder_tp_is_strictly_above_base_tp():
    """The whole point of scale-out is to give the remainder a longer
    runway than the original TP. Catch accidental regression."""
    cfg = PropfirmConfig()
    assert cfg.remainder_tp > cfg.tp


def test_one_R_equals_sl_distance():
    """Sanity: 1R is by convention the SL distance. The 0.045
    remainder_tp default is exactly 3 × sl (3R)."""
    cfg = PropfirmConfig()
    assert cfg.remainder_tp == pytest.approx(3 * cfg.sl, abs=1e-6)


# ---------------------------------------------------------------------------
# compose_scale_out_pnl — helper math
# ---------------------------------------------------------------------------

def test_pnl_without_scale_out_passes_through():
    # half_closed=False → ignore half_pnl/fraction, just return remainder.
    assert compose_scale_out_pnl(False, 0.0, 3.0, 0.5) == 3.0
    assert compose_scale_out_pnl(False, 99.0, -1.5, 0.5) == -1.5  # half_pnl
    # ignored


def test_pnl_winner_after_scale_out_at_half():
    # Half closed at +1R (+0.75pp locked in), remainder rides to +3R
    # (+4.5pp). Final pnl = 0.75 + 0.5 × 4.5 = 3.0pp.
    p = compose_scale_out_pnl(half_closed=True, half_pnl=0.75,
                              remainder_pnl=4.5, fraction=0.5)
    assert p == pytest.approx(3.0)


def test_pnl_loser_after_scale_out():
    # Half closed at +1R (+0.75pp locked in), remainder reverses to SL
    # (-1.5pp). Final pnl = 0.75 + 0.5 × (-1.5) = 0.0pp.
    # Scale-out converts what would have been a -1.5pp loser into
    # breakeven IF the trade got to +1R first.
    p = compose_scale_out_pnl(half_closed=True, half_pnl=0.75,
                              remainder_pnl=-1.5, fraction=0.5)
    assert p == pytest.approx(0.0)


def test_pnl_pure_loser_never_hit_1R():
    # Half never closed; trade exits at -1.5pp directly.
    p = compose_scale_out_pnl(half_closed=False, half_pnl=0.0,
                              remainder_pnl=-1.5, fraction=0.5)
    assert p == -1.5


def test_pnl_with_custom_fraction():
    # If user sets scale_out_fraction=0.3 (close 30% at +1R), the
    # remainder is the 70% that keeps riding.
    # half_pnl is 0.3 × 1.5pp = 0.45pp at +1R; remainder rides to +4.5pp
    # contributing 0.7 × 4.5 = 3.15pp. Total 3.6pp.
    p = compose_scale_out_pnl(half_closed=True, half_pnl=0.45,
                              remainder_pnl=4.5, fraction=0.3)
    assert p == pytest.approx(3.6)


def test_pnl_tp_hit_exactly_on_remainder():
    # Remainder hits the original +1R TP at +1.5pp (not riding to +3R):
    # 0.75 (half) + 0.5 × 1.5 = 1.5pp. STRICTLY LESS than a no-scale-out
    # full TP at +3pp. This is the math behind why scale-out is a
    # tradeoff — it costs runners.
    p = compose_scale_out_pnl(half_closed=True, half_pnl=0.75,
                              remainder_pnl=1.5, fraction=0.5)
    assert p == pytest.approx(1.5)


def test_pnl_remainder_runs_past_3R():
    # If we trail and remainder gets +6pp, scale-out total =
    # 0.75 + 3.0 = 3.75pp. Wins vs full-TP of 3pp by 0.75pp.
    # This is the leakage-capture case the design is aiming for.
    p = compose_scale_out_pnl(half_closed=True, half_pnl=0.75,
                              remainder_pnl=6.0, fraction=0.5)
    assert p == pytest.approx(3.75)


# ---------------------------------------------------------------------------
# End-to-end engine integration — scale_out_at_1R inside run()
# ---------------------------------------------------------------------------

from propfirm_engine.engine import PropfirmEngine


def _make_uptrend_bars(n: int, start: float = 100.0, step: float = 0.5):
    """Bars where price ticks straight up by `step` per bar."""
    return [{
        "ts": 1_700_000_000 + i * 3600,
        "open": start + i * step,
        "high": start + i * step + 0.1,
        "low":  start + i * step - 0.1,
        "close": start + i * step,
        "volume": 1000.0,
    } for i in range(n)]


def test_engine_scale_out_off_uses_base_tp():
    """With scale_out_at_1R=False the engine path is exactly P3 — TP fires
    at +cfg.tp (default +3pp), no half-close trade record."""
    cfg = PropfirmConfig(scale_out_at_1R=False, sl=0.015, tp=0.03)
    # Just verify the config path doesn't break anything.
    # (Real backtest with regime entries is iter-4/5's job; this only
    # confirms construction works.)
    eng = PropfirmEngine(cfg)
    bars = _make_uptrend_bars(200)
    result = eng.run(bars, "BTCUSDT")
    # No trades expected on synthetic regular bars (s1_trend_ema needs
    # real price action), but the run must complete without exception.
    assert isinstance(result.trades, list)


def test_engine_scale_out_on_does_not_crash():
    """Smoke test: scale_out_at_1R=True on synthetic bars completes
    without raising. Behavioural correctness is validated via the
    helper unit tests above + the iter-4/5 8-scenario backtest."""
    cfg = PropfirmConfig(scale_out_at_1R=True, sl=0.015, tp=0.03,
                         remainder_tp=0.045, scale_out_fraction=0.5)
    eng = PropfirmEngine(cfg)
    bars = _make_uptrend_bars(200)
    result = eng.run(bars, "BTCUSDT")
    assert isinstance(result.trades, list)
    # Defaults: remainder_tp=4.5%, fraction=0.5 — config plumbed through.
    assert eng.cfg.remainder_tp == 0.045
    assert eng.cfg.scale_out_fraction == 0.5
