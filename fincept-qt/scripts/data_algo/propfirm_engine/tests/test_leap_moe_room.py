from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from propfirm_engine import leap_moe_room as room


SCHEMA = """
CREATE TABLE trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,
    side          TEXT    NOT NULL,
    entry_price   REAL    NOT NULL,
    exit_price    REAL,
    sl            REAL,
    tp            REAL,
    entry_ts      TEXT    NOT NULL,
    exit_ts       TEXT,
    exit_reason   TEXT,
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
"""


def _make_db(tmp_path: Path, rows: list[dict]) -> str:
    path = tmp_path / "journal.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        for row in rows:
            payload = {
                "symbol": "COINBASE:BTCUSDC.P",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 102.0,
                "sl": 99.2,
                "tp": 102.0,
                "entry_ts": "2026-05-04T12:00:00Z",
                "exit_ts": "2026-05-04T13:00:00Z",
                "exit_reason": "tp",
                "pnl_pct": 2.0,
                "setup_reason": "willy_a",
                "regime": "trend",
                "session_hour": 12,
                "mfe_10": 1.0,
                "mfe_50": 2.0,
                "mfe_100": 2.5,
                "mae_10": -0.2,
                "mae_50": -0.3,
                "mae_100": -0.4,
            }
            payload.update(row)
            cols = ", ".join(payload.keys())
            qs = ", ".join(["?"] * len(payload))
            conn.execute(f"INSERT INTO trades ({cols}) VALUES ({qs})", tuple(payload.values()))
        conn.commit()
    return str(path)


def test_readonly_connection_blocks_mutation(tmp_path: Path):
    db = _make_db(tmp_path, [{}])
    with room._connect_readonly(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO trades (symbol, side, entry_price, entry_ts) VALUES ('X', 'long', 1, '2026-05-04T00:00:00Z')")


def test_metrics_rank_best_setup_and_pass_when_clean(tmp_path: Path):
    db = _make_db(tmp_path, [
        {"setup_reason": "willy_a", "pnl_pct": 2.0, "exit_price": 102.0},
        {"setup_reason": "willy_a", "pnl_pct": 1.0, "exit_price": 101.0},
        {"setup_reason": "willy_b", "pnl_pct": -0.5, "exit_price": 99.5, "exit_reason": "sl"},
    ])
    analysis = room.analyze(db, day=date(2026, 5, 4), group_by="setup", now=datetime(2026, 5, 4, tzinfo=timezone.utc))
    assert analysis.daily_activity_count == 3
    assert analysis.daily_closed_count == 3
    assert analysis.discipline_score >= 90
    assert analysis.top_groups[0].key == "willy_a"
    assert analysis.top_groups[0].total_pnl_pct == pytest.approx(3.0)
    assert analysis.best_trade and analysis.best_trade["pnl_pct"] == pytest.approx(2.0)
    assert analysis.verdict == "FLAG"  # still lacks three contest activity days


def test_non_competition_symbol_blocks_for_leap(tmp_path: Path):
    db = _make_db(tmp_path, [{"symbol": "TVC:GOLD"}])
    analysis = room.analyze(db, day=date(2026, 5, 4), now=datetime(2026, 5, 4, tzinfo=timezone.utc))
    assert analysis.verdict == "BLOCK"
    assert any("non-competition symbol" in v for v in analysis.violations)


def test_high_frequency_cluster_blocks(tmp_path: Path):
    base = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(30):
        entry = base + timedelta(seconds=i)
        exit_ts = base + timedelta(seconds=i + 1)
        rows.append({
            "entry_ts": entry.isoformat().replace("+00:00", "Z"),
            "exit_ts": exit_ts.isoformat().replace("+00:00", "Z"),
            "setup_reason": f"burst_{i}",
        })
    db = _make_db(tmp_path, rows)
    analysis = room.analyze(db, day=date(2026, 5, 4), now=base)
    assert analysis.verdict == "BLOCK"
    assert any("60+" in v for v in analysis.violations)


def test_empty_day_is_flag_not_crash(tmp_path: Path):
    db = _make_db(tmp_path, [{"entry_ts": "2026-05-03T12:00:00Z", "exit_ts": "2026-05-03T13:00:00Z"}])
    analysis = room.analyze(db, day=date(2026, 5, 4), now=datetime(2026, 5, 4, tzinfo=timezone.utc))
    assert analysis.daily_activity_count == 0
    assert analysis.discipline_score == 0
    assert analysis.top_groups == []
    assert analysis.verdict == "FLAG"


def test_render_chat_has_hard_safety_header(tmp_path: Path):
    db = _make_db(tmp_path, [{}])
    analysis = room.analyze(db, day=date(2026, 5, 4), now=datetime(2026, 5, 4, tzinfo=timezone.utc))
    rendered = room.render_chat(analysis)
    assert rendered.startswith(room.HEADER)
    assert "NO TRADES EXECUTED" in rendered
    assert "this room does not suggest entries" in rendered
    assert "TOP BUCKETS" in rendered


def test_missing_journal_does_not_create_file(tmp_path: Path):
    missing = tmp_path / "missing.sqlite"
    analysis = room.analyze(str(missing), day=date(2026, 5, 4), now=datetime(2026, 5, 4, tzinfo=timezone.utc))
    assert not missing.exists()
    assert analysis.journal_exists is False
    assert analysis.total_rows == 0
    assert analysis.verdict == "FLAG"


def test_static_safety_no_network_or_dynamic_code():
    source = Path(room.__file__).read_text()
    forbidden = ["import requests", "import ccxt", "import subprocess", "eval(", "exec("]
    for token in forbidden:
        assert token not in source
