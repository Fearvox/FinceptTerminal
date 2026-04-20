"""
Trade journal — SQLite persistent log for the propfirm v4 engine.

Schema per spec §5 (17 columns). Every closed trade writes one row; P4
MFE tracker back-fills the mfe_/mae_ columns after +10/+50/+100 bars.

Usage:
    from propfirm_engine.trade_journal import init_schema, DEFAULT_DB_PATH
    init_schema()                       # creates ./trade_journal.sqlite
    init_schema("/tmp/custom.sqlite")   # or anywhere you like

The init is idempotent — running twice is safe.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "trade_journal.sqlite")

# 17 columns, spec §5. Order preserved for readability of `.schema trades`.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,
    side          TEXT    NOT NULL CHECK (side IN ('long', 'short')),
    entry_price   REAL    NOT NULL,
    exit_price    REAL,
    sl            REAL,
    tp            REAL,
    entry_ts      TEXT    NOT NULL,
    exit_ts       TEXT,
    exit_reason   TEXT    CHECK (exit_reason IN (
                      'tp', 'sl', 'atr_trail', 'regime_flip',
                      'session_close', 'manual', NULL
                  ) OR exit_reason IS NULL),
    pnl_pct       REAL,
    setup_reason  TEXT,
    regime        TEXT,
    session_hour  INTEGER,
    mfe_10        REAL,
    mfe_50        REAL,
    mfe_100       REAL,
    mae_10        REAL,
    mae_50        REAL,
    mae_100       REAL
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_entry_ts ON trades(entry_ts);
CREATE INDEX IF NOT EXISTS idx_trades_exit_reason ON trades(exit_reason);
"""


def init_schema(db_path: str = DEFAULT_DB_PATH) -> str:
    """Create the trades table (if missing). Returns the absolute db path."""
    abs_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with sqlite3.connect(abs_path) as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    return abs_path


@contextmanager
def connect(db_path: str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a Row-factory connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


if __name__ == "__main__":
    path = init_schema()
    print(f"trade_journal schema initialised at {path}")
