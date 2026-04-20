"""
Tests for propfirm_engine.session_filter.

Plan §Phase 1 lists four required cases; we add a few edge cases around
asset classification and calendar parsing to reach spec §7 80% coverage.
Run with:  cd fincept-qt/scripts/data_algo && python3 -m pytest propfirm_engine/tests -q
"""
from datetime import datetime, timezone

import pytest

from propfirm_engine import session_filter as sf


# ── Plan-required four cases ────────────────────────────────────────────

def test_fx_bar_at_noon_utc_is_tradeable():
    ts = datetime(2026, 6, 15, 13, 0, tzinfo=timezone.utc)
    assert sf.is_trading_hour(ts, "fx") is True


def test_fx_bar_at_4am_utc_is_blocked():
    ts = datetime(2026, 6, 15, 4, 0, tzinfo=timezone.utc)
    assert sf.is_trading_hour(ts, "fx") is False


def test_crypto_bar_during_wolf_hour_is_blocked():
    ts = datetime(2026, 6, 15, 3, 0, tzinfo=timezone.utc)
    assert sf.is_trading_hour(ts, "crypto") is False


def test_bar_10min_before_fomc_is_near_news():
    # 2026-04-29 18:00 UTC FOMC → 10 min before = 17:50 UTC
    ts = datetime(2026, 4, 29, 17, 50, tzinfo=timezone.utc)
    assert sf.is_near_news(ts) is True


# ── Asset class routing ─────────────────────────────────────────────────

@pytest.mark.parametrize("symbol,expected", [
    ("BTCUSDT", "crypto"),
    ("ETHUSDC", "crypto"),
    ("EURUSD=X", "fx"),
    ("GBPUSD=X", "fx"),
    ("GC=F", "gold"),
    ("CL=F", "oil"),
    ("BZ=F", "oil"),
    ("SPY", "equity"),
    ("QQQ", "equity"),
    ("AAPL", "equity"),
])
def test_symbol_classification(symbol, expected):
    assert sf.classify_symbol(symbol) == expected


# ── Session masks by asset class ────────────────────────────────────────

def test_crypto_outside_wolf_hour_is_tradeable():
    # 01:00 UTC and 05:00 UTC should both pass
    assert sf.is_trading_hour(datetime(2026, 6, 1, 1, 0, tzinfo=timezone.utc), "crypto")
    assert sf.is_trading_hour(datetime(2026, 6, 1, 5, 0, tzinfo=timezone.utc), "crypto")


def test_crypto_wolf_hour_edges():
    # 02:30 UTC (inclusive start of Wolf) → blocked
    assert not sf.is_trading_hour(datetime(2026, 6, 1, 2, 30, tzinfo=timezone.utc), "crypto")
    # 02:29 UTC → allowed (before Wolf)
    assert sf.is_trading_hour(datetime(2026, 6, 1, 2, 29, tzinfo=timezone.utc), "crypto")
    # 03:59 UTC → blocked (inside Wolf)
    assert not sf.is_trading_hour(datetime(2026, 6, 1, 3, 59, tzinfo=timezone.utc), "crypto")
    # 04:00 UTC (exclusive end of Wolf) → allowed
    assert sf.is_trading_hour(datetime(2026, 6, 1, 4, 0, tzinfo=timezone.utc), "crypto")


def test_equity_has_two_windows():
    # NY open 13:30-15:00 UTC
    assert sf.is_trading_hour(datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc), "equity")
    # Between open and close → blocked
    assert not sf.is_trading_hour(datetime(2026, 6, 1, 17, 0, tzinfo=timezone.utc), "equity")
    # NY close 19:30-20:00 UTC
    assert sf.is_trading_hour(datetime(2026, 6, 1, 19, 45, tzinfo=timezone.utc), "equity")


def test_oil_single_window():
    assert sf.is_trading_hour(datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc), "oil")
    assert not sf.is_trading_hour(datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc), "oil")
    assert not sf.is_trading_hour(datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc), "oil")


# ── ts input flexibility ────────────────────────────────────────────────

def test_accepts_iso_string():
    assert sf.is_trading_hour("2026-06-15T13:00:00Z", "fx")


def test_accepts_epoch_seconds():
    # 2026-06-15T13:00:00Z ≈ 1781179200
    ts = int(datetime(2026, 6, 15, 13, 0, tzinfo=timezone.utc).timestamp())
    assert sf.is_trading_hour(ts, "fx")


def test_accepts_epoch_millis():
    ts_ms = int(datetime(2026, 6, 15, 13, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert sf.is_trading_hour(ts_ms, "fx")


def test_accepts_symbol_instead_of_class():
    ts = datetime(2026, 6, 15, 13, 0, tzinfo=timezone.utc)
    assert sf.is_trading_hour(ts, "EURUSD=X")  # routes via classify_symbol


# ── News windows ────────────────────────────────────────────────────────

def test_bar_outside_calendar_coverage_is_allowed():
    # 2027 has no calendar entries → should default to not-near-news
    ts = datetime(2027, 8, 15, 12, 0, tzinfo=timezone.utc)
    assert sf.is_near_news(ts) is False


def test_fomc_window_boundaries():
    # 2026-04-29 18:00 UTC FOMC with ±15 min window
    ev_ts = datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)
    # Exactly at event → near
    assert sf.is_near_news(ev_ts)
    # 14 min before → near
    assert sf.is_near_news(datetime(2026, 4, 29, 17, 46, tzinfo=timezone.utc))
    # 16 min after → outside
    assert not sf.is_near_news(datetime(2026, 4, 29, 18, 16, tzinfo=timezone.utc))


def test_cpi_window():
    # 2026-05-12 12:30 UTC CPI → 5 min before blocked
    assert sf.is_near_news(datetime(2026, 5, 12, 12, 25, tzinfo=timezone.utc))


# ── Combinator ──────────────────────────────────────────────────────────

def test_is_tradeable_requires_both():
    # FX at 13:00 UTC on a day with no nearby news → tradeable
    assert sf.is_tradeable(datetime(2026, 6, 15, 13, 0, tzinfo=timezone.utc), "fx")
    # Crypto at 03:00 UTC (Wolf Hour) → not tradeable
    assert not sf.is_tradeable(datetime(2026, 6, 15, 3, 0, tzinfo=timezone.utc), "BTCUSDT")
    # FX at 13:00 UTC on FOMC day near window → not tradeable
    assert not sf.is_tradeable(datetime(2026, 4, 29, 17, 55, tzinfo=timezone.utc), "fx")
