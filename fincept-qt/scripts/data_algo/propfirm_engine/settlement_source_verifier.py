#!/usr/bin/env python3
"""Settlement-source verifier for prediction-market trade reviews.

Solves the recurring "Anti-LGTM blocks oil/macro candidates because we can't
reach the named settlement source live" problem (manifold-review-2026-05-15).
Provides programmatic fetchers for the 4 sources that come up most often.

Usage:
  settlement_source_verifier.py eia-brent
  settlement_source_verifier.py yahoo --symbol CL=F --days 5
  settlement_source_verifier.py yahoo --symbol BZ=F --days 7
  settlement_source_verifier.py manifold --contract-id n0h0uppcyl
  settlement_source_verifier.py marketwatch-cl   # → NEEDS_PLAYWRIGHT path note

Output: JSON to stdout. Errors → JSON with "error" key, exit 1.

Sources covered:
  • EIA daily Brent FOB (Europe Brent Spot Price)
      - URL: https://www.eia.gov/dnav/pet/hist/RBRTEd.htm
      - Cadence: weekly publication (next release printed on the page itself)
      - Parser: scrape the weekly table, return the last 4 weeks of daily values
  • Yahoo chart API (CL=F WTI, BZ=F Brent, BTC-USD, etc.)
      - URL: https://query1.finance.yahoo.com/v8/finance/chart/<symbol>
      - Cadence: realtime delayed ~15min for futures, realtime for crypto
      - Auth: none required for chart range queries
  • Manifold public market state
      - URL: https://api.manifold.markets/v0/market/<contractId>
      - Cadence: realtime
      - Auth: NONE for GETs (POST /v0/bet requires Authorization: Key ...)
  • MarketWatch CL.1 etc.
      - REQUIRES PLAYWRIGHT — WebFetch returns "blocked"
      - This module returns NEEDS_PLAYWRIGHT with a one-liner browser command
        the operator can paste; future revision could shell out to a node
        playwright runner if installed locally.

Anti-LGTM contract: if any fetcher hits a non-200 response, captcha, or empty
parse result, the JSON output sets `"verified": false` with a `failure_reason`.
Callers (Manifold reviewers, goalv3-cc dispatch) must treat verified=false as
FLAG, not PASS, regardless of how the directional case looks.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from typing import Any

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0"  # Yahoo silently rejects more specific Chrome UAs; short form works.
TIMEOUT = 20


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fail(reason: str, **extra: Any) -> dict:
    return {"verified": False, "failure_reason": reason, "fetched_at": _now_iso(), **extra}


# ── EIA daily Brent FOB ─────────────────────────────────────────────
def fetch_eia_brent() -> dict:
    """Scrape EIA Europe Brent Spot Price FOB daily values from the hist page.

    The hist page is a long static HTML page with a table per year cluster.
    We extract the last 4 weeks of daily values + the next-release date.
    """
    url = "https://www.eia.gov/dnav/pet/hist/RBRTEd.htm"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    except requests.RequestException as e:
        return _fail(f"network: {e}", source="eia_brent", url=url)
    if r.status_code != 200:
        return _fail(f"http_{r.status_code}", source="eia_brent", url=url)

    soup = BeautifulSoup(r.text, "html.parser")

    # Next release date — search in all text, not just main table
    nrd_match = re.search(r"Next Release Date:\s*(\d{1,2}/\d{1,2}/\d{4})", soup.get_text(" "))
    next_release = nrd_match.group(1) if nrd_match else None

    # EIA hist tables have rows like:
    #   <tr><td>2026 May-11 to May-15</td><td>106.11</td><td></td><td></td><td></td><td></td></tr>
    # The week label TD contains the year + day-range; subsequent 5 TDs are Mon-Fri.
    current_year = dt.date.today().year
    week_label_rx = re.compile(
        rf"^\s*{current_year}\s+([A-Z][a-z]{{2}})-\s*(\d{{1,2}})\s+to\s+([A-Z][a-z]{{2}})-\s*(\d{{1,2}})\s*$"
    )
    weeks: list[dict] = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        first_txt = tds[0].get_text(" ", strip=True)
        m = week_label_rx.match(first_txt)
        if not m:
            continue
        start_mon, start_day, end_mon, end_day = m.groups()
        # The week label cell may itself span multiple TDs depending on EIA's
        # rowspan tricks. Search remaining TDs for numeric content (1-5 days).
        numeric: list[float | None] = []
        for td in tds[1:]:
            txt = td.get_text(" ", strip=True)
            if not txt:
                numeric.append(None)
                continue
            try:
                numeric.append(float(txt.replace(",", "")))
            except ValueError:
                # Sometimes EIA pads with spacer cells with "&nbsp;" → empty after strip
                numeric.append(None)
        # Pad/truncate to exactly 5 (Mon-Fri)
        numeric = (numeric + [None] * 5)[:5]
        weeks.append({
            "week_label": f"{start_mon}-{start_day} to {end_mon}-{end_day}",
            "values_mon_to_fri": numeric,
        })
    last_n = weeks[-4:] if weeks else []

    # The most recent non-None value
    latest_value = None
    latest_date = None
    for w in reversed(last_n):
        for i, v in enumerate(reversed(w["values_mon_to_fri"])):
            if v is not None:
                # crude: assume Friday is index 4 from start; map back
                idx_from_start = len(w["values_mon_to_fri"]) - 1 - i
                # Don't try to compute the exact date — caller can read week_label
                latest_value = v
                latest_date = f"{w['week_label']} index {idx_from_start}"
                break
        if latest_value is not None:
            break

    return {
        "verified": latest_value is not None,
        "source": "eia_brent",
        "url": url,
        "fetched_at": _now_iso(),
        "next_release_date": next_release,
        "last_4_weeks": last_n,
        "latest_value_usd_per_bbl": latest_value,
        "latest_value_date_hint": latest_date,
    }


# ── Yahoo chart API ────────────────────────────────────────────────
def fetch_yahoo_chart(symbol: str, days: int = 5) -> dict:
    """Pull daily OHLC for a Yahoo Finance symbol over the last N trading days.

    Works for futures (CL=F, BZ=F, GC=F), crypto (BTC-USD), equities (^GSPC).
    """
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=days + 3)  # +3 buffer for weekends
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "interval": "1d",
    }
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=TIMEOUT)
    except requests.RequestException as e:
        return _fail(f"network: {e}", source="yahoo", symbol=symbol)
    if r.status_code != 200:
        return _fail(f"http_{r.status_code}", source="yahoo", symbol=symbol, url=r.url)
    try:
        d = r.json()
    except ValueError:
        return _fail("non_json_response", source="yahoo", symbol=symbol)
    result = (d.get("chart") or {}).get("result") or []
    if not result:
        return _fail("empty_chart_result", source="yahoo", symbol=symbol)
    r0 = result[0]
    ts = r0.get("timestamp") or []
    q = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, t in enumerate(ts):
        d_ = dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()
        rows.append({
            "date": d_,
            "open": q.get("open", [None] * len(ts))[i],
            "high": q.get("high", [None] * len(ts))[i],
            "low": q.get("low", [None] * len(ts))[i],
            "close": q.get("close", [None] * len(ts))[i],
        })
    return {
        "verified": bool(rows),
        "source": "yahoo",
        "symbol": symbol,
        "url": r.url,
        "fetched_at": _now_iso(),
        "days_returned": len(rows),
        "bars": rows,
    }


# ── Manifold public market state ───────────────────────────────────
def fetch_manifold_market(contract_id: str) -> dict:
    """GET https://api.manifold.markets/v0/market/<id> — public, no auth."""
    url = f"https://api.manifold.markets/v0/market/{contract_id}"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    except requests.RequestException as e:
        return _fail(f"network: {e}", source="manifold", contract_id=contract_id)
    if r.status_code != 200:
        return _fail(f"http_{r.status_code}", source="manifold", contract_id=contract_id)
    try:
        d = r.json()
    except ValueError:
        return _fail("non_json_response", source="manifold", contract_id=contract_id)
    close_iso = None
    if "closeTime" in d and isinstance(d["closeTime"], (int, float)):
        close_iso = dt.datetime.fromtimestamp(d["closeTime"] / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "verified": True,
        "source": "manifold",
        "url": url,
        "fetched_at": _now_iso(),
        "id": d.get("id"),
        "question": d.get("question"),
        "creator_username": d.get("creatorUsername"),
        "probability_yes": d.get("probability"),
        "total_liquidity": d.get("totalLiquidity"),
        "close_time_iso": close_iso,
        "is_resolved": d.get("isResolved"),
        "resolution": d.get("resolution"),
        "description_text": _extract_description_text(d.get("description")),
    }


