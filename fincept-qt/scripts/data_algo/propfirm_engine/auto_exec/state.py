"""
Operator visibility CLI for the propfirm-v4 p5b auto-exec pipeline.

Reads from:
  - dispatches.jsonl  (written by AutoExecDispatcher)
  - trade_journal.sqlite  (written by tv_webhook / log_tv_trade)

CLI:
    python -m propfirm_engine.auto_exec.state recent
    python -m propfirm_engine.auto_exec.state recent --since 1h --source fincept
    python -m propfirm_engine.auto_exec.state summary --since 24h
    python -m propfirm_engine.auto_exec.state status
    python -m propfirm_engine.auto_exec.state kill --reason "entering manual session"
    python -m propfirm_engine.auto_exec.state resume

dispatches.jsonl schema (one JSON object per line):
    {
      "ts":       "<ISO-8601 UTC>",           # written by dispatcher
      "payload":  {<original alert dict>},    # full inbound payload
      "decision": {
        "dispatched": bool,
        "dry_run":    bool,
        "reason":     str,                    # e.g. "policy:ok", "kill_switch"
        "qty":        int | null,
        "raw":        dict | null
      }
    }
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# .logs/ lives next to this file (inside auto_exec/).
_DEFAULT_LOG_DIR: pathlib.Path = pathlib.Path(__file__).parent / ".logs"

# Pull DEFAULT_DB_PATH from trade_journal without importing the whole engine.
def _default_db_path() -> str:
    """Resolve the trade journal SQLite path from the sibling module.

    Returns:
        Absolute path string.  Falls back to a local ``trade_journal.sqlite``
        adjacent to this file if the import fails.
    """
    try:
        from propfirm_engine.trade_journal import DEFAULT_DB_PATH  # noqa: PLC0415
        return DEFAULT_DB_PATH
    except ImportError:
        return str(pathlib.Path(__file__).parent.parent / "trade_journal.sqlite")


# Kill switch file path — dispatcher checks this at every on_entry_alert call.
_KILL_SWITCH_FILENAME = ".kill_switch"


def _kill_switch_path(log_dir: pathlib.Path) -> pathlib.Path:
    """Return the canonical kill switch file path.

    Args:
        log_dir: Directory where dispatches.jsonl lives.

    Returns:
        ``pathlib.Path`` pointing to ``.kill_switch`` inside *log_dir*.
    """
    return log_dir / _KILL_SWITCH_FILENAME


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_duration(spec: str) -> timedelta:
    """Parse a human duration spec like ``"1h"``, ``"30m"``, ``"24h"``.

    Args:
        spec: Duration string.  Suffix ``h`` = hours, ``m`` = minutes,
            ``d`` = days.  No suffix treated as seconds.

    Returns:
        ``datetime.timedelta`` equivalent.

    Raises:
        ValueError: If the spec cannot be parsed.
    """
    spec = spec.strip().lower()
    if spec.endswith("h"):
        return timedelta(hours=float(spec[:-1]))
    if spec.endswith("m"):
        return timedelta(minutes=float(spec[:-1]))
    if spec.endswith("d"):
        return timedelta(days=float(spec[:-1]))
    return timedelta(seconds=float(spec))


def _parse_iso(ts_str: str) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp string to an aware datetime.

    Handles both ``Z`` suffix and ``+00:00`` offset.

    Args:
        ts_str: Timestamp string.

    Returns:
        Aware UTC ``datetime``, or ``None`` if parsing fails.
    """
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f+00:00",
                "%Y-%m-%dT%H:%M:%S+00:00",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(ts_str[:26], fmt[:len(ts_str[:26])])
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Last-ditch: strip trailing Z and fromisoformat
    try:
        clean = ts_str.rstrip("Z").split("+")[0]
        return datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# JSONL reader
# ---------------------------------------------------------------------------

