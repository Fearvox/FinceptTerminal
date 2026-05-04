"""
The Leap Crypto — read-only Virtual Meeting MoE / ATA room.

This module does one thing: read the local propfirm trade journal and turn
it into a deterministic multi-role accountability transcript for TradingView
The Leap Crypto. It never connects to a broker, never places orders, never
writes to the journal, and never gives entry signals.

Run from fincept-qt/scripts/data_algo:

    python3 -m propfirm_engine.leap_moe_room --date today
    python3 -m propfirm_engine.leap_moe_room --date 2026-05-04 --group-by setup
    python3 -m propfirm_engine.leap_moe_room --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .trade_journal import DEFAULT_DB_PATH

HEADER = (
    "=== READ-ONLY VIRTUAL MEETING MoE — LEAP CRYPTO MAY 2026 — "
    "NO TRADES EXECUTED, PLACED, OR SUGGESTED ==="
)

CONTEST_START = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
CONTEST_END = datetime(2026, 5, 15, 23, 59, 59, tzinfo=timezone.utc)
STARTING_BALANCE_USD = 100_000
CRYPTO_LEVERAGE = "10:1"
COMMISSION_PCT = 0.01
MIN_ACTIVITY_DAYS = 3
TX_PER_MINUTE_BLOCK = 60

OFFICIAL_SYMBOLS = {
    "COINBASE:BTCUSDC.P",
    "COINBASE:ETHUSDC.P",
    "COINBASE:SOLUSDC.P",
    "COINBASE:XRPUSDC.P",
    "COINBASE:DOGEUSDC.P",
}
MAX_OPEN_POSITIONS = {
    "COINBASE:BTCUSDC.P": 5.0,
    "COINBASE:ETHUSDC.P": 100.0,
    "COINBASE:SOLUSDC.P": 2_000.0,
    "COINBASE:XRPUSDC.P": 150_000.0,
    "COINBASE:DOGEUSDC.P": 2_000_000.0,
}
CRYPTO_ROOTS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
PLANNED_EXIT_REASONS = {"tp", "sl", "atr_trail", "regime_flip", "session_close"}
GROUP_FIELDS = {"setup", "symbol", "regime", "side", "session_hour"}


@dataclass(frozen=True)
class Trade:
    id: int
    symbol: str
    side: str
    entry_price: float
    exit_price: float | None
    sl: float | None
    tp: float | None
    entry_ts: datetime
    exit_ts: datetime | None
    exit_reason: str | None
    pnl_pct: float | None
    setup_reason: str
    regime: str
    session_hour: int | None
    mfe_10: float | None
    mfe_50: float | None
    mfe_100: float | None
    mae_10: float | None
    mae_50: float | None
    mae_100: float | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Trade":
        return cls(
            id=int(row["id"]),
            symbol=str(row["symbol"] or "").upper(),
            side=str(row["side"] or "").lower(),
            entry_price=float(row["entry_price"]),
            exit_price=_float_or_none(row["exit_price"]),
            sl=_float_or_none(row["sl"]),
            tp=_float_or_none(row["tp"]),
            entry_ts=_parse_utc(str(row["entry_ts"])),
            exit_ts=_parse_utc(str(row["exit_ts"])) if row["exit_ts"] else None,
            exit_reason=str(row["exit_reason"] or "") or None,
            pnl_pct=_float_or_none(row["pnl_pct"]),
            setup_reason=str(row["setup_reason"] or "unknown_setup"),
            regime=str(row["regime"] or "unknown_regime"),
            session_hour=int(row["session_hour"]) if row["session_hour"] is not None else None,
            mfe_10=_float_or_none(row["mfe_10"]),
            mfe_50=_float_or_none(row["mfe_50"]),
            mfe_100=_float_or_none(row["mfe_100"]),
            mae_10=_float_or_none(row["mae_10"]),
            mae_50=_float_or_none(row["mae_50"]),
            mae_100=_float_or_none(row["mae_100"]),
        )

    @property
    def is_closed(self) -> bool:
        return self.exit_ts is not None and self.pnl_pct is not None

    @property
    def risk_pct(self) -> float | None:
        if self.sl is None or self.entry_price <= 0:
            return None
        return abs(self.entry_price - self.sl) / self.entry_price * 100

    @property
    def reward_pct(self) -> float | None:
        if self.tp is None or self.entry_price <= 0:
            return None
        return abs(self.tp - self.entry_price) / self.entry_price * 100

    @property
    def rr(self) -> float | None:
        risk = self.risk_pct
        reward = self.reward_pct
        if risk is None or reward is None or risk <= 0:
            return None
        return reward / risk

    @property
    def r_multiple(self) -> float | None:
        risk = self.risk_pct
        if risk is None or risk <= 0 or self.pnl_pct is None:
            return None
        return self.pnl_pct / risk


@dataclass(frozen=True)
class GroupStat:
    key: str
    count: int
    wins: int
    win_rate: float
    total_pnl_pct: float
    avg_pnl_pct: float
    avg_r: float | None
    best_trade_id: int | None
    worst_trade_id: int | None


@dataclass(frozen=True)
class Analysis:
    day: str
    now_utc: str
    journal_path: str
    journal_exists: bool
    total_rows: int
    daily_activity_count: int
    daily_closed_count: int
    daily_open_count: int
    contest_activity_days: list[str]
    missing_activity_days: int
    discipline_score: int
    score_parts: dict[str, float]
    top_groups: list[GroupStat]
    best_trade: dict[str, Any] | None
    worst_trade: dict[str, Any] | None
    violations: list[str]
    limitations: list[str]
    verdict: str


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_utc(value: str) -> datetime:
    if not value:
        raise ValueError("empty timestamp")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def parse_day(raw: str | None, now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    if raw is None or raw == "today":
        return now.date()
    if raw == "yesterday":
        return (now - timedelta(days=1)).date()
    return date.fromisoformat(raw)


def _connect_readonly(db_path: str) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(str(path))
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def load_trades(db_path: str) -> tuple[list[Trade], int, bool]:
    path = Path(db_path).expanduser()
    if not path.exists():
        return [], 0, False
    with _connect_readonly(str(path)) as conn:
        rows = conn.execute(
            """
            SELECT id, symbol, side, entry_price, exit_price, sl, tp,
                   entry_ts, exit_ts, exit_reason, pnl_pct, setup_reason,
                   regime, session_hour, mfe_10, mfe_50, mfe_100,
                   mae_10, mae_50, mae_100
            FROM trades
            ORDER BY id
            """
        ).fetchall()
    return [Trade.from_row(r) for r in rows], len(rows), True


def symbol_status(symbol: str) -> str:
    s = symbol.upper()
    if s in OFFICIAL_SYMBOLS:
        return "official"
    short = s.split(":")[-1]
    if f"COINBASE:{short}" in OFFICIAL_SYMBOLS:
        return "official_short"
    if any(short.startswith(root) for root in CRYPTO_ROOTS):
        return "crypto_alias_not_official"
    return "non_competition_symbol"


def _valid_sl(trade: Trade) -> bool:
    if trade.sl is None:
        return False
    if trade.side == "long":
        return trade.sl < trade.entry_price
    if trade.side == "short":
        return trade.sl > trade.entry_price
    return False


def _valid_tp(trade: Trade) -> bool:
    if trade.tp is None:
        return False
    if trade.side == "long":
        return trade.tp > trade.entry_price
    if trade.side == "short":
        return trade.tp < trade.entry_price
    return False


def _event_times(trades: Iterable[Trade]) -> list[tuple[datetime, int, str]]:
    events: list[tuple[datetime, int, str]] = []
    for t in trades:
        events.append((t.entry_ts, t.id, "entry"))
        if t.exit_ts is not None:
            events.append((t.exit_ts, t.id, "exit"))
    return sorted(events, key=lambda x: x[0])


def _has_high_frequency_cluster(trades: list[Trade]) -> bool:
    events = _event_times(trades)
    j = 0
    for i, (ts, _trade_id, _kind) in enumerate(events):
        while j < len(events) and (events[j][0] - ts).total_seconds() < 60:
            j += 1
        if j - i >= TX_PER_MINUTE_BLOCK:
            return True
    return False


def _risk_quality(trade: Trade) -> float:
    risk = trade.risk_pct
    if risk is None or risk <= 0:
        return 0.0
    if risk <= 1.0:
        return 1.0
    if risk <= 1.5:
        return 0.7
    if risk <= 2.0:
        return 0.4
    return 0.1


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def _group_key(trade: Trade, group_by: str) -> str:
    if group_by == "setup":
        return trade.setup_reason or "unknown_setup"
    if group_by == "symbol":
        return trade.symbol
    if group_by == "regime":
        return trade.regime or "unknown_regime"
    if group_by == "side":
        return trade.side
    if group_by == "session_hour":
        return str(trade.session_hour) if trade.session_hour is not None else "unknown_hour"
    raise ValueError(f"unsupported group field: {group_by}")


def _group_stats(trades: list[Trade], group_by: str, top: int) -> list[GroupStat]:
    grouped: dict[str, list[Trade]] = {}
    for trade in trades:
        grouped.setdefault(_group_key(trade, group_by), []).append(trade)
    stats: list[GroupStat] = []
    for key, rows in grouped.items():
        pnls = [t.pnl_pct or 0.0 for t in rows]
        wins = sum(1 for p in pnls if p > 0)
        r_vals = [t.r_multiple for t in rows if t.r_multiple is not None]
        best = max(rows, key=lambda t: (t.pnl_pct or 0.0, -t.id)) if rows else None
        worst = min(rows, key=lambda t: (t.pnl_pct or 0.0, t.id)) if rows else None
        stats.append(GroupStat(
            key=key,
            count=len(rows),
            wins=wins,
            win_rate=(wins / len(rows) * 100) if rows else 0.0,
            total_pnl_pct=sum(pnls),
            avg_pnl_pct=_mean(pnls),
            avg_r=_mean([float(v) for v in r_vals]) if r_vals else None,
            best_trade_id=best.id if best else None,
            worst_trade_id=worst.id if worst else None,
        ))
    stats.sort(key=lambda s: (-s.total_pnl_pct, -s.avg_pnl_pct, s.key))
    return stats[:top]


def _trade_snapshot(trade: Trade | None) -> dict[str, Any] | None:
    if trade is None:
        return None
    return {
        "id": trade.id,
        "symbol": trade.symbol,
        "side": trade.side,
        "pnl_pct": trade.pnl_pct,
        "r_multiple": trade.r_multiple,
        "exit_reason": trade.exit_reason,
        "setup_reason": trade.setup_reason,
        "regime": trade.regime,
        "entry_ts": trade.entry_ts.isoformat().replace("+00:00", "Z"),
        "exit_ts": trade.exit_ts.isoformat().replace("+00:00", "Z") if trade.exit_ts else None,
    }


def analyze(db_path: str = DEFAULT_DB_PATH, day: date | None = None,
            group_by: str = "setup", top: int = 5,
            now: datetime | None = None) -> Analysis:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day = day or now.date()
    if group_by not in GROUP_FIELDS:
        raise ValueError(f"--group-by must be one of {sorted(GROUP_FIELDS)}")

    trades, total_rows, journal_exists = load_trades(db_path)
    start, end = _day_bounds(day)
    daily_activity = [
        t for t in trades
        if (start <= t.entry_ts < end) or (t.exit_ts is not None and start <= t.exit_ts < end)
    ]
    daily_closed = [t for t in trades if t.is_closed and t.exit_ts is not None and start <= t.exit_ts < end]
    daily_open = [t for t in daily_activity if not t.is_closed]

    contest_events = [
        ts.date().isoformat()
        for ts, _trade_id, _kind in _event_times(trades)
        if CONTEST_START <= ts <= CONTEST_END
    ]
    activity_days = sorted(set(contest_events))
    missing_days = max(0, MIN_ACTIVITY_DAYS - len(activity_days))

    violations: list[str] = []
    limitations = [
        "trade_journal has no quantity/notional column, so leaderboard-dollar PnL cannot be reconstructed; ranking uses realized pnl_pct and R multiple.",
        "trade_journal has no account marker, so this tool cannot prove the row came from the TradingView 'The Leap Crypto' competition sub-account.",
    ]

    if not journal_exists:
        limitations.append("journal file does not exist yet; no schema initialization was attempted because this room is read-only.")

    for t in daily_activity:
        status = symbol_status(t.symbol)
        if status == "crypto_alias_not_official":
            violations.append(f"trade #{t.id} uses crypto alias {t.symbol}; official contest instruments are COINBASE:*USDC.P only")
        elif status == "non_competition_symbol":
            violations.append(f"trade #{t.id} uses non-competition symbol {t.symbol}")
        if not _valid_sl(t):
            violations.append(f"trade #{t.id} missing or direction-wrong SL")
        if not _valid_tp(t):
            violations.append(f"trade #{t.id} missing or direction-wrong TP")
        if t.risk_pct is not None and t.risk_pct > 2.0:
            violations.append(f"trade #{t.id} price-risk {t.risk_pct:.2f}% is oversized for prop discipline")

    if _has_high_frequency_cluster(daily_activity):
        violations.append(f"{TX_PER_MINUTE_BLOCK}+ journal events inside one minute; TradingView can restrict Paper Trading access")

    if not daily_activity:
        score_parts = {"sl_tp": 0.0, "planned_exit": 0.0, "risk": 0.0, "contest_consistency": 0.0}
        score = 0
    else:
        sl_tp_part = _mean([((_valid_sl(t) + _valid_tp(t)) / 2.0) for t in daily_activity])
        if daily_closed:
            exit_part = _mean([1.0 if (t.exit_reason or "") in PLANNED_EXIT_REASONS else 0.0 for t in daily_closed])
        else:
            exit_part = 0.0
        risk_part = _mean([_risk_quality(t) for t in daily_activity])
        status_scores = []
        for t in daily_activity:
            status = symbol_status(t.symbol)
            status_scores.append(1.0 if status in {"official", "official_short"} else 0.6 if status == "crypto_alias_not_official" else 0.0)
        consistency_part = 0.7 * _mean(status_scores) + 0.3 * (0.0 if _has_high_frequency_cluster(daily_activity) else 1.0)
        score_parts = {
            "sl_tp": round(sl_tp_part * 40, 2),
            "planned_exit": round(exit_part * 30, 2),
            "risk": round(risk_part * 20, 2),
            "contest_consistency": round(consistency_part * 10, 2),
        }
        score = int(round(sum(score_parts.values())))

    top_groups = _group_stats(daily_closed, group_by, top)
    best = max(daily_closed, key=lambda t: ((t.pnl_pct or 0.0), -t.id), default=None)
    worst = min(daily_closed, key=lambda t: ((t.pnl_pct or 0.0), t.id), default=None)

    if any("60+" in v for v in violations):
        verdict = "BLOCK"
    elif any("non-competition symbol" in v or "official contest instruments" in v for v in violations):
        verdict = "BLOCK"
    elif not journal_exists or not daily_activity or missing_days > 0 or score < 70:
        verdict = "FLAG"
    else:
        verdict = "PASS"

    return Analysis(
        day=day.isoformat(),
        now_utc=now.isoformat().replace("+00:00", "Z"),
        journal_path=str(Path(db_path).expanduser()),
        journal_exists=journal_exists,
        total_rows=total_rows,
        daily_activity_count=len(daily_activity),
        daily_closed_count=len(daily_closed),
        daily_open_count=len(daily_open),
        contest_activity_days=activity_days,
        missing_activity_days=missing_days,
        discipline_score=score,
        score_parts=score_parts,
        top_groups=top_groups,
        best_trade=_trade_snapshot(best),
        worst_trade=_trade_snapshot(worst),
        violations=sorted(set(violations)),
        limitations=limitations,
        verdict=verdict,
    )


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.3f}%"


def _fmt_r(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}R"


def render_chat(analysis: Analysis, quiet: bool = False) -> str:
    lines: list[str] = [HEADER]
    lines.append(f"day_utc: {analysis.day} | now_utc: {analysis.now_utc}")
    lines.append(f"journal: {analysis.journal_path} | rows={analysis.total_rows} | read_only=yes")
    lines.append(
        "contest: The Leap Crypto May 2026 | balance=$100,000 virtual | "
        f"crypto leverage={CRYPTO_LEVERAGE} | commission={COMMISSION_PCT}% | "
        "rank=realized closed PnL"
    )
    lines.append("")

    if analysis.limitations:
        lines.append("DATA LIMITATIONS")
        for item in analysis.limitations:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("ATA ROOM")
    lines.append(
        f"[Rule Referee] Activity days: {len(analysis.contest_activity_days)}/{MIN_ACTIVITY_DAYS}. "
        f"Missing={analysis.missing_activity_days}. Today activity={analysis.daily_activity_count}, "
        f"closed={analysis.daily_closed_count}, still_open={analysis.daily_open_count}."
    )
    lines.append(
        f"[Discipline Cop] Score={analysis.discipline_score}/100 "
        f"(SL/TP {analysis.score_parts.get('sl_tp', 0):.1f}/40, "
        f"planned exits {analysis.score_parts.get('planned_exit', 0):.1f}/30, "
        f"risk {analysis.score_parts.get('risk', 0):.1f}/20, "
        f"contest consistency {analysis.score_parts.get('contest_consistency', 0):.1f}/10)."
    )

    if analysis.top_groups:
        champ = analysis.top_groups[0]
        lines.append(
            f"[PnL Hawk] Today's top bucket is '{champ.key}' with "
            f"{_fmt_pct(champ.total_pnl_pct)} total realized pnl_pct, "
            f"avg={_fmt_pct(champ.avg_pnl_pct)}, win_rate={champ.win_rate:.1f}%, "
            f"avg_r={_fmt_r(champ.avg_r)}."
        )
    else:
        lines.append("[PnL Hawk] No closed trade for this UTC day, so nobody gets to claim scoreboard alpha yet.")

    if analysis.best_trade:
        bt = analysis.best_trade
        lines.append(
            f"[Why Explainer] Best closed row #{bt['id']} {bt['symbol']} {bt['side']} "
            f"ended {_fmt_pct(bt['pnl_pct'])} / {_fmt_r(bt['r_multiple'])}; "
            f"reason={bt['exit_reason']}, setup={bt['setup_reason']}, regime={bt['regime']}."
        )
    else:
        lines.append("[Why Explainer] Nothing to explain until a position is closed; open PnL does not rank in The Leap.")

    if analysis.violations:
        lines.append(f"[Risk Officer] Violations found: {len(analysis.violations)}. First: {analysis.violations[0]}")
    else:
        lines.append("[Risk Officer] No journal-level violation detected for this day. Keep the account/sub-account check manual.")

    lines.append(
        f"[Gatekeeper] verdict={analysis.verdict}. "
        "Next legal move: log facts, close loops, obey pre-trade checklist; this room does not suggest entries."
    )

    if not quiet:
        lines.append("")
        lines.append("TOP BUCKETS")
        if analysis.top_groups:
            for idx, g in enumerate(analysis.top_groups, 1):
                lines.append(
                    f"{idx}. {g.key} | n={g.count} | wins={g.wins} | "
                    f"win_rate={g.win_rate:.1f}% | total={_fmt_pct(g.total_pnl_pct)} | "
                    f"avg={_fmt_pct(g.avg_pnl_pct)} | avg_r={_fmt_r(g.avg_r)} | "
                    f"best_id={g.best_trade_id} | worst_id={g.worst_trade_id}"
                )
        else:
            lines.append("- none")

        lines.append("")
        lines.append("VIOLATIONS")
        if analysis.violations:
            for v in analysis.violations:
                lines.append(f"- {v}")
        else:
            lines.append("- none")

        lines.append("")
        lines.append("OFFICIAL SYMBOLS")
        for sym, max_pos in MAX_OPEN_POSITIONS.items():
            lines.append(f"- {sym}: max_open_position={max_pos:g}")

    return "\n".join(lines)


def render_json(analysis: Analysis) -> str:
    payload = asdict(analysis)
    payload["top_groups"] = [asdict(g) for g in analysis.top_groups]
    return json.dumps(payload, indent=2, sort_keys=True)


def run_once(args: argparse.Namespace) -> int:
    target_day = parse_day(args.date)
    try:
        analysis = analyze(
            db_path=args.db,
            day=target_day,
            group_by=args.group_by,
            top=args.top,
        )
    except sqlite3.Error as exc:
        print(HEADER)
        print(f"BLOCK: could not read journal safely: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(render_json(analysis))
    else:
        print(render_chat(analysis, quiet=args.quiet))
    return 0 if analysis.verdict != "BLOCK" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leap_moe_room",
        description="Read-only ATA/MoE accountability room for TradingView The Leap Crypto journal rows.",
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="trade_journal.sqlite path")
    parser.add_argument("--date", default="today", help="UTC day: today, yesterday, or YYYY-MM-DD")
    parser.add_argument("--group-by", choices=sorted(GROUP_FIELDS), default="setup")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--quiet", action="store_true", help="omit detailed bucket/violation tables")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--watch-seconds", type=int, default=0,
                        help="repeat every N seconds; 0 means one shot")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.top <= 0:
        raise SystemExit("--top must be positive")
    if args.watch_seconds and args.json:
        raise SystemExit("--watch-seconds with --json would stream multiple JSON documents; use text mode")
    rc = 0
    while True:
        rc = run_once(args)
        if args.watch_seconds <= 0:
            return rc
        print("\n--- next read-only room refresh in %ss ---\n" % args.watch_seconds, flush=True)
        time.sleep(args.watch_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