def _extract_description_text(desc: Any) -> str | None:
    """Manifold's `description` can be a TipTap JSON doc or plain str."""
    if desc is None:
        return None
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict):
        chunks: list[str] = []
        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "text" and "text" in node:
                    chunks.append(node["text"])
                for k in ("content", "children"):
                    if k in node:
                        walk(node[k])
            elif isinstance(node, list):
                for n in node:
                    walk(n)
        walk(desc)
        return "\n".join(chunks)
    return None


# ── MarketWatch — playwright-required stub ─────────────────────────
def fetch_marketwatch_cl1() -> dict:
    """MarketWatch CL.1 is blocked from WebFetch; needs real browser.

    Returns a NEEDS_PLAYWRIGHT marker with a one-liner the operator can
    paste into a playwright-enabled session. Future revision: shell out to
    a local node playwright runner if `npx playwright` is available.
    """
    return _fail(
        "needs_playwright",
        source="marketwatch_cl1",
        url="https://www.marketwatch.com/investing/future/cl.1",
        recommended_action=(
            "Use playwright MCP: browser_navigate then browser_evaluate to read "
            "bg-quote.value + .kv__item nodes. CL.1 day-range is exposed at "
            "li.kv__item containing 'DAY RANGE'."
        ),
    )


# ── CLI ────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="source", required=True)

    sub.add_parser("eia-brent", help="EIA daily Brent FOB latest values + next release date")

    yh = sub.add_parser("yahoo", help="Yahoo chart API daily OHLC")
    yh.add_argument("--symbol", required=True, help="e.g. CL=F, BZ=F, BTC-USD")
    yh.add_argument("--days", type=int, default=5, help="Trading days to fetch")

    mf = sub.add_parser("manifold", help="Public Manifold market state by contractId")
    mf.add_argument("--contract-id", required=True)

    sub.add_parser("marketwatch-cl", help="MarketWatch CL.1 (needs playwright; returns marker)")

    args = ap.parse_args()

    if args.source == "eia-brent":
        out = fetch_eia_brent()
    elif args.source == "yahoo":
        out = fetch_yahoo_chart(args.symbol, args.days)
    elif args.source == "manifold":
        out = fetch_manifold_market(args.contract_id)
    elif args.source == "marketwatch-cl":
        out = fetch_marketwatch_cl1()
    else:
        ap.error(f"unknown source: {args.source}")
        return 2

    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