def _read_dispatches(
    jsonl_path: pathlib.Path,
    since: datetime | None = None,
    source_filter: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Read and filter records from ``dispatches.jsonl``.

    Args:
        jsonl_path: Path to the JSONL file.
        since: Only return records at or after this timestamp.
        source_filter: If set, only records whose ``payload.source`` matches.
        limit: Maximum number of records to return (most-recent first).

    Returns:
        List of parsed record dicts, newest-first.
    """
    if not jsonl_path.exists():
        return []

    records: list[dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                rec = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            ts_str: str = rec.get("ts", "")
            ts = _parse_iso(ts_str)
            if since is not None and ts is not None and ts < since:
                continue

            payload = rec.get("payload") or {}
            if source_filter and payload.get("source", "") != source_filter:
                continue

            records.append(rec)

    # Reverse so newest is first; apply limit.
    records.reverse()
    if limit is not None:
        records = records[:limit]
    return records


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _fmt_recent_table(records: list[dict[str, Any]]) -> str:
    """Format recent dispatch records as an aligned table.

    Columns: ts, source, action, ticker, dispatched, reason.

    Args:
        records: List of dispatch records.

    Returns:
        Multi-line string.
    """
    col_w = {
        "ts":         24,
        "source":     9,
        "action":     7,
        "ticker":     14,
        "dispatched": 11,
        "reason":     40,
    }
    header = (
        f"{'ts':<{col_w['ts']}}"
        f"{'source':<{col_w['source']}}"
        f"{'action':<{col_w['action']}}"
        f"{'ticker':<{col_w['ticker']}}"
        f"{'dispatched':<{col_w['dispatched']}}"
        f"{'reason':<{col_w['reason']}}"
    )
    sep = "-" * len(header)
    lines = [header, sep]

    for rec in records:
        ts = rec.get("ts", "")[:23]  # trim sub-second precision for display
        payload = rec.get("payload") or {}
        decision = rec.get("decision") or {}
        source = str(payload.get("source", ""))
        action = str(payload.get("action", ""))
        ticker = str(payload.get("ticker", ""))
        dispatched = str(decision.get("dispatched", ""))
        reason = str(decision.get("reason", ""))[:col_w["reason"]]
        lines.append(
            f"{ts:<{col_w['ts']}}"
            f"{source:<{col_w['source']}}"
            f"{action:<{col_w['action']}}"
            f"{ticker:<{col_w['ticker']}}"
            f"{dispatched:<{col_w['dispatched']}}"
            f"{reason:<{col_w['reason']}}"
        )

    if len(lines) == 2:
        lines.append("(no records)")

    return "\n".join(lines)


def _fmt_recent_json(records: list[dict[str, Any]]) -> str:
    """Format records as pretty-printed JSON array.

    Args:
        records: List of dispatch records.

    Returns:
        JSON string.
    """
    slim = []
    for rec in records:
        payload = rec.get("payload") or {}
        decision = rec.get("decision") or {}
        slim.append({
            "ts": rec.get("ts"),
            "source": payload.get("source"),
            "action": payload.get("action"),
            "ticker": payload.get("ticker"),
            "dispatched": decision.get("dispatched"),
            "dry_run": decision.get("dry_run"),
            "reason": decision.get("reason"),
            "qty": decision.get("qty"),
        })
    return json.dumps(slim, indent=2, default=str)


def _fmt_summary_table(summary: dict[str, Any]) -> str:
    """Format summary dict as a human-readable text block.

    Args:
        summary: Output of ``_compute_summary``.

    Returns:
        Multi-line string.
    """
    lines: list[str] = []
    lines.append(f"Period  : {summary['since_label']}")
    lines.append(f"Total   : {summary['total']}  "
                 f"dispatched={summary['dispatched']}  "
                 f"blocked={summary['blocked']}")
    lines.append("")

    # Per-source breakdown
    col_w_src = 10
    col_w_d = 12
    col_w_b = 12
    hdr = (f"{'source':<{col_w_src}}"
           f"{'dispatched':<{col_w_d}}"
           f"{'blocked':<{col_w_b}}")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for src, counts in sorted(summary["by_source"].items()):
        lines.append(
            f"{src:<{col_w_src}}"
            f"{counts['dispatched']:<{col_w_d}}"
            f"{counts['blocked']:<{col_w_b}}"
        )

    lines.append("")
    lines.append("Top reject reasons:")
    for reason, count in summary["top_reject_reasons"]:
        lines.append(f"  {count:>4}  {reason}")

    if not summary["top_reject_reasons"]:
        lines.append("  (none)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Business logic helpers
# ---------------------------------------------------------------------------

def _compute_summary(records: list[dict[str, Any]], since_label: str) -> dict[str, Any]:
    """Aggregate dispatch records into a summary dict.

    Args:
        records: All records in the time window (pre-filtered by ``since``).
        since_label: Human label for the time window (e.g. ``"24h"``).

    Returns:
        Dict with keys: ``since_label``, ``total``, ``dispatched``, ``blocked``,
        ``by_source``, ``top_reject_reasons``.
    """
    total = len(records)
    dispatched = 0
    blocked = 0
    by_source: dict[str, dict[str, int]] = {}
    reject_reasons: Counter[str] = Counter()

    for rec in records:
        payload = rec.get("payload") or {}
        decision = rec.get("decision") or {}
        src = str(payload.get("source", "unknown"))
        is_dispatched = bool(decision.get("dispatched"))
        reason = str(decision.get("reason", ""))

        if src not in by_source:
            by_source[src] = {"dispatched": 0, "blocked": 0}

        if is_dispatched:
            dispatched += 1
            by_source[src]["dispatched"] += 1
        else:
            blocked += 1
            by_source[src]["blocked"] += 1
            if reason:
                reject_reasons[reason] += 1

    top_reject = reject_reasons.most_common(10)

    return {
        "since_label": since_label,
        "total": total,
        "dispatched": dispatched,
        "blocked": blocked,
        "by_source": by_source,
        "top_reject_reasons": top_reject,
    }


def _dispatches_today(jsonl_path: pathlib.Path) -> int:
    """Count dispatch records whose ``ts`` falls in the current UTC calendar day.

    Args:
        jsonl_path: Path to dispatches.jsonl.

    Returns:
        Integer count.
    """
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    records = _read_dispatches(jsonl_path, since=day_start)
    return len(records)


def _hourly_utilization(
    jsonl_path: pathlib.Path,
    source_filter: str | None = None,
) -> dict[str, int]:
    """Count dispatches per source in the current UTC hour.

    Args:
        jsonl_path: Path to dispatches.jsonl.
        source_filter: Optional single source to restrict count.

    Returns:
        Dict mapping source name to dispatch count this hour.
    """
    now = datetime.now(timezone.utc)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    records = _read_dispatches(jsonl_path, since=hour_start,
                               source_filter=source_filter)
    counts: Counter[str] = Counter()
    for rec in records:
        payload = rec.get("payload") or {}
        src = str(payload.get("source", "unknown"))
        decision = rec.get("decision") or {}
        if decision.get("dispatched"):
            counts[src] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_recent(args: argparse.Namespace, log_dir: pathlib.Path) -> int:
    """Handle the ``recent`` subcommand.

    Args:
        args: Parsed namespace.
        log_dir: Resolved log directory.

    Returns:
        Exit code.
    """
    jsonl_path = log_dir / "dispatches.jsonl"
    since = datetime.now(timezone.utc) - _parse_duration(args.since)
    records = _read_dispatches(
        jsonl_path,
        since=since,
        source_filter=args.source or None,
        limit=args.n,
    )

    if args.format == "json":
        print(_fmt_recent_json(records))
    else:
        print(_fmt_recent_table(records))
    return 0


def _cmd_summary(args: argparse.Namespace, log_dir: pathlib.Path) -> int:
    """Handle the ``summary`` subcommand.

    Args:
        args: Parsed namespace.
        log_dir: Resolved log directory.

    Returns:
        Exit code.
    """
    jsonl_path = log_dir / "dispatches.jsonl"
    since = datetime.now(timezone.utc) - _parse_duration(args.since)
    records = _read_dispatches(jsonl_path, since=since)
    summary = _compute_summary(records, since_label=args.since)

    if getattr(args, "format", "table") == "json":
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(_fmt_summary_table(summary))
    return 0


def _cmd_kill(args: argparse.Namespace, log_dir: pathlib.Path) -> int:
    """Handle the ``kill`` subcommand.

    Creates the kill switch file.  Confirms to stdout.

    Args:
        args: Parsed namespace.
        log_dir: Resolved log directory.

    Returns:
        Exit code.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    ks_path = _kill_switch_path(log_dir)
    reason_text = getattr(args, "reason", "") or ""
    ks_path.write_text(
        f"kill_switch activated\n"
        f"ts: {datetime.now(timezone.utc).isoformat()}\n"
        f"reason: {reason_text}\n",
        encoding="utf-8",
    )
    print(f"kill switch ACTIVATED: {ks_path}")
    if reason_text:
        print(f"reason: {reason_text}")
    return 0


def _cmd_resume(args: argparse.Namespace, log_dir: pathlib.Path) -> int:
    """Handle the ``resume`` subcommand.

    Deletes the kill switch file.  Confirms to stdout.

    Args:
        args: Parsed namespace.
        log_dir: Resolved log directory.

    Returns:
        Exit code.
    """
    ks_path = _kill_switch_path(log_dir)
    if not ks_path.exists():
        print(f"kill switch is not set (nothing to remove). path={ks_path}")
        return 0
    ks_path.unlink()
    print(f"kill switch CLEARED: {ks_path}")
    print("dispatcher will accept new signals on next alert.")
    return 0


def _cmd_status(args: argparse.Namespace, log_dir: pathlib.Path) -> int:
    """Handle the ``status`` subcommand.

    Shows kill switch state, today's total dispatches, current-hour per-source
    utilization, and log file info.

    Args:
        args: Parsed namespace.
        log_dir: Resolved log directory.

    Returns:
        Exit code.
    """
    jsonl_path = log_dir / "dispatches.jsonl"
    ks_path = _kill_switch_path(log_dir)

    # Kill switch state
    ks_active = ks_path.exists()
    ks_label = "ACTIVE" if ks_active else "clear"
    ks_reason = ""
    if ks_active:
        try:
            ks_reason = ks_path.read_text(encoding="utf-8").strip()
        except OSError:
            ks_reason = "(unreadable)"

    # Counts
    today_total = _dispatches_today(jsonl_path)
    hour_util = _hourly_utilization(jsonl_path)

    # Log file info
    log_size_bytes = 0
    log_exists = jsonl_path.exists()
    if log_exists:
        log_size_bytes = jsonl_path.stat().st_size

    lines: list[str] = []
    lines.append(f"kill_switch  : {ks_label}")
    if ks_active and ks_reason:
        for ks_line in ks_reason.splitlines():
            lines.append(f"               {ks_line}")
    lines.append(f"today_total  : {today_total}")
    lines.append(f"hour_util    : {json.dumps(hour_util) if hour_util else '(none this hour)'}")
    lines.append(f"log_file     : {jsonl_path}")
    lines.append(f"log_exists   : {log_exists}")
    lines.append(f"log_size     : {log_size_bytes:,} bytes")
    lines.append(f"kill_sw_path : {ks_path}")

    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI entry point for ``python -m propfirm_engine.auto_exec.state``.

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(
        prog="python -m propfirm_engine.auto_exec.state",
        description="Operator visibility tool for the auto-exec pipeline.",
    )
    parser.add_argument(
        "--log-dir",
        metavar="PATH",
        default=str(_DEFAULT_LOG_DIR),
        help=f"Directory containing dispatches.jsonl (default: {_DEFAULT_LOG_DIR}).",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=_default_db_path(),
        help="Path to trade_journal.sqlite (default: resolved from trade_journal module).",
    )

    sub = parser.add_subparsers(dest="cmd")

    # -- recent --
    recent_p = sub.add_parser("recent", help="Show recent dispatches from dispatches.jsonl.")
    recent_p.add_argument(
        "--since",
        default="1h",
        metavar="DURATION",
        help="How far back to look (e.g. '1h', '30m', '24h'). Default: 1h.",
    )
    recent_p.add_argument(
        "--source",
        default=None,
        metavar="NAME",
        help="Filter by source (fincept | willy | smc).",
    )
    recent_p.add_argument(
        "--n",
        type=int,
        default=50,
        metavar="N",
        help="Maximum rows to show (default: 50).",
    )
    recent_p.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table).",
    )

    # -- summary --
    summary_p = sub.add_parser("summary", help="Grouped dispatch counts + top reject reasons.")
    summary_p.add_argument(
        "--since",
        default="24h",
        metavar="DURATION",
        help="Time window (default: 24h).",
    )
    summary_p.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table).",
    )

    # -- kill --
    kill_p = sub.add_parser("kill", help="Activate the kill switch (blocks all dispatches).")
    kill_p.add_argument(
        "--reason",
        default="",
        metavar="TEXT",
        help="Optional reason text written into the kill switch file.",
    )

    # -- resume --
    sub.add_parser("resume", help="Clear the kill switch.")

    # -- status --
    sub.add_parser("status", help="Show kill switch state, counts, and log file info.")

    args = parser.parse_args()

    log_dir = pathlib.Path(args.log_dir).expanduser().resolve()

    if args.cmd == "recent":
        return _cmd_recent(args, log_dir)
    if args.cmd == "summary":
        return _cmd_summary(args, log_dir)
    if args.cmd == "kill":
        return _cmd_kill(args, log_dir)
    if args.cmd == "resume":
        return _cmd_resume(args, log_dir)
    if args.cmd == "status":
        return _cmd_status(args, log_dir)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
