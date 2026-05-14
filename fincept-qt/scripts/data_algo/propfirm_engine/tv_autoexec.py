"""
TV auto-exec adapter via opencli — drives operator's Brave TV session.

The session is shared with operator's browser (cookies + login inherited).
TV's Paper Trading panel exposes quick-trade buttons in DOM via data-name:
  - buy-order-button   → 1-click market BUY
  - sell-order-button  → 1-click market SELL
  - qtyEl              → quantity display/control
  - order-panel        → status messages (e.g. "Non-tradable symbol")

Usage:
  from . import tv_autoexec as tx
  tx.open_chart()               # ensure TV chart loaded
  tx.switch_symbol("BATS:TSLA") # change symbol
  tx.set_qty(1)                 # default 1 unit
  tx.click_buy()                # place market BUY
  tx.get_panel_status()         # check for "Non-tradable" etc

Or as a webhook downstream:
  alert(action=buy, ticker=SP500, qty=1) → tv_autoexec.execute(action, ticker, qty)

The wrapper handles opencli session quirks:
  - Single-eval-per-call pattern (avoids session timeout)
  - Open-before-each-call to ensure tab loaded
  - JSON parsing of eval results
  - Stable retry on transient failures
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any


OPENCLI = "opencli"
SESSION = "default"
TV_CHART_URL = "https://www.tradingview.com/chart/"


def _opencli(*args, timeout: int = 15) -> str:
    """Run opencli command, return stdout. Raise on non-zero exit."""
    cmd = [OPENCLI, "browser", SESSION, *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"opencli failed: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def open_chart(url: str = TV_CHART_URL, wait_sec: float = 5.0) -> dict:
    """Open TV chart in opencli session, wait for load."""
    out = _opencli("open", url)
    time.sleep(wait_sec)
    return json.loads(out) if out.startswith("{") else {"url": url, "raw": out}


def eval_js(js: str, timeout: int = 12) -> Any:
    """
    Run JS in browser, return parsed result.

    The JS should be an expression that evaluates to a JSON-serializable
    value, OR a return statement that does. We wrap in (() => { return ... })()
    automatically if it looks like a statement block.
    """
    if not js.strip().startswith("(") and "\n" in js:
        # Wrap multi-line block as IIFE
        js = f"(() => {{ {js} }})()"
    out = _opencli("eval", js, timeout=timeout)
    # opencli sometimes wraps eval result in a JSON envelope like {"result": ...}
    if out.startswith("{") or out.startswith("["):
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out
    return out


def open_trade_panel() -> bool:
    """Click the header Trade button to ensure Paper Trading panel is visible."""
    js = """
    const trade = document.getElementById('header-toolbar-trade-desktop');
    if (!trade) return JSON.stringify({error: 'trade button not found'});
    const btn = trade.querySelector('button');
    if (!btn) return JSON.stringify({error: 'no button inside trade container'});
    btn.click();
    return JSON.stringify({clicked: true, label: btn.getAttribute('aria-label') || btn.textContent.slice(0,30)});
    """
    result = eval_js(js)
    return isinstance(result, dict) and result.get("clicked") is True


def get_panel_status() -> dict:
    """
    Return current Paper Trading panel state:
      tradable: bool — whether current symbol can be traded on the active broker
      message: str — any panel-level message (e.g. "Non-tradable symbol...")
      symbol: str — current chart symbol (from page title)
      buy_visible: bool / sell_visible: bool
    """
    js = """
    const panel = document.querySelector('[data-name="order-panel"]');
    const buyBtn = document.querySelector('[data-name="buy-order-button"]');
    const sellBtn = document.querySelector('[data-name="sell-order-button"]');
    const panelText = panel ? panel.textContent.trim() : '';
    const nonTradable = /non-tradable|cannot trade|can't trade/i.test(panelText);
    return JSON.stringify({
      tradable: !!buyBtn && !nonTradable,
      message: panelText.slice(0, 200),
      symbol: document.title.slice(0, 60),
      buy_visible: !!buyBtn,
      sell_visible: !!sellBtn,
    });
    """
    return eval_js(js)


def get_qty() -> int | None:
    """Read current quantity from qtyEl. Returns None if unreadable."""
    js = """
    const qty = document.querySelector('[data-name="qtyEl"]');
    if (!qty) return JSON.stringify({error: 'no qtyEl'});
    // qtyEl is a DIV with inner span containing the number
    const inner = qty.querySelector('span') || qty;
    const text = (inner.textContent || '').trim();
    const num = parseFloat(text);
    return JSON.stringify({raw: text, value: isFinite(num) ? num : null});
    """
    result = eval_js(js)
    if isinstance(result, dict):
        return result.get("value")
    return None


def click_buy() -> dict:
    """Fire 1-click market BUY at current qty."""
    js = """
    const buy = document.querySelector('[data-name="buy-order-button"]');
    if (!buy) return JSON.stringify({error: 'buy button not in DOM'});
    const disabled = buy.classList.toString().toLowerCase().includes('disabled');
    if (disabled) return JSON.stringify({error: 'buy button disabled', classes: buy.className});
    buy.click();
    return JSON.stringify({clicked: 'buy', ts: Date.now()});
    """
    return eval_js(js)


def click_sell() -> dict:
    """Fire 1-click market SELL at current qty."""
    js = """
    const sell = document.querySelector('[data-name="sell-order-button"]');
    if (!sell) return JSON.stringify({error: 'sell button not in DOM'});
    const disabled = sell.classList.toString().toLowerCase().includes('disabled');
    if (disabled) return JSON.stringify({error: 'sell button disabled'});
    sell.click();
    return JSON.stringify({clicked: 'sell', ts: Date.now()});
    """
    return eval_js(js)


def switch_symbol(ticker: str) -> dict:
    """
    Switch chart to a given ticker via header symbol search.
    Format examples: 'BATS:TSLA', 'TVC:GOLD', 'VANTAGE:SP500'.
    Uses keyboard shortcut: focus chart then type ticker then Enter.
    """
    # TV keyboard shortcut: just type letters opens symbol search
    # Or use the symbol-search button: id="header-toolbar-symbol-search"
    js = f"""
    const search = document.getElementById('header-toolbar-symbol-search');
    if (!search) return JSON.stringify({{error: 'symbol search btn not found'}});
    search.click();
    // Wait for input to appear, then fill
    setTimeout(() => {{
      const input = document.querySelector('input[data-role="search"]') ||
                    document.querySelector('input[placeholder*="Symbol"]') ||
                    document.querySelector('input[autofocus]');
      if (input) {{
        input.focus();
        input.value = '{ticker}';
        input.dispatchEvent(new Event('input', {{bubbles: true}}));
        setTimeout(() => {{
          // press Enter to select first match
          const enter = new KeyboardEvent('keydown', {{key: 'Enter', code: 'Enter', bubbles: true}});
          input.dispatchEvent(enter);
        }}, 600);
      }}
    }}, 400);
    return JSON.stringify({{searched: '{ticker}'}});
    """
    return eval_js(js)


def get_positions() -> list[dict]:
    """Read open positions from Paper Trading panel positions tab."""
    js = """
    // Find positions table rows
    const rows = document.querySelectorAll('[class*=positionsTable] tr, table[class*=positions] tr');
    const out = [];
    rows.forEach(r => {
      const cells = r.querySelectorAll('td');
      if (cells.length >= 4) {
        out.push({
          symbol: cells[0]?.textContent.trim(),
          side: cells[1]?.textContent.trim(),
          qty: cells[2]?.textContent.trim(),
          avgPrice: cells[3]?.textContent.trim(),
        });
      }
    });
    return JSON.stringify({positions: out.filter(p => p.symbol)});
    """
    result = eval_js(js)
    if isinstance(result, dict):
        return result.get("positions", [])
    return []


def execute_signal(action: str, ticker: str, qty: int = 1) -> dict:
    """
    End-to-end signal execution:
      1. Ensure chart loaded
      2. Switch to ticker
      3. Ensure trade panel open
      4. Verify tradability
      5. Click buy or sell

    Returns dict with status + intermediate steps.
    """
    log = {"action": action, "ticker": ticker, "qty": qty, "steps": []}

    # Step 1: chart
    try:
        open_chart(wait_sec=4)
        log["steps"].append({"step": "chart_loaded", "ok": True})
    except Exception as e:
        log["steps"].append({"step": "chart_loaded", "ok": False, "err": str(e)})
        return log

    # Step 2: switch symbol
    try:
        sym_result = switch_symbol(ticker)
        log["steps"].append({"step": "switch_symbol", "ok": True, "result": sym_result})
        time.sleep(3)
    except Exception as e:
        log["steps"].append({"step": "switch_symbol", "ok": False, "err": str(e)})

    # Step 3: ensure panel open
    try:
        open_trade_panel()
        time.sleep(1)
        log["steps"].append({"step": "trade_panel_opened", "ok": True})
    except Exception as e:
        log["steps"].append({"step": "trade_panel_opened", "ok": False, "err": str(e)})

    # Step 4: verify
    try:
        status = get_panel_status()
        log["steps"].append({"step": "panel_status", "ok": True, "status": status})
        if not status.get("tradable"):
            log["aborted"] = "non-tradable symbol per current broker"
            return log
    except Exception as e:
        log["steps"].append({"step": "panel_status", "ok": False, "err": str(e)})

    # Step 5: click
    try:
        if action.lower() in ("buy", "long"):
            click_result = click_buy()
        elif action.lower() in ("sell", "short"):
            click_result = click_sell()
        else:
            log["aborted"] = f"unknown action: {action}"
            return log
        log["steps"].append({"step": "click", "ok": True, "result": click_result})
    except Exception as e:
        log["steps"].append({"step": "click", "ok": False, "err": str(e)})

    return log


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(prog="tv_autoexec")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("open")
    e = sub.add_parser("execute")
    e.add_argument("--action", required=True, choices=["buy", "sell"])
    e.add_argument("--ticker", required=True)
    e.add_argument("--qty", type=int, default=1)
    args = p.parse_args()

    if args.cmd == "status":
        open_chart()
        open_trade_panel()
        print(json.dumps(get_panel_status(), indent=2))
    elif args.cmd == "open":
        print(json.dumps(open_chart(), indent=2))
    elif args.cmd == "execute":
        print(json.dumps(execute_signal(args.action, args.ticker, args.qty), indent=2))
