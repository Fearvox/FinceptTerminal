"""
Propfirm v4 — P4 MFE counterfactual log.

Spec §4.3 Phase 4: after every closed trade, this tracker continues for
100 bars and records MFE/MAE @ +10/+50/+100 bars post-exit. Monthly
leakage is defined as `mean(MFE_100 − realized_pnl_pct)` — how much
profit we left on the table by exiting when we did.

This module is the iter-1/5 SCAFFOLD: a self-contained tracker class
with no engine.py wiring yet. The tracker class records the per-bar
post-exit excursion of each registered trade and snapshots the
cumulative MFE / MAE at three checkpoints. Iter-2/5 will integrate it
into PropfirmEngine via `cfg.mfe_log_on`. Iter-3/5 builds the backtest
runner that calls `leakage_summary()` to evaluate the gate.

Conventions:
  - All percentage values are in **percentage points** (e.g. 1.5 = 1.5%).
  - For a LONG trade: MFE = max gain above exit_price; MAE = max loss
    below exit_price (negative number).
  - For a SHORT trade: MFE = max gain BELOW exit_price (exit_price -
    low_price, positive when favorable); MAE = max loss ABOVE exit
    (exit_price - high_price, negative when adverse).
  - The "0 R" reference frame is exit_price. realized_pnl_pct is the
    pnl that the trade actually closed at (positive on a winner,
    negative on a loser).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Direction = Literal["long", "short"]


@dataclass
class TrackedTrade:
    """A closed trade being followed for 100 bars post-exit.

    `running_mfe` and `running_mae` are updated each call to
    `MfeTracker.advance()` and represent the cumulative best / worst
    excursion since the exit bar. Snapshots are stamped into the *_10,
    *_50, *_100 fields when the corresponding bar offset is reached.
    """
    trade_id: str
    exit_idx: int
    exit_price: float
    direction: Direction
    realized_pnl_pct: float

    running_mfe: float = 0.0
    running_mae: float = 0.0

    mfe_10: float | None = None
    mfe_50: float | None = None
    mfe_100: float | None = None
    mae_10: float | None = None
    mae_50: float | None = None
    mae_100: float | None = None

    completed: bool = False  # set when +100 bars reached


class MfeTracker:
    """Walks each closed trade forward through 100 post-exit bars.

    Typical engine integration (iter-2/5):
        tracker = MfeTracker()
        for i, bar in enumerate(bars):
            ... entry / exit logic ...
            if trade_just_closed:
                tracker.register(trade_id, exit_idx=i, exit_price=close,
                                  direction=side, realized_pnl_pct=pnl)
            tracker.advance(bars, current_idx=i)
        # at end:
        summary = tracker.leakage_summary()
    """

    def __init__(self) -> None:
        self.trades: list[TrackedTrade] = []

    def register(
        self,
        trade_id: str,
        exit_idx: int,
        exit_price: float,
        direction: Direction,
        realized_pnl_pct: float,
    ) -> None:
        """Add a freshly-closed trade to the tracker."""
        if direction not in ("long", "short"):
            raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")
        if exit_price <= 0:
            raise ValueError(f"exit_price must be positive, got {exit_price}")
        self.trades.append(TrackedTrade(
            trade_id=trade_id,
            exit_idx=exit_idx,
            exit_price=exit_price,
            direction=direction,
            realized_pnl_pct=realized_pnl_pct,
        ))

    def advance(self, bars: list[dict[str, Any]], current_idx: int) -> None:
        """For each not-yet-completed tracked trade, update running MFE/MAE
        against `bars[current_idx]` and snapshot at +10/+50/+100."""
        if current_idx < 0 or current_idx >= len(bars):
            return
        bar = bars[current_idx]

        for t in self.trades:
            if t.completed:
                continue
            elapsed = current_idx - t.exit_idx
            if elapsed <= 0:
                # Tracker only logs strictly AFTER the exit bar.
                continue

            # Per-bar excursion in pp (percentage points) from exit_price.
            if t.direction == "long":
                gain_pct = (bar["high"] - t.exit_price) / t.exit_price * 100
                loss_pct = (bar["low"] - t.exit_price) / t.exit_price * 100
            else:  # short
                gain_pct = (t.exit_price - bar["low"]) / t.exit_price * 100
                loss_pct = (t.exit_price - bar["high"]) / t.exit_price * 100

            # Cumulative best/worst since exit.
            if gain_pct > t.running_mfe:
                t.running_mfe = gain_pct
            if loss_pct < t.running_mae:
                t.running_mae = loss_pct

            # Snapshot at exact bar offsets.
            if elapsed == 10:
                t.mfe_10 = t.running_mfe
                t.mae_10 = t.running_mae
            if elapsed == 50:
                t.mfe_50 = t.running_mfe
                t.mae_50 = t.running_mae
            if elapsed == 100:
                t.mfe_100 = t.running_mfe
                t.mae_100 = t.running_mae
                t.completed = True

    # ------------------------------------------------------------------
    # Leakage analytics — used by the P4 backtest runner in iter-3/5.
    # ------------------------------------------------------------------

    def completed_trades(self) -> list[TrackedTrade]:
        """Trades that reached the full +100-bar follow-up."""
        return [t for t in self.trades if t.completed]

    def leakage_summary(
        self,
        last_n: int | None = None,
        r_unit_pct: float = 1.5,
        leakage_threshold_R: float = 0.5,
    ) -> dict[str, Any]:
        """Aggregate leakage statistics over completed trades.

        Args:
          last_n: if given, only the most-recent N completed trades.
              Spec §4.3 evaluates leakage over "last 30 trades".
          r_unit_pct: 1R in percentage points. Default 1.5 matches the
              engine's default fixed SL distance.
          leakage_threshold_R: pass-gate threshold in R units. Default
              0.5 per spec §4.3 ("leakage < 0.5R over last 30 trades").

        Returns a dict with mean / median / max leakage in pp and in R,
        plus counts and a `pass_gate` boolean. Returns `n_completed: 0`
        when no trades have completed (caller should treat as
        insufficient-data, NOT pass).
        """
        comp = self.completed_trades()
        if last_n is not None and last_n > 0:
            comp = comp[-last_n:]

        if not comp:
            return {
                "n_completed": 0,
                "n_pending": sum(1 for t in self.trades if not t.completed),
                "pass_gate": False,
                "reason": "no completed trades",
            }

        leakages_pp = [(t.mfe_100 or 0.0) - t.realized_pnl_pct for t in comp]
        leakages_R = [L / r_unit_pct for L in leakages_pp]

        mean_pp = sum(leakages_pp) / len(leakages_pp)
        mean_R = sum(leakages_R) / len(leakages_R)
        max_pp = max(leakages_pp)
        median_R = sorted(leakages_R)[len(leakages_R) // 2]

        pass_gate = mean_R < leakage_threshold_R

        return {
            "n_completed": len(comp),
            "n_pending": sum(1 for t in self.trades if not t.completed),
            "mean_leakage_pp": mean_pp,
            "mean_leakage_R": mean_R,
            "median_leakage_R": median_R,
            "max_leakage_pp": max_pp,
            "r_unit_pct": r_unit_pct,
            "leakage_threshold_R": leakage_threshold_R,
            "trades_above_threshold": sum(
                1 for L in leakages_R if L >= leakage_threshold_R
            ),
            "pass_gate": pass_gate,
        }
