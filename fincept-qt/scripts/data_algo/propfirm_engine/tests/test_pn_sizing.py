"""
Tests for pn_sizing.PNSizer — P5 P.N.-style sizing module.

Locks in the spec §4.3 semantics:
- 2 consecutive losses → skip the NEXT UTC day exactly once
- Winner / flat resets the streak counter (but not a committed skip)
- Subsequent losses inside the same streak do NOT extend the skip
- Daily trade target (3) is informational, never a hard upper cap
- Time inputs interchangeable: ISO string / epoch seconds / epoch ms /
  datetime / date
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from propfirm_engine.pn_sizing import PNSizer, _normalize_to_utc_date


# ---------------------------------------------------------------------------
# _normalize_to_utc_date — time input coercion
# ---------------------------------------------------------------------------

def test_normalize_accepts_iso_string_with_z():
    assert _normalize_to_utc_date("2026-05-13T22:30:00Z") == date(2026, 5, 13)


def test_normalize_accepts_iso_string_with_offset():
    # 23:30 UTC-5 = 04:30 next-day UTC
    assert _normalize_to_utc_date("2026-05-13T23:30:00-05:00") == date(2026, 5, 14)


def test_normalize_accepts_epoch_seconds():
    # 2026-05-13 00:00:00 UTC
    epoch = int(datetime(2026, 5, 13, tzinfo=timezone.utc).timestamp())
    assert _normalize_to_utc_date(epoch) == date(2026, 5, 13)


def test_normalize_accepts_epoch_millis():
    epoch_ms = int(datetime(2026, 5, 13, tzinfo=timezone.utc).timestamp() * 1000)
    assert _normalize_to_utc_date(epoch_ms) == date(2026, 5, 13)


def test_normalize_accepts_naive_datetime_as_utc():
    assert _normalize_to_utc_date(datetime(2026, 5, 13, 12, 0)) == date(2026, 5, 13)


def test_normalize_accepts_date_unchanged():
    d = date(2026, 5, 13)
    assert _normalize_to_utc_date(d) is d


def test_normalize_rejects_unsupported_type():
    with pytest.raises(TypeError):
        _normalize_to_utc_date(["not", "a", "time"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Defaults match spec §4.3
# ---------------------------------------------------------------------------

def test_default_construction_matches_spec():
    s = PNSizer()
    assert s.consecutive_loss_block == 2
    assert s.daily_trade_target == 3
    assert s.risk_per_trade == 0.01
    assert s.current_loss_streak() == 0


def test_can_trade_now_true_on_empty_state():
    s = PNSizer()
    assert s.can_trade_now("2026-05-13T10:00:00Z") is True
    assert s.is_blocked_today("2026-05-13T10:00:00Z") is False


# ---------------------------------------------------------------------------
# Loss streak + skip-day semantics
# ---------------------------------------------------------------------------

def test_single_loss_does_not_block():
    s = PNSizer()
    s.register_trade(-1.5, "2026-05-13T10:00:00Z")
    assert s.current_loss_streak() == 1
    assert s.can_trade_now("2026-05-13T11:00:00Z") is True
    assert s.can_trade_now("2026-05-14T11:00:00Z") is True


def test_two_consecutive_losses_block_next_day():
    s = PNSizer()
    s.register_trade(-1.5, "2026-05-13T10:00:00Z")
    s.register_trade(-1.5, "2026-05-13T14:00:00Z")
    assert s.current_loss_streak() == 2
    # Same day as 2nd loss — engine may still be processing positions.
    assert s.can_trade_now("2026-05-13T15:00:00Z") is True
    # Next UTC day — block fires.
    assert s.can_trade_now("2026-05-14T08:00:00Z") is False
    # Day after the skip — block clears.
    assert s.can_trade_now("2026-05-15T08:00:00Z") is True


def test_third_consecutive_loss_does_not_extend_skip():
    """Spec read: 'skip next day after 2 consecutive losses' — singular.
    A 3rd loss inside the same streak doesn't double the skip."""
    s = PNSizer()
    s.register_trade(-1.5, "2026-05-13T10:00:00Z")
    s.register_trade(-1.5, "2026-05-13T14:00:00Z")
    s.register_trade(-1.5, "2026-05-14T01:00:00Z")  # while blocked but registerable
    # Skip day remains 2026-05-14, NOT extended to 2026-05-15.
    assert s.can_trade_now("2026-05-14T12:00:00Z") is False
    assert s.can_trade_now("2026-05-15T08:00:00Z") is True


