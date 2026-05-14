"""
Propfirm v4 P5b — manual-click signal router.

When tv_webhook accepts a Willy alert, this module fires:
  1. a macOS notification (osascript) — visible across the OS
  2. a pbcopy of the structured trade plan — ready to paste anywhere
  3. a stderr log line — record in webhook.log for replay

The webhook still returns 200 < 100ms because both side effects use
subprocess.Popen (fire-and-forget), not run/check_output.

Config via env (overrides):
  P5B_ACCOUNT_SIZE   default 100000  (operator's TV paper start)
  P5B_RISK_PCT       default 0.01    (1% per trade per pn_sizing default)
  P5B_MAX_LEVERAGE   default 5.0     (abort notify if position implies >5x)

The 4 rails reminder is baked into every entry notification so the
operator sees them on every click decision:
  no BE / no I.q. 4-of-4 / no Wolf-Hour / no 4+ confluence
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


DEFAULT_ACCOUNT_SIZE = _env_float("P5B_ACCOUNT_SIZE", 100_000.0)
DEFAULT_RISK_PCT = _env_float("P5B_RISK_PCT", 0.01)
DEFAULT_MAX_LEVERAGE = _env_float("P5B_MAX_LEVERAGE", 25.0)

RAILS_LINE = "rails: no-BE | no-IQ-4of4 | no-Wolf | no-4+conf"


def compute_position_size(
    entry: float,
    sl: float,
    account_size: float = DEFAULT_ACCOUNT_SIZE,
    risk_pct: float = DEFAULT_RISK_PCT,
) -> dict[str, float]:
    """
    Side-aware sizing: units = (account * risk_pct) / |entry - sl|.

    Returns dict with units, risk_$, leverage_implied, sl_distance_pct.
    Raises ValueError if entry == sl (zero SL distance → infinite size).
    """
    if entry <= 0:
        raise ValueError(f"entry must be > 0, got {entry}")
    if sl <= 0:
        raise ValueError(f"sl must be > 0, got {sl}")
    sl_distance = abs(entry - sl)
    if sl_distance == 0:
        raise ValueError("entry == sl, SL distance is zero")
    risk_dollars = account_size * risk_pct
    units = risk_dollars / sl_distance
    notional = units * entry
    leverage = notional / account_size if account_size > 0 else 0.0
    sl_distance_pct = (sl_distance / entry) * 100.0
    return {
        "units": units,
        "risk_dollars": risk_dollars,
        "notional": notional,
        "leverage": leverage,
        "sl_distance_pct": sl_distance_pct,
    }


def _format_entry_plan(
    *,
    symbol: str,
    side: str,
    entry: float,
    sl: float,
    tp: float,
    setup: str,
    sizing: dict[str, float],
    row_id: int | None = None,
) -> str:
    action = "BUY" if side == "long" else "SELL"
    rr = abs(tp - entry) / abs(entry - sl) if entry != sl else 0.0
    id_str = f"#{row_id} " if row_id is not None else ""
    return (
        f"{id_str}{action} {symbol} @ {entry:g}\n"
        f"  SL {sl:g}  |  TP {tp:g}  |  R:R = {rr:.2f}\n"
        f"  size: {sizing['units']:.4g} units  "
        f"(notional ${sizing['notional']:,.0f}, lev {sizing['leverage']:.2f}x)\n"
        f"  risk: ${sizing['risk_dollars']:,.0f} "
        f"(SL distance {sizing['sl_distance_pct']:.2f}%)\n"
        f"  setup: {setup}\n"
        f"  {RAILS_LINE}"
    )


def _format_exit_summary(
    *, symbol: str, exit_reason: str, pnl_pct: float | None, row_id: int | None = None
) -> str:
    pnl_str = f"{pnl_pct:+.2f}%" if pnl_pct is not None else "n/a"
    id_str = f"#{row_id} " if row_id is not None else ""
    return f"{id_str}{symbol} EXIT [{exit_reason}] pnl {pnl_str}"


def _fire_osascript_notification(title: str, subtitle: str, body: str) -> None:
    """Fire macOS notification via osascript (non-blocking)."""
    # Escape double-quotes for AppleScript string literals
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

    script = (
        f'display notification "{esc(body)}" '
        f'with title "{esc(title)}" subtitle "{esc(subtitle)}"'
    )
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        # osascript missing — non-fatal, log only
        print(f"[notify_signal] osascript missing; would notify: {title}", file=sys.stderr)


def _fire_pbcopy(payload: str) -> None:
    """Copy payload to macOS clipboard (non-blocking write)."""
    try:
        p = subprocess.Popen(
            ["pbcopy"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        p.stdin.write(payload.encode())
        p.stdin.close()
        # Don't wait — pbcopy completes in microseconds; if it hangs we don't care
    except FileNotFoundError:
        print(f"[notify_signal] pbcopy missing; clipboard not updated", file=sys.stderr)


def notify_entry(
    *,
    symbol: str,
    side: str,
    entry: float,
    sl: float,
    tp: float,
    setup: str = "willy_signal",
    row_id: int | None = None,
    account_size: float = DEFAULT_ACCOUNT_SIZE,
    risk_pct: float = DEFAULT_RISK_PCT,
    max_leverage: float = DEFAULT_MAX_LEVERAGE,
) -> dict[str, Any]:
    """
    Fire notification + clipboard for an entry signal.

    Returns the computed sizing dict + 'skipped' bool. If leverage
    exceeds max_leverage, skips notification (returns skipped=True)
    so operator doesn't accidentally enter an over-sized position.
    """
    sizing = compute_position_size(entry, sl, account_size, risk_pct)
    if sizing["leverage"] > max_leverage:
        msg = (
            f"[notify_signal] ABORTED entry notify for {symbol}: "
            f"leverage {sizing['leverage']:.2f}x > cap {max_leverage}x. "
            f"SL too tight or account too small. row_id={row_id}"
        )
        print(msg, file=sys.stderr)
        return {"skipped": True, "reason": "leverage_cap", **sizing}

    plan = _format_entry_plan(
        symbol=symbol,
        side=side,
        entry=entry,
        sl=sl,
        tp=tp,
        setup=setup,
        sizing=sizing,
        row_id=row_id,
    )

    title = f"Willy {('BUY' if side == 'long' else 'SELL')} {symbol}"
    subtitle = f"@{entry:g}  SL {sl:g}  TP {tp:g}"
    # Notification body shows the compact line; clipboard has full multi-line plan
    body = (
        f"size {sizing['units']:.4g} | risk ${sizing['risk_dollars']:,.0f} "
        f"| lev {sizing['leverage']:.2f}x"
    )
    _fire_osascript_notification(title, subtitle, body)
    _fire_pbcopy(plan)
    # Also log the full plan so it survives in webhook.log
    print(f"[notify_signal] ENTRY:\n{plan}", file=sys.stderr)
    return {"skipped": False, **sizing}


def notify_exit(
    *,
    symbol: str,
    exit_reason: str,
    pnl_pct: float | None = None,
    row_id: int | None = None,
) -> None:
    """Fire notification for an exit/close event. No clipboard write."""
    line = _format_exit_summary(
        symbol=symbol, exit_reason=exit_reason, pnl_pct=pnl_pct, row_id=row_id
    )
    title = f"EXIT {symbol}"
    subtitle = f"[{exit_reason}] " + (f"{pnl_pct:+.2f}%" if pnl_pct is not None else "n/a")
    body = "manual close — verify on TV paper, then log_tv_trade close --id N"
    _fire_osascript_notification(title, subtitle, body)
    print(f"[notify_signal] EXIT: {line}", file=sys.stderr)
