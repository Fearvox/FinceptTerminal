"""
Tests for propfirm_engine.mfe_tracker (P4 scaffold).

Locks in the post-exit MFE/MAE measurement semantic so iter-2/5 can
wire it into the engine without ambiguity about which way each metric
points for long vs short.
"""
from __future__ import annotations

import pytest

from propfirm_engine import mfe_tracker as mt


def _bar(o: float, h: float, l: float, c: float, v: float = 1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


# ---------------------------------------------------------------------------
# register() input validation
# ---------------------------------------------------------------------------

def test_register_rejects_invalid_direction():
    tr = mt.MfeTracker()
    with pytest.raises(ValueError):
        tr.register("t1", exit_idx=10, exit_price=100.0,
                    direction="up", realized_pnl_pct=0.5)  # type: ignore[arg-type]


def test_register_rejects_non_positive_price():
    tr = mt.MfeTracker()
    with pytest.raises(ValueError):
        tr.register("t1", exit_idx=10, exit_price=0.0,
                    direction="long", realized_pnl_pct=0.5)


def test_register_stores_trade():
    tr = mt.MfeTracker()
    tr.register("t1", exit_idx=10, exit_price=100.0,
                direction="long", realized_pnl_pct=1.0)
    assert len(tr.trades) == 1
    assert tr.trades[0].trade_id == "t1"
    assert tr.trades[0].completed is False


# ---------------------------------------------------------------------------
# advance() — no-op cases
# ---------------------------------------------------------------------------

def test_advance_ignores_bar_at_or_before_exit():
    """Tracker only logs STRICTLY AFTER the exit bar."""
    bars = [_bar(100, 101, 99, 100) for _ in range(20)]
    tr = mt.MfeTracker()
    tr.register("t1", exit_idx=10, exit_price=100.0,
                direction="long", realized_pnl_pct=0.0)
    tr.advance(bars, current_idx=10)  # exit bar itself
    tr.advance(bars, current_idx=9)   # before exit
    assert tr.trades[0].running_mfe == 0.0
    assert tr.trades[0].running_mae == 0.0
    assert tr.trades[0].mfe_10 is None


def test_advance_handles_out_of_range_index():
    bars = [_bar(100, 101, 99, 100) for _ in range(5)]
    tr = mt.MfeTracker()
    tr.register("t1", exit_idx=0, exit_price=100.0,
                direction="long", realized_pnl_pct=0.0)
    # Negative or past-end should be silent no-ops
    tr.advance(bars, current_idx=-1)
    tr.advance(bars, current_idx=999)
    assert tr.trades[0].mfe_10 is None


# ---------------------------------------------------------------------------
# Long trade: MFE = max upside, MAE = max downside
# ---------------------------------------------------------------------------

def test_long_mfe_captures_max_upside_at_snapshot_offsets():
    # Exit at bar 0, exit_price=100. Then 100 follow-up bars.
    # We make bar+10 spike to high=110 (10% above exit) and bar+50 to
    # high=120 (20%). Snapshots should freeze running max at those points.
    bars = [_bar(100, 100, 100, 100)]  # exit bar
    # bars 1..9: flat
    for _ in range(9):
        bars.append(_bar(100, 100, 100, 100))
    # bar 10: spike to 110
    bars.append(_bar(100, 110, 100, 105))
    # bars 11..49: flat at 105
    for _ in range(39):
        bars.append(_bar(105, 105, 105, 105))
    # bar 50: bigger spike to 120
    bars.append(_bar(105, 120, 105, 115))
    # bars 51..100: flat at 115
    for _ in range(50):
        bars.append(_bar(115, 115, 115, 115))

    tr = mt.MfeTracker()
    tr.register("L1", exit_idx=0, exit_price=100.0,
                direction="long", realized_pnl_pct=2.0)
    for i in range(1, len(bars)):
        tr.advance(bars, i)

    t = tr.trades[0]
    # +10: max so far was 110 → 10pp
    assert t.mfe_10 == pytest.approx(10.0)
    # +50: max bumped to 120 → 20pp
    assert t.mfe_50 == pytest.approx(20.0)
    # +100: nothing higher → still 20pp
    assert t.mfe_100 == pytest.approx(20.0)
    assert t.completed is True


def test_long_mae_captures_max_drawdown_at_snapshot_offsets():
    bars = [_bar(100, 100, 100, 100)]
    # bars 1..9: flat
    for _ in range(9):
        bars.append(_bar(100, 100, 100, 100))
    # bar 10: dip to low=90 → -10pp
    bars.append(_bar(100, 100, 90, 95))
    # bars 11..99: stay above 90
    for _ in range(90):
        bars.append(_bar(95, 96, 94, 95))

    tr = mt.MfeTracker()
    tr.register("L2", exit_idx=0, exit_price=100.0,
                direction="long", realized_pnl_pct=-1.0)
    for i in range(1, len(bars)):
        tr.advance(bars, i)

    t = tr.trades[0]
    assert t.mae_10 == pytest.approx(-10.0)
    # MAE is the worst, so it locks in once the dip happens
    assert t.mae_50 == pytest.approx(-10.0)
    assert t.mae_100 == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# Short trade: MFE = max downside (favorable), MAE = max upside (adverse)
# ---------------------------------------------------------------------------

def test_short_mfe_captures_max_downside():
    bars = [_bar(100, 100, 100, 100)]
    for _ in range(9):
        bars.append(_bar(100, 100, 100, 100))
    # bar 10: low dips to 88 → favorable for short by 12pp
    bars.append(_bar(100, 100, 88, 92))
    for _ in range(90):
        bars.append(_bar(92, 93, 91, 92))

    tr = mt.MfeTracker()
    tr.register("S1", exit_idx=0, exit_price=100.0,
                direction="short", realized_pnl_pct=3.0)
    for i in range(1, len(bars)):
        tr.advance(bars, i)

    t = tr.trades[0]
    assert t.mfe_10 == pytest.approx(12.0)


def test_short_mae_captures_max_upside_adverse():
    bars = [_bar(100, 100, 100, 100)]
    for _ in range(9):
        bars.append(_bar(100, 100, 100, 100))
    # bar 10: high pops to 115 → adverse for short by 15pp
    bars.append(_bar(100, 115, 100, 110))
    for _ in range(90):
        bars.append(_bar(110, 110, 110, 110))

    tr = mt.MfeTracker()
    tr.register("S2", exit_idx=0, exit_price=100.0,
                direction="short", realized_pnl_pct=-0.5)
    for i in range(1, len(bars)):
        tr.advance(bars, i)

    t = tr.trades[0]
    assert t.mae_10 == pytest.approx(-15.0)


# ---------------------------------------------------------------------------
# Incomplete trade: bars exhausted before +100 reached
# ---------------------------------------------------------------------------

def test_incomplete_when_bars_exhausted_before_100():
    # Only 30 post-exit bars available
    bars = [_bar(100, 100, 100, 100)] + [_bar(105, 106, 104, 105) for _ in range(30)]

    tr = mt.MfeTracker()
    tr.register("X1", exit_idx=0, exit_price=100.0,
                direction="long", realized_pnl_pct=0.0)
    for i in range(1, len(bars)):
        tr.advance(bars, i)

    t = tr.trades[0]
    assert t.mfe_10 is not None     # +10 snapshot reached
    assert t.mfe_50 is None         # +50 snapshot never reached
    assert t.mfe_100 is None
    assert t.completed is False


# ---------------------------------------------------------------------------
# leakage_summary()
# ---------------------------------------------------------------------------

def test_leakage_summary_empty():
    tr = mt.MfeTracker()
    s = tr.leakage_summary()
    assert s["n_completed"] == 0
    assert s["pass_gate"] is False


def test_leakage_summary_basic_long_winner_then_more_upside():
    """Trade closed at +2pp; price kept running to +5pp → 3pp leakage = 2R."""
    bars = [_bar(100, 100, 100, 100)]
    for _ in range(10):
        bars.append(_bar(100, 100, 100, 100))
    # at +11 (just past the +10 snapshot, doesn't matter for +100), price runs to 105
    for _ in range(95):
        bars.append(_bar(105, 105, 105, 105))

    tr = mt.MfeTracker()
    # trade closed with realized +2pp; subsequent MFE_100 should be +5pp
    tr.register("W1", exit_idx=0, exit_price=100.0,
                direction="long", realized_pnl_pct=2.0)
    for i in range(1, len(bars)):
        tr.advance(bars, i)

    s = tr.leakage_summary(r_unit_pct=1.5, leakage_threshold_R=0.5)
    assert s["n_completed"] == 1
    # leakage = MFE_100(5pp) - realized(2pp) = 3pp = 2R
    assert s["mean_leakage_pp"] == pytest.approx(3.0)
    assert s["mean_leakage_R"] == pytest.approx(2.0)
    # 2R >= 0.5R threshold → 1 trade above, pass_gate False
    assert s["trades_above_threshold"] == 1
    assert s["pass_gate"] is False


def test_leakage_summary_last_n_filter():
    tr = mt.MfeTracker()
    # Build 3 completed trades with leakages of 0.3R, 0.4R, 1.5R
    # Easiest: just mock the completed flag + mfe_100 directly
    for trade_id, realized, mfe100 in [("a", 0.0, 0.45), ("b", 0.0, 0.6), ("c", 0.0, 2.25)]:
        t = mt.TrackedTrade(
            trade_id=trade_id, exit_idx=0, exit_price=100.0,
            direction="long", realized_pnl_pct=realized,
            mfe_100=mfe100, completed=True,
        )
        tr.trades.append(t)

    s_all = tr.leakage_summary(r_unit_pct=1.5, leakage_threshold_R=0.5)
    # mean leakage_R = mean(0.3, 0.4, 1.5) = 0.733
    assert s_all["mean_leakage_R"] == pytest.approx(0.733, abs=0.01)
    assert s_all["pass_gate"] is False  # 0.733 >= 0.5

    s_last2 = tr.leakage_summary(last_n=2, r_unit_pct=1.5, leakage_threshold_R=0.5)
    # mean of (0.4R, 1.5R) = 0.95R, still fails
    assert s_last2["mean_leakage_R"] == pytest.approx(0.95, abs=0.01)
    assert s_last2["pass_gate"] is False
    assert s_last2["n_completed"] == 2


def test_leakage_summary_pass_when_all_under_threshold():
    tr = mt.MfeTracker()
    for trade_id, mfe100 in [("a", 0.3), ("b", 0.45), ("c", 0.15)]:
        tr.trades.append(mt.TrackedTrade(
            trade_id=trade_id, exit_idx=0, exit_price=100.0,
            direction="long", realized_pnl_pct=0.0,
            mfe_100=mfe100, completed=True,
        ))
    s = tr.leakage_summary(r_unit_pct=1.5, leakage_threshold_R=0.5)
    # mean = 0.3pp = 0.2R, below threshold
    assert s["pass_gate"] is True
    assert s["trades_above_threshold"] == 0
