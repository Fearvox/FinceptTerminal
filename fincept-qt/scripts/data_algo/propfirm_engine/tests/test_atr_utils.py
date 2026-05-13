"""
Tests for propfirm_engine.atr_utils — chandelier trailing stop primitives.

Plan §Phase 2 requires two core cases; we add boundary + multiplier
routing tests for coverage.
Run: cd fincept-qt/scripts/data_algo && python3 -m pytest propfirm_engine/tests -q
"""
import pytest

from propfirm_engine import atr_utils as au


def _flat_bars(n: int, price: float = 100.0) -> list[dict]:
    """Bars with zero range — ATR should be zero, chandelier SL == price."""
    return [{"open": price, "high": price, "low": price, "close": price,
             "volume": 1000.0} for _ in range(n)]


def _rising_bars(n: int, start: float = 100.0, step: float = 1.0,
                 rng: float = 0.5) -> list[dict]:
    """Monotone rising bars with fixed range for deterministic ATR."""
    out = []
    for i in range(n):
        c = start + i * step
        out.append({"open": c - step/2, "high": c + rng, "low": c - rng,
                    "close": c, "volume": 1000.0})
    return out


# ── Multiplier routing ──────────────────────────────────────────────────

def test_multiplier_default():
    assert au.multiplier_for("BTCUSDT", "1h") == 1.5
    assert au.multiplier_for("EURUSD=X", "1h") == 1.5


def test_multiplier_btc_15m_widened():
    assert au.multiplier_for("BTCUSDT", "15m") == 2.0
    # ETH 15m does NOT get the widened mult — only BTC (spec Risk B)
    assert au.multiplier_for("ETHUSDT", "15m") == 1.5


def test_multiplier_custom_defaults():
    assert au.multiplier_for("BTCUSDT", "1h", default=1.8) == 1.8
    assert au.multiplier_for("BTCUSDT", "15m", btc_15m=2.5) == 2.5


# ── ATR delegation ──────────────────────────────────────────────────────

def test_atr_flat_bars_returns_zero():
    bars = _flat_bars(40)
    atr_s = au.atr(bars, 14)
    # After warmup, ATR on zero-range bars is zero
    assert atr_s[-1] == 0


def test_atr_has_expected_length():
    bars = _rising_bars(50)
    atr_s = au.atr(bars, 14)
    assert len(atr_s) == len(bars)


# ── Chandelier long ──────────────────────────────────────────────────────

def test_chandelier_long_never_retreats_over_5_atr_rise():
    """Plan requirement: Long rising 5×ATR — trailing SL must never move down."""
    bars = _rising_bars(60, start=100.0, step=0.5, rng=0.5)
    atr_s = au.atr(bars, 14)
    entry_idx = 20
    prev_sl = 0.0
    retreats = 0
    for i in range(entry_idx, len(bars)):
        sl = au.chandelier_long_sl(entry_idx, i, bars, atr_s, mult=1.5)
        # enforce ratchet from caller side (engine.py does max(prev_sl, sl))
        ratcheted = max(prev_sl, sl)
        if ratcheted < prev_sl - 1e-9:
            retreats += 1
        prev_sl = ratcheted
    assert retreats == 0


def test_chandelier_long_is_entry_minus_mult_atr_at_entry_bar():
    bars = _rising_bars(60, start=100.0, step=0.5, rng=0.5)
    atr_s = au.atr(bars, 14)
    entry_idx = 20
    sl = au.chandelier_long_sl(entry_idx, entry_idx, bars, atr_s, mult=1.5)
    # at entry bar, highest high over [entry_idx..entry_idx] = bars[entry_idx]["high"]
    expected = bars[entry_idx]["high"] - 1.5 * atr_s[entry_idx]
    assert sl == pytest.approx(expected)


def test_chandelier_long_out_of_range_returns_zero():
    bars = _rising_bars(40)
    atr_s = au.atr(bars, 14)
    assert au.chandelier_long_sl(30, 20, bars, atr_s, 1.5) == 0.0  # cur < entry
    assert au.chandelier_long_sl(30, 100, bars, atr_s, 1.5) == 0.0  # cur >= len(bars)


# ── Chandelier short ─────────────────────────────────────────────────────

def _falling_bars(n: int, start: float = 100.0, step: float = 1.0,
                  rng: float = 0.5) -> list[dict]:
    out = []
    for i in range(n):
        c = start - i * step
        out.append({"open": c + step/2, "high": c + rng, "low": c - rng,
                    "close": c, "volume": 1000.0})
    return out


def test_chandelier_short_never_retreats_over_3_atr_fall():
    """Plan requirement: Short falling 3×ATR — trailing SL must never move up."""
    bars = _falling_bars(60, start=100.0, step=0.5, rng=0.5)
    atr_s = au.atr(bars, 14)
    entry_idx = 20
    prev_sl = float("inf")
    retreats = 0
    for i in range(entry_idx, len(bars)):
        sl = au.chandelier_short_sl(entry_idx, i, bars, atr_s, mult=1.5)
        ratcheted = sl if sl < prev_sl else prev_sl
        if ratcheted > prev_sl + 1e-9:
            retreats += 1
        prev_sl = ratcheted
    assert retreats == 0


def test_chandelier_short_is_entry_plus_mult_atr_at_entry_bar():
    bars = _falling_bars(60, start=100.0, step=0.5, rng=0.5)
    atr_s = au.atr(bars, 14)
    entry_idx = 20
    sl = au.chandelier_short_sl(entry_idx, entry_idx, bars, atr_s, mult=1.5)
    expected = bars[entry_idx]["low"] + 1.5 * atr_s[entry_idx]
    assert sl == pytest.approx(expected)
