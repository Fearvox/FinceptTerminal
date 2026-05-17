"""Wolf-mode ledger: append-only JSONL of every decision + SQLite for closed-PnL.

JSONL row contract (one per decision):
    {
      "ts": "2026-05-17T01:23:45Z",
      "cycle_id": "20260517T012300",
      "phase": "dry_run" | "live",
      "candidate": {"contract_id": str, "slug": str, "url": str, ...},
      "score": {"raw_edge": ..., "kelly": ..., "liq": ..., "decay": ..., "conf": ..., "composite": ...},
      "decision": "BET" | "SKIP" | "WATCH",
      "size_mana": int (0 if SKIP/WATCH),
      "outcome": "YES" | "NO" | null,
      "reason": str (short),
      "auditor_used": bool,
      "result": {                 # filled later by audit_phase
        "bet_id": str | null,
        "error": str | null,
        "pre_balance": float,
        "post_balance": float | null
      } | null
    }
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Defaults — overridable via env for tests
DEFAULT_DIR = Path(__file__).parent / ".state"
LEDGER_FILE = DEFAULT_DIR / "wolf_ledger.jsonl"
AUDIT_DB = DEFAULT_DIR / "wolf_audit.sqlite"

_LOCK = threading.Lock()


def _ensure_dir():
    DEFAULT_DIR.mkdir(parents=True, exist_ok=True)


def utc_iso(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def cycle_id_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M00")


def append_decision(row: dict[str, Any]) -> None:
    _ensure_dir()
    row.setdefault("ts", utc_iso())
    with _LOCK, LEDGER_FILE.open("a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def read_recent(limit: int = 200) -> list[dict]:
    if not LEDGER_FILE.exists():
        return []
    out = []
    with LEDGER_FILE.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out[-limit:]


def init_audit_schema() -> Path:
    _ensure_dir()
    with sqlite3.connect(AUDIT_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS closed_positions (
                bet_id TEXT PRIMARY KEY,
                contract_id TEXT,
                slug TEXT,
                ts_open TEXT,
                ts_closed TEXT,
                outcome TEXT,
                size_mana REAL,
                score_at_entry REAL,
                pnl_mana REAL,
                resolved_yes_prob REAL
            );
            CREATE INDEX IF NOT EXISTS idx_closed_ts ON closed_positions(ts_closed);

            CREATE TABLE IF NOT EXISTS score_calibration (
                ts TEXT PRIMARY KEY,
                weights_json TEXT,
                hit_rate REAL,
                avg_pnl_mana REAL,
                n_samples INTEGER
            );

            CREATE TABLE IF NOT EXISTS killswitch (
                ts TEXT PRIMARY KEY,
                reason TEXT,
                drawdown_mana REAL
            );
            """
        )
    return AUDIT_DB


def record_closed(bet_id: str, contract_id: str, slug: str,
                  ts_open: str, ts_closed: str, outcome: str,
                  size_mana: float, score_at_entry: float,
                  pnl_mana: float, resolved_yes_prob: float | None) -> None:
    init_audit_schema()
    with sqlite3.connect(AUDIT_DB) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO closed_positions
               (bet_id, contract_id, slug, ts_open, ts_closed, outcome,
                size_mana, score_at_entry, pnl_mana, resolved_yes_prob)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (bet_id, contract_id, slug, ts_open, ts_closed, outcome,
             size_mana, score_at_entry, pnl_mana, resolved_yes_prob),
        )


def record_killswitch(reason: str, drawdown_mana: float) -> Path:
    init_audit_schema()
    with sqlite3.connect(AUDIT_DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO killswitch (ts, reason, drawdown_mana) VALUES (?, ?, ?)",
            (utc_iso(), reason, drawdown_mana),
        )
    flag = DEFAULT_DIR / "wolf_killswitch.flag"
    flag.write_text(f"{utc_iso()} {reason} drawdown={drawdown_mana}\n")
    return flag


def killswitch_active() -> bool:
    flag = DEFAULT_DIR / "wolf_killswitch.flag"
    user_override = Path.home() / "wolf_killswitch.flag"
    return flag.exists() or user_override.exists()


def stats_summary() -> dict:
    """Quick summary of ledger + audit DB. For daily brief."""
    rows = read_recent(2000)
    init_audit_schema()
    with sqlite3.connect(AUDIT_DB) as conn:
        closed = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl_mana),0), COALESCE(AVG(pnl_mana),0) "
            "FROM closed_positions"
        ).fetchone()
    return {
        "ledger_rows": len(rows),
        "decisions_bet": sum(1 for r in rows if r.get("decision") == "BET"),
        "decisions_skip": sum(1 for r in rows if r.get("decision") == "SKIP"),
        "decisions_watch": sum(1 for r in rows if r.get("decision") == "WATCH"),
        "closed_n": closed[0],
        "closed_pnl_mana": closed[1],
        "closed_avg_mana": closed[2],
        "killswitch_active": killswitch_active(),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(stats_summary(), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "tail":
        for row in read_recent(20):
            print(json.dumps(row))
    else:
        print("usage: wolf_ledger.py [stats|tail]")
