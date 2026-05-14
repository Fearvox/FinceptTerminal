"""
TV auto-exec via Playwright Chromium with persistent profile.

Replaces tv_autoexec.py (opencli-based) which had session stability issues.
This launches its own Chromium browser in a sandbox profile directory,
so operator's Brave session is never touched.

First run: operator logs into TradingView in the new Chromium window ONCE.
Profile persists in PROFILE_DIR -> subsequent runs reuse the auth state.

CLI usage:
    python -m propfirm_engine.tv_playwright launch     # opens browser
    python -m propfirm_engine.tv_playwright status     # current state JSON
    python -m propfirm_engine.tv_playwright buy        # 1-click market BUY
    python -m propfirm_engine.tv_playwright sell       # 1-click market SELL
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROFILE_DIR = Path("/tmp/tv_pw_profile")
TV_CHART_URL = "https://www.tradingview.com/chart/"


def _ensure_profile():
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def open_browser_persistent(headless: bool = False):
    """
    Open Chromium with persistent profile. Returns (playwright, context, page).
    First call: profile fresh, may need login. Reuses cookies on subsequent calls.
    """
    from playwright.sync_api import sync_playwright
    _ensure_profile()
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1400, "height": 900},
        no_viewport=False,
    )
    if context.pages:
        page = context.pages[0]
    else:
        page = context.new_page()
    if "tradingview.com" not in (page.url or ""):
        page.goto(TV_CHART_URL, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(3)
    return pw, context, page


def connect_cdp(cdp_url: str = "http://127.0.0.1:9222"):
    """
    Connect to existing Chrome instance via CDP. Returns (playwright, context, page).
    Operator must launch Chrome separately with --remote-debugging-port=9222 +
    --user-data-dir=/some/path and log into TradingView there.

    Finds the TradingView tab (most-recently-used if multiple).
    """
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(cdp_url)
    # default context contains all tabs
    if not browser.contexts:
        raise RuntimeError("No contexts in Chrome CDP")
    context = browser.contexts[0]
    pages = context.pages
    # Prefer TV tabs (not extension background pages)
    tv_pages = [p for p in pages if "tradingview.com" in (p.url or "")]
    if not tv_pages:
        # Open a new tab on TV chart
        page = context.new_page()
        page.goto(TV_CHART_URL, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(3)
    else:
        page = tv_pages[0]
    return pw, context, page


def _run_js(page, js: str) -> Any:
    """Run JS in browser context, return parsed result."""
    return page.evaluate(js)


def get_status(page) -> dict:
    return _run_js(page, """() => {
        const panel = document.querySelector('[data-name="order-panel"]');
        const buy = document.querySelector('[data-name="buy-order-button"]');
        const sell = document.querySelector('[data-name="sell-order-button"]');
        const qty = document.querySelector('[data-name="qtyEl"]');
        const panelMsg = panel ? panel.textContent.trim().slice(0, 200) : '';
        const nonTradable = /non-tradable|cannot trade|can't trade/i.test(panelMsg);
        return {
            url: window.location.href,
            title: document.title.slice(0, 80),
            tradable: !!buy && !nonTradable,
            panel_msg: panelMsg,
            buy_visible: !!buy,
            sell_visible: !!sell,
            qty_visible: !!qty,
        };
    }""")


def click_paper_trading_card_if_open(page) -> dict:
    """If broker selection dialog is visible, click the Paper Trading card."""
    return _run_js(page, """() => {
        const divs = document.querySelectorAll('div');
        let card = null;
        for (const d of divs) {
            const t = (d.textContent || '').trim();
            if (t === 'Paper TradingBrokerage simulator by TradingView') {
                const r = d.getBoundingClientRect();
                if (r.width > 0 && r.width < 500) { card = d; break; }
            }
        }
        if (!card) return {found: false};
        const r = card.getBoundingClientRect();
        const cx = r.left + r.width/2, cy = r.top + r.height/2;
        for (const ev of ['mousedown', 'mouseup', 'click']) {
            card.dispatchEvent(new MouseEvent(ev, {bubbles: true, cancelable: true, clientX: cx, clientY: cy}));
        }
        return {found: true, clicked: true};
    }""")


def ensure_panel(page) -> dict:
    """Open the Trade panel if not already. Click Paper Trading card if dialog shows."""
    _run_js(page, """() => {
        const trade = document.getElementById('header-toolbar-trade-desktop');
        if (trade) {
            const btn = trade.querySelector('button');
            if (btn) btn.click();
        }
    }""")
    time.sleep(1.5)
    pt = click_paper_trading_card_if_open(page)
    time.sleep(2 if pt.get("clicked") else 0.5)
    return get_status(page)


def _ensure_market_mode(page) -> bool:
    """Click the Market tab if not already selected."""
    market_loc = page.locator('[role=tab]', has_text="Market").first
    if market_loc.count() == 0:
        return False
    if market_loc.get_attribute("aria-selected") != "true":
        market_loc.click(timeout=3_000)
        time.sleep(0.5)
    return True


def click_buy(page) -> dict:
    """Set buy side via ribbon + submit via place-and-modify-button. Market mode."""
    try:
        _ensure_market_mode(page)
    except Exception:
        pass
    # Click buy ribbon (sets side=buy in the form)
    ribbon = page.locator('[data-name="buy-order-button"]').first
    if ribbon.count() == 0:
        return {"error": "buy-order-button not in DOM"}
    try:
        ribbon.click(timeout=5_000)
        time.sleep(0.3)
    except Exception as e:
        return {"error": f"ribbon click failed: {e}"}
    # Click the actual submit button
    submit = page.locator('[data-name="place-and-modify-button"]').first
    if submit.count() == 0:
        return {"error": "place-and-modify-button not in DOM", "ribbon_clicked": True}
    try:
        submit.click(timeout=5_000)
        return {"clicked": "buy", "submitted": True}
    except Exception as e:
        return {"error": f"submit click failed: {e}", "ribbon_clicked": True}


def click_sell(page) -> dict:
    """Set sell side + submit via place-and-modify-button. Market mode."""
    try:
        _ensure_market_mode(page)
    except Exception:
        pass
    ribbon = page.locator('[data-name="sell-order-button"]').first
    if ribbon.count() == 0:
        return {"error": "sell-order-button not in DOM"}
    try:
        ribbon.click(timeout=5_000)
        time.sleep(0.3)
    except Exception as e:
        return {"error": f"ribbon click failed: {e}"}
    submit = page.locator('[data-name="place-and-modify-button"]').first
    if submit.count() == 0:
        return {"error": "place-and-modify-button not in DOM", "ribbon_clicked": True}
    try:
        submit.click(timeout=5_000)
        return {"clicked": "sell", "submitted": True}
    except Exception as e:
        return {"error": f"submit click failed: {e}", "ribbon_clicked": True}


def get_account_state(page) -> dict:
    """Extract Paper Trading account balance + positions from page text."""
    return _run_js(page, """() => {
        const text = document.body.innerText;
        const grab = (re) => { const r = text.match(re); return r ? (r[1] || r[0]) : null; };
        return {
            balance: grab(/Account balance[\\s\\n]+([0-9,]+\\.[0-9]+)/),
            equity: grab(/Equity[\\s\\n]+([0-9,]+\\.[0-9]+)/),
            realized: grab(/Realized PnL[\\s\\S]{0,80}?([-+\\u2212]?[0-9,]+\\.[0-9]+)/),
            unrealized: grab(/Unrealized PnL[\\s\\S]{0,80}?([-+\\u2212]?[0-9,]+\\.[0-9]+)/),
            has_no_positions: text.includes('no open positions'),
        };
    }""")


def _chart_symbol_matches(page, ticker: str | None) -> tuple[bool, str]:
    """Check if current chart symbol matches the alert ticker."""
    if not ticker:
        return True, "no ticker filter"
    # Title format: "SP500 7,510.40 ▲..." or "AAPL 299.00 ▲..."
    title = page.title()
    # Extract first token (symbol)
    chart_sym = title.split()[0] if title else ""
    # Match either bare symbol or exchange:symbol
    alert_bare = ticker.split(":")[-1].upper()
    chart_upper = chart_sym.upper()
    # Substring match: GOLD in TVC:GOLD or SP500 in VANTAGE:SP500
    matches = chart_upper == alert_bare or alert_bare in chart_upper or chart_upper in alert_bare
    return matches, f"chart={chart_upper} alert={alert_bare}"


def execute_signal(page, action: str, ticker: str | None = None, qty: int = 1) -> dict:
    """End-to-end: ensure panel + verify tradable + click buy/sell.

    If ticker is provided and doesn't match the current chart symbol, aborts
    to prevent firing the wrong-asset trade. Operator should set chart to the
    primary auto-trade symbol once + let webhook auto-fire only matching alerts.
    """
    log: dict[str, Any] = {"action": action, "ticker": ticker, "qty": qty, "steps": []}

    # Symbol mismatch guard
    matches, detail = _chart_symbol_matches(page, ticker)
    log["steps"].append({"step": "symbol_check", "matches": matches, "detail": detail})
    if not matches:
        log["aborted"] = f"chart-symbol-mismatch: {detail}"
        return log

    s1 = ensure_panel(page)
    log["steps"].append({"step": "ensure_panel", "status": s1})
    if not s1.get("tradable"):
        log["aborted"] = "not tradable: " + s1.get("panel_msg", "no buy-order-button found")
        return log
    if action.lower() in ("buy", "long"):
        click = click_buy(page)
    elif action.lower() in ("sell", "short"):
        click = click_sell(page)
    else:
        log["aborted"] = f"unknown action: {action}"
        return log
    log["steps"].append({"step": "click", "result": click})
    time.sleep(2)
    log["account_after"] = get_account_state(page)
    return log


def main():
    p = argparse.ArgumentParser(prog="tv_playwright")
    p.add_argument("--cdp", default="http://127.0.0.1:9222",
                   help="CDP URL of running Chrome (preferred over launching new)")
    p.add_argument("--launch", action="store_true",
                   help="Launch new persistent-profile Chromium instead of CDP connect")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("account")
    sub.add_parser("buy")
    sub.add_parser("sell")
    e = sub.add_parser("execute")
    e.add_argument("--action", required=True, choices=["buy", "sell"])
    e.add_argument("--ticker", default=None)
    e.add_argument("--qty", type=int, default=1)
    args = p.parse_args()

    if args.launch:
        pw, context, page = open_browser_persistent(headless=False)
    else:
        pw, context, page = connect_cdp(args.cdp)
    try:
        if args.cmd == "launch":
            print(json.dumps({"url": page.url, "title": page.title()[:80]}, indent=2))
            print("Browser opened. Log into TradingView if needed.", file=sys.stderr)
            print("Press Ctrl-C to close.", file=sys.stderr)
            try:
                while True: time.sleep(60)
            except KeyboardInterrupt:
                pass
        elif args.cmd == "status":
            print(json.dumps(get_status(page), indent=2))
        elif args.cmd == "account":
            print(json.dumps(get_account_state(page), indent=2))
        elif args.cmd == "buy":
            ensure_panel(page)
            print(json.dumps(click_buy(page), indent=2))
        elif args.cmd == "sell":
            ensure_panel(page)
            print(json.dumps(click_sell(page), indent=2))
        elif args.cmd == "execute":
            print(json.dumps(execute_signal(page, args.action, args.ticker, args.qty), indent=2))
    finally:
        context.close()
        pw.stop()


if __name__ == "__main__":
    main()
