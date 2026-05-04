"""
Fusion Chat-facing alert feed for the propfirm webhook lane.

This module is deliberately display-only. It stores sanitized TradingView alert
facts into a local JSONL feed and renders a browser panel that polls that feed.
No broker/order APIs, no subprocesses, no dynamic code execution, no trade
suggestions.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ALERT_LOG_PATH = os.path.join(os.path.dirname(__file__), "fusion_alerts.jsonl")
MAX_BODY_BYTES = 64 * 1024
MAX_EVENTS = 200
MAX_STRING = 240
MAX_COLLECTION_ITEMS = 24
MAX_PAYLOAD_KEYS = 32

SECRET_KEY_RE = re.compile(r"(?:secret|token|password|api[_-]?key|authorization|bearer)", re.I)
SECRET_VALUE_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|xai-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|Bearer\s+[A-Za-z0-9._-]+)",
    re.I,
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_key(value: Any) -> str:
    text = CONTROL_RE.sub("", str(value or "")).strip().replace(" ", "_")
    text = re.sub(r"[^A-Za-z0-9_.:-]", "_", text)[:80]
    return text or "field"


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    text = CONTROL_RE.sub("", str(value))
    text = SECRET_VALUE_RE.sub("[redacted]", text).strip()
    if len(text) > MAX_STRING:
        return text[:MAX_STRING] + "…"
    return text


def sanitize_value(value: Any, depth: int = 0) -> Any:
    if depth > 2:
        return "[truncated]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_PAYLOAD_KEYS:
                out["_truncated_keys"] = max(0, len(value) - MAX_PAYLOAD_KEYS)
                break
            safe_key = _safe_key(key)
            out[safe_key] = "[redacted]" if SECRET_KEY_RE.search(safe_key) else sanitize_value(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        items = list(value[:MAX_COLLECTION_ITEMS])
        safe = [sanitize_value(item, depth + 1) for item in items]
        if len(value) > MAX_COLLECTION_ITEMS:
            safe.append({"_truncated_items": len(value) - MAX_COLLECTION_ITEMS})
        return safe
    return _safe_scalar(value)


def sanitize_alert_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return sanitize_value(payload) if isinstance(payload, dict) else {}


def _summary_value(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if key in payload and payload[key] is not None:
            value = _safe_scalar(payload[key])
            return str(value) if value not in (None, "") else None
    return None


def make_alert_event(
    *,
    kind: str,
    status: str,
    payload: dict[str, Any],
    response: dict[str, Any] | None = None,
    error: str | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    safe_payload = sanitize_alert_payload(payload)
    safe_response = sanitize_alert_payload(response or {})
    event = {
        "schema_version": 1,
        "ts": ts or now_utc(),
        "kind": _safe_scalar(kind),
        "status": _safe_scalar(status),
        "symbol": _summary_value(safe_payload, "ticker", "symbol") or _summary_value(safe_response, "symbol") or "—",
        "action": _summary_value(safe_payload, "action"),
        "event": _summary_value(safe_payload, "event"),
        "side": _summary_value(safe_response, "side"),
        "price": _summary_value(safe_payload, "price", "entry", "entry_price") or _summary_value(safe_response, "entry_price", "exit_price"),
        "payload": safe_payload,
        "response": safe_response,
        "error": _safe_scalar(error) if error else None,
        "no_trade_boundary": "NO TRADES EXECUTED, PLACED, OR SUGGESTED BY THIS DISPLAY FEED",
    }
    digest_input = json.dumps({k: v for k, v in event.items() if k != "id"}, sort_keys=True, ensure_ascii=False)
    event["id"] = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]
    return event


def append_alert_event(
    log_path: str,
    *,
    kind: str,
    status: str,
    payload: dict[str, Any],
    response: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    event = make_alert_event(kind=kind, status=status, payload=payload, response=response, error=error)
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return event


def load_alert_events(log_path: str, limit: int = 80) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit or 80), MAX_EVENTS))
    path = Path(log_path)
    if not path.exists():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=bounded_limit)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return list(rows)


def alert_feed_payload(log_path: str, limit: int = 80) -> dict[str, Any]:
    events = load_alert_events(log_path, limit=limit)
    return {
        "schema_version": 1,
        "generated_at_utc": now_utc(),
        "mode": "local-readonly-display",
        "mutation_bridge_enabled": False,
        "secret_values_recorded": False,
        "event_count": len(events),
        "events": events,
    }


def fusion_panel_html() -> str:
    # Inline, zero-dependency browser panel. All dynamic data is rendered through
    # textContent, never innerHTML.
    title = html.escape("Propfirm Fusion Alert Feed")
    return f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>{title}</title>
<style>
:root {{ color-scheme: dark; --bg:#07100d; --panel:#111a16; --line:#284036; --ink:#e7f5e9; --muted:#82948b; --green:#8ff0a4; --amber:#e2c56c; --rose:#ff8fa3; --blue:#89b7ff; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
* {{ box-sizing: border-box; }}
html, body {{ min-height: 100%; margin: 0; background: radial-gradient(circle at 1px 1px, rgba(143,240,164,.12) 1px, transparent 1.4px), var(--bg); background-size: 10px 10px, auto; color: var(--ink); }}
.shell {{ display: grid; gap: 14px; padding: 18px; }}
.header {{ display:flex; justify-content:space-between; gap:14px; align-items:flex-start; padding-bottom:14px; border-bottom:1px solid var(--line); }}
h1 {{ margin:0; font-size:18px; letter-spacing:-.03em; }}
.sub {{ margin-top:6px; color:var(--muted); font-size:11px; line-height:1.45; text-transform:uppercase; }}
.badge {{ border:1px solid var(--line); color:var(--green); padding:5px 8px; border-radius:999px; font-size:11px; white-space:nowrap; }}
.stats {{ display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:1px; background:var(--line); border:1px solid var(--line); }}
.stat {{ background:#0b130f; padding:10px; min-width:0; }}
.stat span {{ display:block; color:var(--muted); font-size:10px; text-transform:uppercase; }}
.stat strong {{ display:block; margin-top:5px; font-size:15px; overflow-wrap:anywhere; }}
.feed {{ display:grid; gap:10px; }}
.card {{ border:1px solid var(--line); background:rgba(17,26,22,.92); padding:12px; display:grid; gap:9px; }}
.card.accepted {{ border-left:3px solid var(--green); }}
.card.rejected {{ border-left:3px solid var(--rose); }}
.card.flag {{ border-left:3px solid var(--amber); }}
.top {{ display:flex; justify-content:space-between; gap:12px; align-items:baseline; }}
.kind {{ color:var(--green); text-transform:uppercase; font-size:12px; }}
.card.rejected .kind {{ color:var(--rose); }}
.card.flag .kind {{ color:var(--amber); }}
.time {{ color:var(--muted); font-size:10px; }}
.grid {{ display:grid; grid-template-columns:repeat(5, minmax(0,1fr)); gap:6px; }}
.field {{ border:1px solid rgba(255,255,255,.06); padding:7px; background:#08100d; min-width:0; }}
.field span {{ display:block; color:var(--muted); font-size:9px; text-transform:uppercase; }}
.field strong {{ display:block; margin-top:4px; font-size:12px; overflow-wrap:anywhere; }}
pre {{ margin:0; max-height:180px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; color:#c5d4cb; background:#080d0b; border:1px solid rgba(255,255,255,.06); padding:9px; font:inherit; font-size:10px; line-height:1.45; }}
.empty {{ border:1px dashed var(--line); color:var(--muted); padding:18px; text-align:center; }}
.footer {{ color:var(--muted); font-size:10px; line-height:1.45; border-top:1px solid var(--line); padding-top:12px; }}
@media (max-width: 860px) {{ .stats, .grid {{ grid-template-columns:repeat(2, minmax(0,1fr)); }} .header {{ display:grid; }} }}
</style>
</head>
<body>
<main class=\"shell\">
  <header class=\"header\">
    <div>
      <h1>Propfirm Fusion Alert Feed</h1>
      <div class=\"sub\">Webhook alerts → sanitized local feed → Fusion Chat tab. Display-only; not an execution agent.</div>
    </div>
    <div class=\"badge\" id=\"statusBadge\">checking</div>
  </header>
  <section class=\"stats\" aria-label=\"Alert feed stats\">
    <div class=\"stat\"><span>events</span><strong id=\"eventCount\">0</strong></div>
    <div class=\"stat\"><span>latest</span><strong id=\"latestTs\">—</strong></div>
    <div class=\"stat\"><span>mode</span><strong>read-only</strong></div>
    <div class=\"stat\"><span>boundary</span><strong>no trades</strong></div>
  </section>
  <section id=\"feed\" class=\"feed\" aria-live=\"polite\"></section>
  <footer class=\"footer\">NO TRADES EXECUTED, PLACED, OR SUGGESTED. This panel mirrors accepted/rejected webhook facts only. Keep TradingView execution and account checks manual.</footer>
</main>
<script>
const feed = document.querySelector('#feed');
const eventCount = document.querySelector('#eventCount');
const latestTs = document.querySelector('#latestTs');
const statusBadge = document.querySelector('#statusBadge');
function text(value) {{ return value === undefined || value === null || value === '' ? '—' : String(value); }}
function put(el, value) {{ el.textContent = text(value); }}
function field(label, value) {{
  const node = document.createElement('div'); node.className = 'field';
  const k = document.createElement('span'); k.textContent = label;
  const v = document.createElement('strong'); v.textContent = text(value);
  node.append(k, v); return node;
}}
function cardClass(event) {{
  const status = String(event.status || '').toLowerCase();
  if (status.includes('reject') || status.includes('error')) return 'card rejected';
  if (String(event.kind || '').includes('ignored')) return 'card flag';
  return 'card accepted';
}}
function renderEvent(event) {{
  const card = document.createElement('article'); card.className = cardClass(event);
  const top = document.createElement('div'); top.className = 'top';
  const kind = document.createElement('strong'); kind.className = 'kind'; kind.textContent = text(event.kind);
  const time = document.createElement('span'); time.className = 'time'; time.textContent = text(event.ts);
  top.append(kind, time);
  const grid = document.createElement('div'); grid.className = 'grid';
  grid.append(field('symbol', event.symbol), field('action', event.action || event.event), field('side', event.side), field('price', event.price), field('status', event.status));
  const payload = document.createElement('pre'); payload.textContent = JSON.stringify({{ payload: event.payload, response: event.response, error: event.error }}, null, 2);
  card.append(top, grid, payload);
  return card;
}}
function render(payload) {{
  const events = Array.isArray(payload.events) ? payload.events.slice().reverse() : [];
  put(eventCount, payload.event_count ?? events.length);
  put(latestTs, events[0]?.ts);
  statusBadge.textContent = 'live local feed';
  feed.replaceChildren();
  if (!events.length) {{
    const empty = document.createElement('div'); empty.className = 'empty';
    empty.textContent = 'No webhook alerts logged yet. POST TradingView JSON to /tv-signal on the webhook server.';
    feed.appendChild(empty);
    return;
  }}
  events.forEach((event) => feed.appendChild(renderEvent(event)));
}}
async function refresh() {{
  try {{
    const response = await fetch('/alerts?limit=80', {{ cache: 'no-store' }});
    if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
    render(await response.json());
  }} catch (error) {{
    statusBadge.textContent = 'feed unavailable';
    feed.replaceChildren();
    const empty = document.createElement('div'); empty.className = 'empty';
    empty.textContent = `Cannot read /alerts: ${{error.message}}`;
    feed.appendChild(empty);
  }}
}}
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>"""
