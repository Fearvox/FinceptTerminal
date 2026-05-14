"""Tests for notify_signal — position sizing math + side-aware + leverage cap."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from propfirm_engine.notify_signal import (
    compute_position_size,
    notify_entry,
    notify_exit,
    _format_entry_plan,
    _format_exit_summary,
)


# ── sizing math ────────────────────────────────────────────────────────────

def test_position_size_long_basic():
    # $100k account, 1% risk = $1000. Entry 100, SL 99 → 1$ distance → 1000 units
    s = compute_position_size(entry=100.0, sl=99.0, account_size=100_000, risk_pct=0.01)
    assert s["units"] == pytest.approx(1000.0)
    assert s["risk_dollars"] == pytest.approx(1000.0)
    assert s["notional"] == pytest.approx(100_000.0)
    assert s["leverage"] == pytest.approx(1.0)
    assert s["sl_distance_pct"] == pytest.approx(1.0)


def test_position_size_short_same_distance():
    # Short: entry 100, SL 101 → same distance, same size
    s = compute_position_size(entry=100.0, sl=101.0, account_size=100_000, risk_pct=0.01)
    assert s["units"] == pytest.approx(1000.0)
    assert s["sl_distance_pct"] == pytest.approx(1.0)


def test_position_size_tight_sl_increases_units():
    # SL 0.1 away → 10x more units
    s = compute_position_size(entry=100.0, sl=99.9, account_size=100_000, risk_pct=0.01)
    assert s["units"] == pytest.approx(10_000.0)
    assert s["leverage"] == pytest.approx(10.0)  # 10x — over default cap


def test_position_size_zero_distance_raises():
    with pytest.raises(ValueError, match="SL distance is zero"):
        compute_position_size(entry=100.0, sl=100.0)


def test_position_size_invalid_entry_raises():
    with pytest.raises(ValueError, match="entry must be > 0"):
        compute_position_size(entry=0.0, sl=99.0)
    with pytest.raises(ValueError, match="entry must be > 0"):
        compute_position_size(entry=-5.0, sl=99.0)


def test_position_size_invalid_sl_raises():
    with pytest.raises(ValueError, match="sl must be > 0"):
        compute_position_size(entry=100.0, sl=0.0)


def test_position_size_btc_realistic():
    # BTCUSD-style: $100k entry, $99k SL = $1k distance, $1k risk → 1 BTC
    s = compute_position_size(entry=100_000, sl=99_000, account_size=100_000, risk_pct=0.01)
    assert s["units"] == pytest.approx(1.0)
    assert s["notional"] == pytest.approx(100_000.0)
    assert s["leverage"] == pytest.approx(1.0)


# ── leverage cap behaviour ─────────────────────────────────────────────────

def test_notify_entry_skips_on_leverage_cap():
    # Tight SL → 10x leverage → over default 5x cap → skipped, no IO fires
    with patch("propfirm_engine.notify_signal._fire_osascript_notification") as mock_notif, \
         patch("propfirm_engine.notify_signal._fire_pbcopy") as mock_pbcopy:
        result = notify_entry(
            symbol="TIGHTSL",
            side="long",
            entry=100.0,
            sl=99.9,  # → 10x leverage
            tp=101.0,
            setup="willy_long_a",
            max_leverage=5.0,
        )
    assert result["skipped"] is True
    assert result["reason"] == "leverage_cap"
    mock_notif.assert_not_called()
    mock_pbcopy.assert_not_called()


def test_notify_entry_fires_when_under_cap():
    with patch("propfirm_engine.notify_signal._fire_osascript_notification") as mock_notif, \
         patch("propfirm_engine.notify_signal._fire_pbcopy") as mock_pbcopy:
        result = notify_entry(
            symbol="BTCUSD",
            side="long",
            entry=100_000,
            sl=99_000,  # 1x leverage
            tp=102_000,
            setup="willy_long_a",
            row_id=42,
            max_leverage=5.0,
        )
    assert result["skipped"] is False
    assert result["units"] == pytest.approx(1.0)
    mock_notif.assert_called_once()
    mock_pbcopy.assert_called_once()
    # Verify the clipboard payload structure
    clipboard_arg = mock_pbcopy.call_args.args[0]
    assert "BUY BTCUSD" in clipboard_arg
    assert "#42" in clipboard_arg
    assert "rails:" in clipboard_arg


# ── formatting ─────────────────────────────────────────────────────────────

def test_format_entry_plan_long():
    sizing = compute_position_size(100, 99, 100_000, 0.01)
    plan = _format_entry_plan(
        symbol="EURUSD",
        side="long",
        entry=100.0,
        sl=99.0,
        tp=101.5,
        setup="willy_long_a",
        sizing=sizing,
        row_id=7,
    )
    assert "#7 BUY EURUSD @ 100" in plan
    assert "R:R = 1.50" in plan
    assert "rails:" in plan
    assert "no-BE" in plan
    assert "no-IQ-4of4" in plan
    assert "no-Wolf" in plan
    assert "no-4+conf" in plan


def test_format_entry_plan_short():
    sizing = compute_position_size(100, 101, 100_000, 0.01)
    plan = _format_entry_plan(
        symbol="GOLD",
        side="short",
        entry=100.0,
        sl=101.0,
        tp=98.5,
        setup="willy_short_a",
        sizing=sizing,
    )
    assert "SELL GOLD @ 100" in plan
    assert "#" not in plan.split("\n")[0]  # no row_id prefix when None


def test_format_exit_summary():
    line = _format_exit_summary(
        symbol="BTC", exit_reason="tp1_hit", pnl_pct=2.5, row_id=12
    )
    assert "#12 BTC EXIT [tp1_hit] pnl +2.50%" == line


def test_format_exit_summary_no_pnl():
    line = _format_exit_summary(
        symbol="BTC", exit_reason="manual", pnl_pct=None
    )
    assert "BTC EXIT [manual] pnl n/a" == line


# ── notify_exit smoke ──────────────────────────────────────────────────────

def test_notify_exit_fires_osascript():
    with patch("propfirm_engine.notify_signal._fire_osascript_notification") as mock_notif:
        notify_exit(symbol="BTCUSD", exit_reason="sl", pnl_pct=-0.95, row_id=5)
    mock_notif.assert_called_once()
    args = mock_notif.call_args.args
    assert "EXIT BTCUSD" in args[0]
    assert "sl" in args[1]


# ── env override paths ────────────────────────────────────────────────────

def test_account_size_override_via_param():
    # Operator could call with custom account_size to test sizing at $50k
    s = compute_position_size(100, 99, account_size=50_000, risk_pct=0.01)
    assert s["risk_dollars"] == pytest.approx(500.0)
    assert s["units"] == pytest.approx(500.0)


def test_risk_pct_override_via_param():
    # 2% risk doubles size
    s = compute_position_size(100, 99, account_size=100_000, risk_pct=0.02)
    assert s["risk_dollars"] == pytest.approx(2000.0)
    assert s["units"] == pytest.approx(2000.0)
