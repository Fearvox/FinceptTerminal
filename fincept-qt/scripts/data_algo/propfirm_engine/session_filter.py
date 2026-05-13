"""
Propfirm v4 P1 — session filter.

Two predicates decide whether a given UTC bar is tradeable:

    is_trading_hour(ts_utc, asset_class)  # session mask
    is_near_news(ts_utc, calendar_path)   # news window

Both return True when OK to trade. The engine (engine.py) combines them
with a logical AND before letting a signal through.

UTC session masks per spec §4.3 (propfirm research):
  crypto : 24/7 minus Wolf Hour 02:30-04:00 UTC (MM-only window)
  fx     : 12:00-16:00 UTC (London-NY overlap)
  gold   : 12:00-16:00 UTC (same as FX — physical gold follows LDN/NY)
  equity : 13:30-15:00 UTC AND 19:30-20:00 UTC
             (NY open 09:30-11:00 ET + NY close 15:30-16:00 ET)
  oil    : 13:30-19:00 UTC (NYMEX pit + electronic convergence)

Asset → class routing is an explicit table. Pass either the class name
directly or a symbol — the router uses suffix rules.

News windows are read from news_calendar.yaml (package-local default).
Bars *outside* the listed coverage default to *allowed*, so a partially-
populated calendar does not over-block. See file header of news_calendar
for the expansion TODO.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal, Optional

AssetClass = Literal["crypto", "fx", "gold", "equity", "oil"]

_DEFAULT_CALENDAR = os.path.join(os.path.dirname(__file__), "news_calendar.yaml")

# Inclusive-start, exclusive-end UTC hour+minute ranges expressed as
# (start_h, start_m, end_h, end_m). Multiple ranges per class → union.
_SESSION_RANGES: dict[AssetClass, tuple[tuple[int, int, int, int], ...]] = {
    "crypto": (
        (0, 0, 2, 30),    # 00:00-02:30 UTC
        (4, 0, 24, 0),    # 04:00-24:00 UTC  (Wolf Hour 02:30-04:00 excluded)
    ),
    "fx":     ((12, 0, 16, 0),),
    "gold":   ((12, 0, 16, 0),),
    "equity": ((13, 30, 15, 0), (19, 30, 20, 0)),
    "oil":    ((13, 30, 19, 0),),
}


def classify_symbol(symbol: str) -> AssetClass:
    """Map a symbol string to its asset class by suffix/prefix rules.

    Crypto     : BTCUSDT, ETHUSDT, ...  (Binance perpetual style)
    FX         : EURUSD=X, GBPUSD=X     (Yahoo =X suffix)
    Gold/Oil   : GC=F, CL=F             (Yahoo =F suffix — disambiguated by ticker)
    Equity     : SPY, QQQ, AAPL, ...    (everything else)
    """
    s = symbol.upper()
    if s.endswith("=X"):
        return "fx"
    if s.endswith("=F"):
        if s.startswith("GC"):
            return "gold"
        if s.startswith("CL") or s.startswith("BZ"):
            return "oil"
        # default commodity → equity hours (fallback, not perfect)
        return "equity"
    if s.endswith("USDT") or s.endswith("USDC") or s.endswith("BTC"):
        return "crypto"
    return "equity"


def _to_utc_datetime(ts) -> datetime:
    """Accept epoch seconds, epoch millis, ISO-8601 string, or datetime."""
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        # epoch seconds vs millis heuristic
        if ts > 10_000_000_000:  # >= year 2286 in seconds → must be millis
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    raise TypeError(f"unsupported ts type: {type(ts).__name__}")


def _minutes_since_midnight(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def is_trading_hour(ts, asset: str) -> bool:
    """Return True iff the given UTC instant is inside an allowed session
    window for the asset. `asset` can be an AssetClass literal or a raw
    symbol (classified automatically)."""
    dt = _to_utc_datetime(ts)
    cls: AssetClass = asset if asset in _SESSION_RANGES else classify_symbol(asset)  # type: ignore[assignment]
    minutes = _minutes_since_midnight(dt)
    for (sh, sm, eh, em) in _SESSION_RANGES[cls]:
        start = sh * 60 + sm
        end = eh * 60 + em
        if start <= minutes < end:
            return True
    return False


@lru_cache(maxsize=4)
def _load_calendar(path: str) -> tuple[tuple[datetime, int], ...]:
    """Parse news_calendar.yaml into ((utc_dt, skip_window_min), ...).

    Pure-stdlib parser — avoids adding PyYAML as a dependency. Only
    handles the narrow format documented in news_calendar.yaml.
    """
    events: list[tuple[datetime, int]] = []
    current_ts: Optional[str] = None
    current_window: Optional[int] = None
    with open(path, "r") as f:
        in_events = False
        for raw in f:
            line = raw.rstrip()
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.startswith("events:"):
                in_events = True
                continue
            if not in_events:
                continue
            stripped = line.strip()
            if stripped.startswith("- ts:"):
                # new event — flush any pending
                if current_ts is not None and current_window is not None:
                    events.append((_to_utc_datetime(current_ts), current_window))
                current_ts = stripped.split("- ts:", 1)[1].strip().strip('"').strip("'")
                current_window = None
            elif stripped.startswith("ts:"):
                current_ts = stripped.split("ts:", 1)[1].strip().strip('"').strip("'")
            elif stripped.startswith("skip_window_min:"):
                current_window = int(stripped.split("skip_window_min:", 1)[1].strip())
        if current_ts is not None and current_window is not None:
            events.append((_to_utc_datetime(current_ts), current_window))
    return tuple(events)


def is_near_news(ts, calendar_path: str = _DEFAULT_CALENDAR,
                 window_min: Optional[int] = None) -> bool:
    """Return True iff the given UTC instant is within ±window_min of any
    calendar event. Per-event window from the yaml overrides the default.
    Bars outside the calendar coverage return False (allowed to trade)."""
    dt = _to_utc_datetime(ts)
    events = _load_calendar(calendar_path)
    for (ev_dt, ev_win) in events:
        win = window_min if window_min is not None else ev_win
        delta_min = abs((dt - ev_dt).total_seconds()) / 60.0
        if delta_min <= win:
            return True
    return False


def is_tradeable(ts, asset: str,
                 calendar_path: str = _DEFAULT_CALENDAR) -> bool:
    """Convenience combinator used by the engine: in-session AND not-in-news."""
    return is_trading_hour(ts, asset) and not is_near_news(ts, calendar_path)
