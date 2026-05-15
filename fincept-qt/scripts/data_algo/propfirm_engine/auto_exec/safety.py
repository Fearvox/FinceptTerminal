"""
Safety rails for propfirm-v4 p5b auto-exec dispatcher.

Provides four operational safety primitives that the dispatcher calls before
any TV click is fired:

  RateLimiter   — per-source sliding-bucket cap (fixed UTC-hour bucket)
  PositionLock  — one open position per (symbol, source), checked via journal
  LeverageCheck — hard cap on incoming leverage field
  Sizer         — compute integer qty from target notional / entry price

  SafetyRails   — composer: run checks in order, expose unified API to dispatcher

Thread safety: RateLimiter uses a threading.Lock because the webhook is
multi-threaded (HTTPServer spawns one thread per request). PositionLock and
LeverageCheck are stateless — each call opens/closes its own DB connection
(short-lived), so no lock is needed there.

Reason strings are machine-parseable, max ~80 chars, kebab/underscore style.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("propfirm.auto_exec.safety")

# ---------------------------------------------------------------------------
# Default per-source hourly cap — callers can override via constructor.
# fincept is the precision scorer so it naturally fires less; willy/smc
# are noisier so give them more budget but still cap to prevent runaway.
# ---------------------------------------------------------------------------
DEFAULT_HOURLY_CAP: dict[str, int] = {
    "fincept": 10,
    "willy": 30,
    "smc": 30,
    "_default": 5,
}

# Path to the SQLite journal — mirrors trade_journal.DEFAULT_DB_PATH which
# resolves to <propfirm_engine dir>/trade_journal.sqlite.
import os as _os
_ENGINE_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
DEFAULT_DB_PATH: str = _os.path.join(_ENGINE_DIR, "trade_journal.sqlite")


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Per-source hourly dispatch cap using a fixed UTC-hour bucket.

    The bucket resets automatically when the UTC hour rolls over.  Thread-safe.

    Args:
        per_source_hourly_cap: Mapping of source name to max dispatches per UTC
            hour.  Use ``"_default"`` as a catch-all for unlisted sources.
            Example: ``{"fincept": 10, "willy": 30, "_default": 5}``.
    """

    def __init__(self, per_source_hourly_cap: dict[str, int] | None = None) -> None:
        self._caps: dict[str, int] = per_source_hourly_cap or dict(DEFAULT_HOURLY_CAP)
        # State: {source: (utc_hour_key, count)}
        # utc_hour_key is an int: year*1000000 + month*10000 + day*100 + hour
        self._state: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _hour_key() -> int:
        """Current UTC hour as a sortable integer (YYYYMMDDhh)."""
        now = datetime.now(timezone.utc)
        return now.year * 1_000_000 + now.month * 10_000 + now.day * 100 + now.hour

    def _cap_for(self, source: str) -> int:
        return self._caps.get(source, self._caps.get("_default", 5))

    def check(self, source: str) -> tuple[bool, str]:
        """Return ``(allowed, reason)``.

        Reason examples::

            "rate:fincept_9/10_this_hour"   # still within cap
            "rate:fincept_10/10_full"        # cap reached

        Args:
            source: The ``source`` field from the entry payload.

        Returns:
            Tuple of (allowed, reason_string).
        """
        cap = self._cap_for(source)
        current_hour = self._hour_key()

        with self._lock:
            prev_hour, count = self._state.get(source, (current_hour, 0))
            if prev_hour != current_hour:
                # New hour — reset bucket
                count = 0
                self._state[source] = (current_hour, count)

            if count >= cap:
                reason = f"rate:{source}_{count}/{cap}_full"
                logger.warning("rate_limit_hit source=%s count=%d cap=%d", source, count, cap)
                return False, reason

            # Pre-check only — do NOT increment here. record() bumps after success.
            reason = f"rate:{source}_{count}/{cap}_this_hour"
            return True, reason

    def record(self, source: str) -> None:
        """Increment the counter for ``source`` in the current UTC-hour bucket.

        Must be called by the dispatcher AFTER a successful click, not before.

        Args:
            source: The ``source`` field from the entry payload.
        """
        current_hour = self._hour_key()
        with self._lock:
            prev_hour, count = self._state.get(source, (current_hour, 0))
            if prev_hour != current_hour:
                count = 0
            self._state[source] = (current_hour, count + 1)
            logger.debug("rate_recorded source=%s count=%d", source, count + 1)


# ---------------------------------------------------------------------------
# PositionLock
# ---------------------------------------------------------------------------

