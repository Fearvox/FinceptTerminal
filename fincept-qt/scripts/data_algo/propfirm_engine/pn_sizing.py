"""
Propfirm v4 — P5 P.N.-Style Sizing (backtest-side).

Spec §4.3 Phase 5: fixed 1% risk per trade, skip next day after 2
consecutive losses, target ≥ 3 trades per day during allowed sessions.

This module encapsulates the sizing-state machine as a standalone
component so engine.py can wire it via cfg.pn_sizing_on without
embedding the per-day / per-streak bookkeeping in the trading loop.

Public surface:
  PNSizer(consecutive_loss_block=2, daily_trade_target=3, risk_per_trade=0.01)
  PNSizer.register_trade(pnl_pct: float, ts) — call once per closed trade
  PNSizer.can_trade_now(ts) -> bool          — gate predicate for entry
  PNSizer.trades_today(ts) -> int            — informational counter
  PNSizer.daily_trade_count_meets_target(ts) -> bool  — informational
  PNSizer.current_loss_streak() -> int       — introspection
  PNSizer.is_blocked_today(ts) -> bool       — inverse of can_trade_now

Time inputs accept ISO 8601 strings, epoch seconds (int/float), epoch
milliseconds, or `datetime`/`date` objects, mirroring session_filter
conventions so engine.py can pass `bar["ts"]` straight through.

State machine semantics:
- A "trading day" is the UTC calendar day.
- A skip is COMMITTED on the day the 2nd consecutive loss closes
  (call it day N). The skip applies to day N+1 ONLY.
- Once committed, the skip is NOT cleared by an early winner — a
  winner clears the *streak counter* but the committed skip day
  still happens. The skip auto-clears the moment we pass beyond it.
- Subsequent losses inside the same streak do NOT roll the skip
  forward. To trigger another skip, the streak must first reset
  (via a winner / flat), then accumulate 2 fresh consecutive losses.
- Daily trade count is informational. The spec's "≥3 trades/day"
  target is not a hard cap — engine still trades when can_trade_now
  is True regardless of how many trades occurred today.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Union

TimeInput = Union[str, int, float, datetime, date]


def _normalize_to_utc_date(ts: TimeInput) -> date:
    """Coerce any supported time input into a UTC date object.

    Epoch milliseconds detected via the > 1e11 heuristic — anything
    bigger than that as a raw "seconds" value would be year 5138+,
    well past any realistic market data span.
    """
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).date()
    if isinstance(ts, date):  # plain date (subclass check after datetime)
        return ts
    if isinstance(ts, (int, float)):
        seconds = ts / 1000.0 if ts > 1e11 else float(ts)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
    if isinstance(ts, str):
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).astimezone(timezone.utc).date()
    raise TypeError(f"unsupported ts type: {type(ts).__name__}")


@dataclass
class PNSizer:
    """Stateful sizing controller per spec §4.3 Phase 5.

    Hold one instance per backtest run. Construct fresh for a new run.
    """
    consecutive_loss_block: int = 2
    daily_trade_target: int = 3
    risk_per_trade: float = 0.01

    # Internal state (mutated only via public methods; exposed for tests).
    _loss_streak: int = 0
    # Skip day is the UTC date on which trading is blocked. Computed as
    # (day of 2nd consecutive loss) + 1 day. None when no commitment is
    # active. Auto-clears once we've passed beyond it.
    _skip_day: date | None = None
    _daily_counts: dict[date, int] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Trade outcome ingestion
    # ------------------------------------------------------------------

    def register_trade(self, pnl_pct: float, ts: TimeInput) -> None:
        """Record a closed-trade outcome. `pnl_pct` in percentage points
        (sign matters; flat = breaks the streak)."""
        d = _normalize_to_utc_date(ts)
        self._daily_counts[d] = self._daily_counts.get(d, 0) + 1

        if pnl_pct < 0:
            self._loss_streak += 1
            # Only commit a skip on the EXACT transition into the block
            # threshold — subsequent losses inside the same streak do
            # NOT roll the skip forward.
            if (
                self._loss_streak == self.consecutive_loss_block
                and self._skip_day is None
            ):
                self._skip_day = d + timedelta(days=1)
        else:
            # Winner OR flat — reset streak counter ONLY. A committed
            # skip stays committed; it auto-clears once we pass the day.
            self._loss_streak = 0

    # ------------------------------------------------------------------
    # Entry gate
    # ------------------------------------------------------------------

    def can_trade_now(self, ts: TimeInput) -> bool:
        """True iff sizing permits an entry on the bar at `ts`."""
        d = _normalize_to_utc_date(ts)
        if self._skip_day is None:
            return True
        if d == self._skip_day:
            return False
        if d > self._skip_day:
            # Auto-clear stale commitment so future losses can recommit.
            self._skip_day = None
            return True
        # d < self._skip_day (same day as 2nd loss, or earlier) → allowed.
        return True

    # ------------------------------------------------------------------
    # Informational helpers
    # ------------------------------------------------------------------

    def trades_today(self, ts: TimeInput) -> int:
        return self._daily_counts.get(_normalize_to_utc_date(ts), 0)

    def daily_trade_count_meets_target(self, ts: TimeInput) -> bool:
        return self.trades_today(ts) >= self.daily_trade_target

    def current_loss_streak(self) -> int:
        return self._loss_streak

    def is_blocked_today(self, ts: TimeInput) -> bool:
        return not self.can_trade_now(ts)
