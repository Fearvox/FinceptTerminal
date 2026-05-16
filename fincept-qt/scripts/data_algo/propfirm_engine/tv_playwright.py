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
import os
import sys
import time
from pathlib import Path
from typing import Any

PROFILE_DIR = Path("/tmp/tv_pw_profile")
TV_CHART_URL = "https://www.tradingview.com/chart/"

# Default UA spoof: matches Brave 147 / Chrome 130 on macOS. TradingView
# rejects Playwright's bundled-Chromium UA as "unsafe browser" — using a
# real-world Brave/Chrome UA fixes login.
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# Common Brave install path on macOS. Override with TV_PW_EXECUTABLE_PATH.
DEFAULT_BRAVE_PATH = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"


def _ensure_profile():
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def open_browser_persistent(
    headless: bool = False,
    executable_path: str | None = None,
    user_agent: str | None = None,
):
    """
    Open Chromium with persistent profile. Returns (playwright, context, page).

    First call: profile fresh, may need login. Reuses cookies on subsequent calls.

    Parameters
    ----------
    headless: run without UI (only set after login persisted).
    executable_path: full path to a Chromium-compatible binary (e.g. Brave).
        Defaults to env TV_PW_EXECUTABLE_PATH, then DEFAULT_BRAVE_PATH if it
        exists, then Playwright's bundled Chromium.
    user_agent: override the browser UA. Defaults to env TV_PW_USER_AGENT,
        then DEFAULT_UA (a real Brave/Chrome UA) to bypass TV's "unsafe
        browser" gate.
    """
    from playwright.sync_api import sync_playwright
    _ensure_profile()

    # Resolve executable_path: explicit arg > env > Brave default if present > None
    if executable_path is None:
        executable_path = os.environ.get("TV_PW_EXECUTABLE_PATH")
    if executable_path is None and Path(DEFAULT_BRAVE_PATH).exists():
        executable_path = DEFAULT_BRAVE_PATH

    # Resolve UA: explicit arg > env > DEFAULT_UA
    if user_agent is None:
        user_agent = os.environ.get("TV_PW_USER_AGENT", DEFAULT_UA)

    # Anti-automation fingerprint scrubbing — Google OAuth (and others) refuse
    # browsers identifying as automation. Three layers:
    #   1. ignore_default_args removes Playwright's --enable-automation flag
    #      (the "Brave is being controlled by automated test software" banner)
    #   2. --disable-blink-features=AutomationControlled removes the
    #      AutomationControlled blink feature that exposes the webdriver flag
    #   3. add_init_script below overrides navigator.webdriver to undefined
    launch_kwargs: dict[str, Any] = {
        "user_data_dir": str(PROFILE_DIR),
        "headless": headless,
        "viewport": {"width": 1400, "height": 900},
        "no_viewport": False,
        "user_agent": user_agent,
        "ignore_default_args": ["--enable-automation"],
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if executable_path:
        launch_kwargs["executable_path"] = executable_path

    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(**launch_kwargs)

    # Layer 3: scrub navigator.webdriver on every page in this context
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
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


def _resilient_click(locator, label: str) -> dict:
    """Try normal click; if visibility check fails, retry with force=True."""
    try:
        locator.click(timeout=3_000)
        return {"clicked": label, "method": "normal"}
    except Exception:
        try:
            locator.click(timeout=3_000, force=True)
            return {"clicked": label, "method": "force"}
        except Exception as e:
            return {"error": f"{label} click failed (both modes): {str(e)[:200]}"}


def _set_side_via_form(page, side: str) -> dict:
    """Switch order form side via ribbon (best-effort, may be CSS-hidden)."""
    target_dn = "buy-order-button" if side == "buy" else "sell-order-button"
    btn = page.locator(f'[data-name="{target_dn}"]').first
    if btn.count() == 0:
        return {"error": f"{target_dn} not in DOM"}
    return _resilient_click(btn, f"{side}-ribbon")


def _set_qty_in_form(page, qty: int) -> dict:
    """Find the qty input (top text input in order panel) and set value."""
    js = """(qty) => {
        const inputs = Array.from(document.querySelectorAll('input[type=text]')).filter(e => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.y < 350;
        });
        if (inputs.length === 0) return {error: 'no qty input found'};
        const input = inputs[0];
        const oldValue = input.value;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(input, String(qty));
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        return {oldValue, newValue: input.value};
    }"""
    return page.evaluate(js, qty)


def _set_sl_in_form(page, sl_price: float) -> dict:
    """Toggle SL on (if needed) + set SL price. Robust to switch state via retry."""
    # Step 1: find SL switch (2nd input[role=switch], y > 440)
    switches = page.locator('input[role="switch"]')
    if switches.count() < 2:
        return {"error": "fewer than 2 switches found"}
    sl_switch = switches.nth(1)
    # Force click to toggle on (idempotent: if already on, this could turn off — but then we click again to ensure on)
    # Better: check state via JS, only click if off
    try:
        sl_state = page.evaluate("""() => {
            const s = Array.from(document.querySelectorAll('input[role=switch]'));
            if (s.length < 2) return null;
            return s[1].checked;
        }""")
        if sl_state is False:
            sl_switch.click(timeout=2_000, force=True)
            time.sleep(0.8)  # let React update
    except Exception as e:
        return {"error": f"sl toggle failed: {e}"}
    # Step 2: find SL price input (now editable)
    # Try up to 3 times with wait
    for attempt in range(3):
        result = page.evaluate("""(slPrice) => {
            // SL price input: !readOnly text input below SL switch (y > 460)
            const inputs = Array.from(document.querySelectorAll('input[type=text]')).filter(e => {
                const r = e.getBoundingClientRect();
                return r.width > 0 && !e.readOnly && r.y > 460;
            });
            if (inputs.length === 0) return {error: 'no SL price input editable'};
            const input = inputs.sort((a, b) => a.getBoundingClientRect().y - b.getBoundingClientRect().y)[0];
            const before = input.value;
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(input, String(slPrice));
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
            return {ok: true, before, after: input.value, attempt};
        }""", sl_price)
        if "ok" in result:
            return result
        time.sleep(0.5)
    return {"error": "no SL input after 3 retries"}


def _click_submit_once(page, side: str, sl: float | None = None) -> dict:
    """Single submit. Optionally set SL price via bracket toggle before submit."""
    try:
        _ensure_market_mode(page)
    except Exception:
        pass
    _set_side_via_form(page, side)  # best-effort, may error on hidden ribbon
    sl_result = None
    if sl is not None and sl > 0:
        sl_result = _set_sl_in_form(page, sl)
        time.sleep(0.3)
    submit = page.locator('[data-name="place-and-modify-button"]').first
    if submit.count() == 0:
        return {"error": "place-and-modify-button not in DOM"}
    submit_text = submit.text_content() or ""
    r = _resilient_click(submit, f"{side}-submit")
    if "error" in r:
        return {"error": r["error"], "submit_text": submit_text[:80], "sl_result": sl_result}
    return {"clicked": side, "submitted": True, "submit_text": submit_text[:80], "sl_result": sl_result}


def click_buy(page, qty: int = 1, sl: float | None = None) -> dict:
    """Place N market BUY orders. Optionally set bracket SL price on the first fill."""
    results = []
    for i in range(qty):
        r = _click_submit_once(page, "buy", sl=sl if i == 0 else None)
        results.append(r)
        if "error" in r:
            return {"error": r["error"], "filled": i, "results": results}
        if i < qty - 1:
            time.sleep(0.4)
    return {"clicked": "buy", "qty_requested": qty, "qty_submitted": qty,
            "submitted": True, "submit_text": results[-1].get("submit_text", "") if results else "",
            "sl_result": results[0].get("sl_result") if results else None}


def click_sell(page, qty: int = 1, sl: float | None = None) -> dict:
    """Place N market SELL orders. Optionally set bracket SL price."""
    results = []
    for i in range(qty):
        r = _click_submit_once(page, "sell", sl=sl if i == 0 else None)
        results.append(r)
        if "error" in r:
            return {"error": r["error"], "filled": i, "results": results}
        if i < qty - 1:
            time.sleep(0.4)
    return {"clicked": "sell", "qty_requested": qty, "qty_submitted": qty,
            "submitted": True, "submit_text": results[-1].get("submit_text", "") if results else "",
            "sl_result": results[0].get("sl_result") if results else None}


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


# ---------------------------------------------------------------------------
# Ops API — for autonomous loop/agent use (no human in browser)
# ---------------------------------------------------------------------------

def eval_js(page, js: str, arg: Any = None) -> Any:
    """Generic JS evaluation in page context. Returns whatever JS returns.

    Used by daemon /eval endpoint as escape hatch when typed endpoints don't
    cover what an agent needs. JS string can use `arg` to receive the
    `arg` parameter (Playwright passes it as the JS function's first param).
    """
    if arg is not None:
        return page.evaluate(js, arg)
    return page.evaluate(js)


def list_positions(page) -> dict:
    """Scrape Positions tab in Paper Trading panel.

    TV renders positions in a table under Paper Trading widget. Selectors are
    best-effort; falls back to raw row text if structured cells aren't found.
    """
    return page.evaluate("""() => {
        // Click on Positions tab first if not active
        const tabs = Array.from(document.querySelectorAll('[role="tab"], [data-name*="tab"]'));
        const posTab = tabs.find(t => /^positions?$/i.test((t.innerText || '').trim()));
        if (posTab && posTab.getAttribute('aria-selected') !== 'true') {
            try { posTab.click(); } catch (e) {}
        }
        // Find rows under positions section
        const rowSel = [
            '[data-name="paper-trading-positions"] [role="row"]',
            '[class*="positions"] [role="row"]',
            '[class*="positionRow"]',
        ].join(',');
        const rows = Array.from(document.querySelectorAll(rowSel));
        const headerKeywords = /symbol|side|qty|price|profit|loss|stop|target/i;
        const positions = [];
        for (const r of rows) {
            const text = (r.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!text || headerKeywords.test(text.split(' ').slice(0, 3).join(' '))) continue;
            const cells = Array.from(r.querySelectorAll('[role="cell"], td, [class*="cell"]'))
                .map(c => (c.innerText || '').replace(/\\s+/g, ' ').trim())
                .filter(s => s.length);
            positions.push({ text: text.slice(0, 400), cells: cells.slice(0, 12) });
        }
        // Also dump full text near "Positions" word for debugging
        const text = document.body ? document.body.innerText : '';
        const idx = text.indexOf('Positions');
        const snippet = idx >= 0 ? text.slice(idx, idx + 800) : '';
        return { count: positions.length, positions, panel_snippet: snippet };
    }""")


def close_all_positions(page) -> dict:
    """Click every 'close position' button visible in Paper Trading panel."""
    return page.evaluate("""() => {
        const candidates = Array.from(document.querySelectorAll(
            '[aria-label*="Close position" i], ' +
            '[data-name="close-position"], ' +
            'button[title*="Close" i]'
        ));
        let clicked = 0;
        const labels = [];
        for (const b of candidates) {
            try {
                labels.push(b.getAttribute('aria-label') || b.getAttribute('title') || b.getAttribute('data-name') || '(unlabeled)');
                b.click();
                clicked++;
            } catch (e) {}
        }
        return { found: candidates.length, clicked, labels: labels.slice(0, 10) };
    }""")


def switch_symbol(page, symbol: str) -> dict:
    """Open symbol search and load a new symbol on the same chart.

    Tries multiple paths (keyboard shortcut, click symbol button) for robustness.
    Returns title after switch — caller verifies match.
    """
    import time as _t
    before_title = ""
    try:
        before_title = page.title()
    except Exception:
        pass

    # Approach 1: keyboard shortcut. TV opens symbol search on "/" key.
    try:
        page.click('body', position={"x": 700, "y": 400}, timeout=1500)
        page.keyboard.press("/")
        page.wait_for_selector(
            'input[data-name="symbol-search-items-dialog__input"], input[role="combobox"]',
            timeout=3000,
        )
    except Exception:
        # Approach 2: click symbol button at top-left
        try:
            page.locator('[id="header-toolbar-symbol-search"]').first.click(timeout=2000)
        except Exception:
            return {"error": "could not open symbol search dialog", "before": before_title}

    try:
        box = page.locator(
            'input[data-name="symbol-search-items-dialog__input"], input[role="combobox"]'
        ).first
        box.fill(symbol)
        _t.sleep(0.4)
        page.keyboard.press("Enter")
        _t.sleep(1.8)
    except Exception as e:
        return {"error": f"fill/enter failed: {e}", "before": before_title}

    after_title = ""
    try:
        after_title = page.title()
    except Exception:
        pass
    matched = symbol.split(":")[-1].upper() in after_title.upper()
    return {"requested": symbol, "before": before_title, "after": after_title, "matched": matched}


def set_position_sl_via_form(page, sl_price: float) -> dict:
    """Best-effort SL setter using the entry form's SL widget.

    This is the form-side widget (before submit). To modify SL on an existing
    open position, use modify_position_sl (clicks pencil in Positions tab).
    """
    return _set_sl_in_form(page, sl_price)


def modify_position_sl(page, sl_price: float, position_index: int = 0) -> dict:
    """Modify SL on an open position by index (0 = first/only position).

    TV exposes a 'Stop loss' editable price chip inline in each position row.
    JS approach: find all rows under Positions, pick by index, then find an
    input matching the row's SL cell and overwrite value.
    """
    return page.evaluate(
        """([slPrice, idx]) => {
            // Find positions rows
            const rows = Array.from(document.querySelectorAll(
                '[class*="positionRow"], [data-name*="position"] [role="row"]'
            )).filter(r => (r.innerText || '').includes('SOLUSDC') || (r.innerText || '').includes('BTCUSDC') || (r.innerText || '').includes('USDC'));
            if (!rows.length) return { error: 'no position rows', tried_selectors: 3 };
            const row = rows[idx] || rows[0];
            // Find SL chip — typically a button or input with 'sl' / 'stop' label
            const slBtn = row.querySelector('[aria-label*="stop" i], [data-name*="stop"]');
            if (!slBtn) return { error: 'no SL chip on row', row_text: row.innerText.slice(0,200) };
            try { slBtn.click(); } catch(e) {}
            // After click, a price input may pop up
            const input = row.querySelector('input[type="text"]') || document.querySelector('input[type="text"]:not([readonly])');
            if (!input) return { error: 'no SL input after click', row_text: row.innerText.slice(0,200) };
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(input, String(slPrice));
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
            return { ok: true, set_to: slPrice, row_text: row.innerText.slice(0,200) };
        }""",
        [sl_price, position_index],
    )


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


def submit_one_order(page, side: str, units: int, sl: float | None = None) -> dict:
    """Set qty to N units and submit one market order (single click, not N clicks).

    Use this when an agent wants programmatic sizing without 'click N times'
    overhead. Returns the click result + the qty that was actually set.
    """
    side = side.lower()
    if side not in ("buy", "sell", "long", "short"):
        return {"error": f"bad side: {side}"}
    side_n = "buy" if side in ("buy", "long") else "sell"
    qty_set = _set_qty_in_form(page, units)
    time.sleep(0.4)
    if "error" in qty_set:
        return {"error": f"qty_set failed: {qty_set['error']}", "qty_attempted": units}
    click = _click_submit_once(page, side_n, sl=sl)
    return {"side": side_n, "units": units, "qty_set": qty_set, "click": click}


def execute_signal(page, action: str, ticker: str | None = None, qty: int = 1, sl: float | None = None) -> dict:
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
        click = click_buy(page, qty=qty, sl=sl)
    elif action.lower() in ("sell", "short"):
        click = click_sell(page, qty=qty, sl=sl)
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
