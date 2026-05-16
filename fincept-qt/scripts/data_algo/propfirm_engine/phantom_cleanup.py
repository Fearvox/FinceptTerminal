"""
Phantom journal cleanup — close stale `exit_ts IS NULL` rows.

Context:
  The journal records every paper-shadow signal (smc, willy) at entry time.
  Because allow-only dispatch is fincept-only, the shadow rows never see a
  matching close alert and accumulate as "open" in the journal even though
  they were never real positions. By T+24h the journal has dozens of these
  per source, which:
    - inflates `n_open` in /fin-deep
    - makes "open positions" reports unusable
    - distorts source-level win/loss math (closed wins/losses divided over
      enrolled total)

This script closes phantom rows by:
    UPDATE trades
       SET exit_ts = now(),
           exit_reason = 'manual',  -- schema CHECK constraint allows this
           exit_price = NULL,        -- intentional: these were never real fills
           pnl_pct = NULL            -- intentional: leave out of PnL stats
     WHERE exit_ts IS NULL
       AND entry_ts < cutoff(now() - N hours)
       AND id NOT IN (LIVE_KEEP_IDS)

Live-kept rows: pass `--keep-ids 1,2,3` for any real positions you want to
preserve (e.g. the live TV paper SOL long that's not in the journal anyway).

Usage:
    # dry-run (default)
    python -m propfirm_engine.phantom_cleanup --threshold-hours 2

    # apply with auto-backup
    python -m propfirm_engine.phantom_cleanup --threshold-hours 2 --apply

    # only specific source
    python -m propfirm_engine.phantom_cleanup --source smc --apply

Atomic: takes a backup of trade_journal.sqlite → trade_journal.sqlite.bak-<ts>
before running the UPDATE. Use `--no-backup` to skip (not recommended).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

from .trade_journal import DEFAULT_DB_PATH

VALID_SOURCES = ("fincept", "smc", "willy", "all")


def _source_filter(src: str) -> str:
    if src == "all":
        return "1=1"
    return f"setup_reason LIKE '{src}\\_%' ESCAPE '\\'"


def _cutoff_iso(hours: float) -> str:
    return f"datetime('now', '-{hours} hours')"


def preview(conn: sqlite3.Connection, threshold_hours: float, source: str,
            keep_ids: list[int]) -> list[sqlite3.Row]:
    keep_clause = ""
    if keep_ids:
        keep_clause = f" AND id NOT IN ({','.join(str(i) for i in keep_ids)})"
    sql = f"""
    SELECT id, symbol, side, setup_reason, entry_ts,
           round((julianday('now') - julianday(entry_ts)) * 24, 1) AS age_hours
    FROM trades
    WHERE exit_ts IS NULL
      AND entry_ts < strftime('%Y-%m-%dT%H:%M:%SZ', {_cutoff_iso(threshold_hours)})
      AND {_source_filter(source)}
      {keep_clause}
    ORDER BY entry_ts ASC
    """
    return conn.execute(sql).fetchall()


def apply_cleanup(conn: sqlite3.Connection, threshold_hours: float, source: str,
                  keep_ids: list[int]) -> int:
    keep_clause = ""
    if keep_ids:
        keep_clause = f" AND id NOT IN ({','.join(str(i) for i in keep_ids)})"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sql = f"""
    UPDATE trades
       SET exit_ts = ?,
           exit_reason = 'manual',
           exit_price = NULL,
           pnl_pct = NULL
     WHERE exit_ts IS NULL
       AND entry_ts < strftime('%Y-%m-%dT%H:%M:%SZ', {_cutoff_iso(threshold_hours)})
       AND {_source_filter(source)}
       {keep_clause}
    """
    cur = conn.execute(sql, (now_iso,))
    conn.commit()
    return cur.rowcount


def backup_db(db_path: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = f"{db_path}.bak-{ts}"
    shutil.copy2(db_path, dst)
    return dst


def main() -> int:
    p = argparse.ArgumentParser(prog="phantom_cleanup")
    p.add_argument("--db", default=DEFAULT_DB_PATH, help="path to trade_journal.sqlite")
    p.add_argument("--threshold-hours", type=float, default=2.0,
                   help="rows older than this many hours qualify as phantom (default 2)")
    p.add_argument("--source", choices=VALID_SOURCES, default="all",
                   help="restrict cleanup to one source (default: all)")
    p.add_argument("--keep-ids", default="",
                   help="comma-separated trade IDs to preserve (e.g. '1567,1568')")
    p.add_argument("--apply", action="store_true",
                   help="execute the UPDATE (without this flag, dry-run only)")
    p.add_argument("--no-backup", action="store_true",
                   help="skip the .bak file (not recommended)")
    args = p.parse_args()

    keep_ids: list[int] = []
    if args.keep_ids.strip():
        keep_ids = [int(x.strip()) for x in args.keep_ids.split(",") if x.strip()]

    if not os.path.exists(args.db):
        print(f"ERROR: db not found at {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = preview(conn, args.threshold_hours, args.source, keep_ids)
    print(f"Phantom candidates: {len(rows)} rows "
          f"(source={args.source}, threshold={args.threshold_hours}h, keep={keep_ids})")
    if rows:
        by_src: dict[str, int] = {}
        for r in rows:
            sr = (r["setup_reason"] or "").split("_")[0] or "legacy"
            by_src[sr] = by_src.get(sr, 0) + 1
        print(f"  by source: {by_src}")
        print(f"  oldest: id={rows[0]['id']} {rows[0]['entry_ts']} ({rows[0]['age_hours']}h)")
        print(f"  newest: id={rows[-1]['id']} {rows[-1]['entry_ts']} ({rows[-1]['age_hours']}h)")

    if not args.apply:
        print("\n(dry-run) Re-run with --apply to execute.")
        return 0

    if not rows:
        print("Nothing to clean. Exiting.")
        return 0

    if not args.no_backup:
        backup_path = backup_db(args.db)
        print(f"Backup written: {backup_path}")

    affected = apply_cleanup(conn, args.threshold_hours, args.source, keep_ids)
    print(f"UPDATE complete: {affected} rows closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