class PositionLock:
    """One open position per (symbol, source) pair, backed by the trade journal.

    "Open" means ``exit_ts IS NULL`` and ``setup_reason LIKE '<source>_%'``.
    A fresh SQLite connection is opened per check to avoid "database is locked"
    on the long-lived webhook process.

    Args:
        db_path: Absolute path to ``trade_journal.sqlite``.  Defaults to
            ``DEFAULT_DB_PATH`` (``<propfirm_engine>/trade_journal.sqlite``).
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path

    def check(self, symbol: str, source: str, exclude_id: int | None = None) -> tuple[bool, str]:
        """Return ``(allowed, reason)``.

        ``allowed`` is ``False`` if there is already an open trade for this
        exact symbol+source combination.

        Symbol matching is case-insensitive exact match (``LOWER(symbol) = ?``).
        Exchange-prefixed tickers (e.g. ``COINBASE:SOLUSDC.P``) are treated as
        distinct from unprefixed (``SOLUSDC.P``) because each Pine source labels
        its own tickers.

        ``exclude_id`` lets a caller skip a specific journal row — typically
        the row the webhook just inserted for the alert it is dispatching for
        (otherwise the freshly-written row would block its own dispatch).

        Reason examples::

            "lock:fincept_already_open_on_SOLUSDC.P_trade_id_822"
            "lock:fincept_clear_on_SOLUSDC.P"

        Args:
            symbol: The raw ticker string from the payload (case will be
                normalised for the query but preserved in the reason string).
            source: The ``source`` field from the entry payload.
            exclude_id: Optional journal id to exclude from the open-trades
                query — used to ignore the just-written row for the current
                alert.

        Returns:
            Tuple of (allowed, reason_string).
        """
        sql = """
            SELECT id FROM trades
            WHERE LOWER(symbol) = LOWER(?)
              AND exit_ts IS NULL
              AND setup_reason LIKE ?
              AND (? IS NULL OR id != ?)
            ORDER BY id DESC
            LIMIT 1
        """
        pattern = f"{source}_%"
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    sql, (symbol, pattern, exclude_id, exclude_id)
                ).fetchone()
        except sqlite3.OperationalError as exc:
            # Table may not exist yet on a fresh install — treat as clear.
            logger.warning("position_lock_db_error symbol=%s source=%s err=%s", symbol, source, exc)
            return True, f"lock:{source}_db_error_treating_as_clear"

        if row is not None:
            trade_id = row[0]
            reason = f"lock:{source}_already_open_on_{symbol}_trade_id_{trade_id}"
            # Truncate to 80 chars if symbol is very long
            if len(reason) > 80:
                reason = reason[:77] + "..."
            logger.info("position_lock_blocked source=%s symbol=%s trade_id=%d", source, symbol, trade_id)
            return False, reason

        return True, f"lock:{source}_clear_on_{symbol}"


# ---------------------------------------------------------------------------
# LeverageCheck
# ---------------------------------------------------------------------------

class LeverageCheck:
    """Reject payloads whose ``leverage`` field exceeds the configured cap.

    If the payload omits ``leverage``, a default of 10 is assumed (matches the
    documented payload schema default).

    Args:
        max_leverage: Hard cap.  Any payload with ``leverage > max_leverage``
            is rejected.  Defaults to 25.
    """

    _DEFAULT_LEVERAGE: int = 10

    def __init__(self, max_leverage: int = 25) -> None:
        self._max = max_leverage

    def check(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """Return ``(allowed, reason)``.

        Reason examples::

            "leverage:30x_over_25x_cap"   # rejected
            "leverage:10x_ok"              # allowed

        Args:
            payload: The full entry alert dict.

        Returns:
            Tuple of (allowed, reason_string).
        """
        raw = payload.get("leverage", self._DEFAULT_LEVERAGE)
        try:
            lev = int(raw)
        except (TypeError, ValueError):
            logger.warning("leverage_parse_error raw=%r defaulting_to_%d", raw, self._DEFAULT_LEVERAGE)
            lev = self._DEFAULT_LEVERAGE

        if lev > self._max:
            reason = f"leverage:{lev}x_over_{self._max}x_cap"
            logger.warning("leverage_check_fail lev=%d max=%d", lev, self._max)
            return False, reason

        return True, f"leverage:{lev}x_ok"


# ---------------------------------------------------------------------------
# Sizer
# ---------------------------------------------------------------------------

class Sizer:
    """Compute integer position qty from target notional and entry price.

    Formula::

        qty = max(min_qty, floor(target_notional_usd / price))

    For paper trading this yields consistent R-multiples with small fills.
    ``price`` is taken from ``payload["price"]``.  If missing or non-numeric,
    ``min_qty`` is returned with a warning.

    Args:
        target_notional_usd: Target position size in USD.  Defaults to 100.0.
        min_qty: Floor quantity.  Defaults to 1.
    """

    def __init__(
        self,
        target_notional_usd: float = 100.0,
        min_qty: int = 1,
    ) -> None:
        self._notional = target_notional_usd
        self._min_qty = min_qty

    def compute_qty(self, payload: dict[str, Any]) -> int:
        """Return the integer quantity for this payload.

        Args:
            payload: The full entry alert dict containing at least ``price``.

        Returns:
            Integer quantity, always >= ``min_qty``.
        """
        try:
            price = float(payload["price"])
            if price <= 0:
                raise ValueError(f"non-positive price: {price}")
            qty = max(self._min_qty, math.floor(self._notional / price))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("sizer_fallback reason=%s returning_min_qty=%d", exc, self._min_qty)
            qty = self._min_qty

        logger.debug(
            "sizer source=%s ticker=%s price=%s notional=%.2f qty=%d",
            payload.get("source"),
            payload.get("ticker"),
            payload.get("price"),
            self._notional,
            qty,
        )
        return qty


# ---------------------------------------------------------------------------
# SafetyRails — composer
# ---------------------------------------------------------------------------

class SafetyRails:
    """Compose all safety checks into a single ``can_dispatch`` gate.

    Checks run in this order: leverage -> rate -> position_lock.
    Returns on first failure — short-circuit semantics.

    Args:
        rate_limiter: Configured ``RateLimiter`` instance.
        position_lock: Configured ``PositionLock`` instance.
        leverage_check: Configured ``LeverageCheck`` instance.
        sizer: Configured ``Sizer`` instance.
    """

    def __init__(
        self,
        rate_limiter: RateLimiter,
        position_lock: PositionLock,
        leverage_check: LeverageCheck,
        sizer: Sizer,
    ) -> None:
        self._rate = rate_limiter
        self._lock = position_lock
        self._leverage = leverage_check
        self._sizer = sizer

    def can_dispatch(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """Gate a dispatch attempt through all safety checks.

        Checks run in order: leverage -> rate -> position_lock.
        Returns ``(False, reason)`` at the first failing check so the reason
        string identifies exactly which rail blocked the trade.

        Args:
            payload: The full entry alert dict.

        Returns:
            ``(True, last_check_reason)`` if all checks pass, otherwise
            ``(False, failing_check_reason)``.
        """
        source = str(payload.get("source") or "_default").lower()
        symbol = str(payload.get("ticker") or "")

        # 1. Leverage
        ok, reason = self._leverage.check(payload)
        if not ok:
            logger.info("can_dispatch=False gate=leverage source=%s symbol=%s reason=%s",
                        source, symbol, reason)
            return False, reason

        # 2. Rate
        ok, reason = self._rate.check(source)
        if not ok:
            logger.info("can_dispatch=False gate=rate source=%s symbol=%s reason=%s",
                        source, symbol, reason)
            return False, reason

        # 3. Position lock — pass self_row_id so the just-written journal row
        # for the current alert doesn't block its own dispatch.
        self_row_id = payload.get("self_row_id")
        if isinstance(self_row_id, int):
            exclude_id: int | None = self_row_id
        else:
            exclude_id = None
        ok, reason = self._lock.check(symbol, source, exclude_id=exclude_id)
        if not ok:
            logger.info("can_dispatch=False gate=position_lock source=%s symbol=%s reason=%s",
                        source, symbol, reason)
            return False, reason

        logger.debug("can_dispatch=True source=%s symbol=%s", source, symbol)
        return True, reason

    def record_dispatch(self, payload: dict[str, Any]) -> None:
        """Notify the rate limiter that a dispatch succeeded.

        Must be called by the dispatcher AFTER a successful TV click, not
        before — this ensures the counter only counts completed dispatches.

        Args:
            payload: The same entry alert dict passed to ``can_dispatch``.
        """
        source = str(payload.get("source") or "_default").lower()
        self._rate.record(source)

    def compute_qty(self, payload: dict[str, Any]) -> int:
        """Delegate qty computation to the configured ``Sizer``.

        Args:
            payload: The full entry alert dict.

        Returns:
            Integer quantity, always >= 1.
        """
        return self._sizer.compute_qty(payload)


# ---------------------------------------------------------------------------
# Convenience factory — builds a SafetyRails with all defaults wired up.
# Callers that need custom caps or a non-default DB path should instantiate
# each class directly.
# ---------------------------------------------------------------------------

def build_default_safety_rails(
    db_path: str = DEFAULT_DB_PATH,
    max_leverage: int = 25,
    target_notional_usd: float = 100.0,
) -> SafetyRails:
    """Return a ``SafetyRails`` instance with defaults suitable for p5b live week.

    Args:
        db_path: Path to ``trade_journal.sqlite``.
        max_leverage: Hard leverage cap.
        target_notional_usd: Target notional per trade in USD.

    Returns:
        Fully configured ``SafetyRails``.
    """
    return SafetyRails(
        rate_limiter=RateLimiter(per_source_hourly_cap=dict(DEFAULT_HOURLY_CAP)),
        position_lock=PositionLock(db_path=db_path),
        leverage_check=LeverageCheck(max_leverage=max_leverage),
        sizer=Sizer(target_notional_usd=target_notional_usd),
    )
