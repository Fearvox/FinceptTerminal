from __future__ import annotations

import json
import os
from pathlib import Path

from propfirm_engine import fusion_panel


def test_sanitize_alert_payload_redacts_secret_like_keys_and_values():
    payload = {
        "ticker": "COINBASE:BTCUSDC.P",
        "price": 64000.0,
        "secret": "should-not-survive",
        "note": "Bearer abcdefghijklmnopqrstuvwxyz123456",
        "nested": {"api_key": "abc", "ok": "yes"},
    }

    safe = fusion_panel.sanitize_alert_payload(payload)

    assert safe["ticker"] == "COINBASE:BTCUSDC.P"
    assert safe["price"] == 64000.0
    assert safe["secret"] == "[redacted]"
    assert safe["note"] == "[redacted]"
    assert safe["nested"]["api_key"] == "[redacted]"
    assert safe["nested"]["ok"] == "yes"


def test_append_alert_event_creates_0600_jsonl_and_tail_loads(tmp_path: Path):
    log = tmp_path / "fusion_alerts.jsonl"
    event = fusion_panel.append_alert_event(
        str(log),
        kind="entry_opened",
        status="accepted",
        payload={"action": "buy", "ticker": "COINBASE:BTCUSDC.P", "price": 64000},
        response={"id": 1, "side": "long", "entry_price": 64000},
    )

    assert log.exists()
    assert oct(os.stat(log).st_mode & 0o777) == "0o600"
    assert event["symbol"] == "COINBASE:BTCUSDC.P"
    rows = fusion_panel.load_alert_events(str(log), limit=10)
    assert len(rows) == 1
    assert rows[0]["kind"] == "entry_opened"
    assert rows[0]["no_trade_boundary"].startswith("NO TRADES EXECUTED")


def test_alert_feed_payload_is_readonly_contract(tmp_path: Path):
    log = tmp_path / "fusion_alerts.jsonl"
    fusion_panel.append_alert_event(
        str(log),
        kind="exit_ignored",
        status="accepted",
        payload={"event": "sl_hit", "ticker": "COINBASE:BTCUSDC.P", "price": 63100},
        response={"kind": "ignored", "reason": "no open trade"},
    )

    payload = fusion_panel.alert_feed_payload(str(log), limit=5)

    assert payload["mode"] == "local-readonly-display"
    assert payload["mutation_bridge_enabled"] is False
    assert payload["secret_values_recorded"] is False
    assert payload["event_count"] == 1


def test_fusion_panel_html_renders_no_trade_boundary():
    html = fusion_panel.fusion_panel_html()
    assert "NO TRADES EXECUTED" in html
    assert "fetch('/alerts?limit=80'" in html
    assert "innerHTML" not in html