def test_winner_breaks_streak_but_does_not_clear_committed_skip():
    """A winner inside the skip day resets the streak counter so future
    losses start counting fresh, but the already-committed skip day
    remains skipped."""
    s = PNSizer()
    s.register_trade(-1.5, "2026-05-13T10:00:00Z")
    s.register_trade(-1.5, "2026-05-13T14:00:00Z")
    # Committed skip = 2026-05-14
    s.register_trade(+3.0, "2026-05-14T09:00:00Z")  # winner during skip
    assert s.current_loss_streak() == 0
    # Skip day is still blocked — commitment honored.
    assert s.can_trade_now("2026-05-14T10:00:00Z") is False
    # Following day clears.
    assert s.can_trade_now("2026-05-15T10:00:00Z") is True


def test_winner_before_block_resets_streak():
    s = PNSizer()
    s.register_trade(-1.5, "2026-05-13T10:00:00Z")
    s.register_trade(+3.0, "2026-05-13T11:00:00Z")
    s.register_trade(-1.5, "2026-05-13T14:00:00Z")
    # Streak after: 1 loss only — block NOT triggered
    assert s.current_loss_streak() == 1
    assert s.can_trade_now("2026-05-14T08:00:00Z") is True


def test_flat_pnl_resets_streak_like_winner():
    s = PNSizer()
    s.register_trade(-1.5, "2026-05-13T10:00:00Z")
    s.register_trade(0.0, "2026-05-13T11:00:00Z")  # flat treated as non-loss
    s.register_trade(-1.5, "2026-05-13T14:00:00Z")
    assert s.current_loss_streak() == 1
    assert s.can_trade_now("2026-05-14T08:00:00Z") is True


def test_two_loss_pairs_separated_by_winner_recommits_skip():
    s = PNSizer()
    # First pair → skip 2026-05-14
    s.register_trade(-1.5, "2026-05-12T10:00:00Z")
    s.register_trade(-1.5, "2026-05-13T10:00:00Z")
    assert s.can_trade_now("2026-05-14T10:00:00Z") is False
    # Pass the skip
    assert s.can_trade_now("2026-05-15T10:00:00Z") is True
    # Winner clears streak
    s.register_trade(+3.0, "2026-05-15T11:00:00Z")
    # New loss pair → re-commits skip to 2026-05-17
    s.register_trade(-1.5, "2026-05-16T10:00:00Z")
    s.register_trade(-1.5, "2026-05-16T15:00:00Z")
    assert s.can_trade_now("2026-05-17T10:00:00Z") is False
    assert s.can_trade_now("2026-05-18T10:00:00Z") is True


# ---------------------------------------------------------------------------
# Daily trade count — informational
# ---------------------------------------------------------------------------

def test_trades_today_counts_register_calls():
    s = PNSizer()
    s.register_trade(+1.0, "2026-05-13T09:00:00Z")
    s.register_trade(-0.5, "2026-05-13T10:00:00Z")
    s.register_trade(+2.0, "2026-05-13T11:00:00Z")
    s.register_trade(+1.0, "2026-05-14T09:00:00Z")
    assert s.trades_today("2026-05-13T20:00:00Z") == 3
    assert s.trades_today("2026-05-14T20:00:00Z") == 1


def test_daily_target_met_with_three_trades():
    s = PNSizer()
    s.register_trade(+1.0, "2026-05-13T09:00:00Z")
    s.register_trade(+1.0, "2026-05-13T10:00:00Z")
    assert s.daily_trade_count_meets_target("2026-05-13T20:00:00Z") is False
    s.register_trade(+1.0, "2026-05-13T11:00:00Z")
    assert s.daily_trade_count_meets_target("2026-05-13T20:00:00Z") is True


def test_daily_target_is_NOT_a_hard_upper_cap():
    """Spec says target ≥3/day — but exceeding 3 must NOT block.
    can_trade_now stays True regardless of count."""
    s = PNSizer()
    for _ in range(10):
        s.register_trade(+0.5, "2026-05-13T09:00:00Z")
    # Plenty over target but no streak block → still tradeable
    assert s.can_trade_now("2026-05-13T20:00:00Z") is True


# ---------------------------------------------------------------------------
# Custom block thresholds — for future regime-aware sizing
# ---------------------------------------------------------------------------

def test_custom_loss_block_threshold():
    s = PNSizer(consecutive_loss_block=3)
    s.register_trade(-1.5, "2026-05-13T10:00:00Z")
    s.register_trade(-1.5, "2026-05-13T11:00:00Z")
    # 2 losses with block=3 → no skip
    assert s.can_trade_now("2026-05-14T10:00:00Z") is True
    s.register_trade(-1.5, "2026-05-13T12:00:00Z")
    # 3 losses → skip
    assert s.can_trade_now("2026-05-14T10:00:00Z") is False
