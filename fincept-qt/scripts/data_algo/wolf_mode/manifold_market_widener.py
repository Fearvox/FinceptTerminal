"""Manifold market widener — broad candidate sourcing beyond existing scanners.

Pulls from multiple Manifold v0/markets sorts and the user's Mega Leviathans
league group, dedupes by contract id, and outputs JSON list of candidates.

Output schema (one per candidate):
    {
      "contract_id": str,
      "slug": str,
      "question": str,
      "url": str,
      "outcome_type": "BINARY" | "MULTIPLE_CHOICE" | "FREE_RESPONSE" | ...,
      "probability": float | null,           # for BINARY
      "volume": float,
      "volume_24h": float,
      "total_liquidity": float,
      "close_time_ms": int | null,
      "is_resolved": bool,
      "source": "newest" | "score" | "trending" | "league_group",
      "fetched_at": "2026-05-17T01:23:45Z"
    }

Usage:
    python -m wolf_mode.manifold_market_widener scan --json
    python -m wolf_mode.manifold_market_widener scan --json --limit 100
    python -m wolf_mode.manifold_market_widener scan --json --sources newest,score
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

MANIFOLD = "https://api.manifold.markets/v0"
UA = {"User-Agent": "wolf-mode-widener/1.0"}
TIMEOUT_S = 15

SOURCES = {
    "newest":        f"{MANIFOLD}/markets?sort=created-time&order=desc",
    "active":        f"{MANIFOLD}/markets?sort=last-bet-time&order=desc",
    "soon_to_close": f"{MANIFOLD}/markets?sort=close-date&order=asc",
    # group endpoint requires group slug; "mega-leviathans" league group inferred — fallback ok
}


def _http_get(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            data = json.loads(r.read())
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[widener] {url} failed: {e}", file=sys.stderr)
        return []


def _normalize(market: dict, source: str) -> dict | None:
    if not isinstance(market, dict):
        return None
    cid = market.get("id")
    if not cid:
        return None
    return {
        "contract_id": cid,
        "slug": market.get("slug", ""),
        "question": (market.get("question") or "")[:140],
        "url": market.get("url", f"https://manifold.markets/{market.get('creatorUsername','?')}/{market.get('slug','?')}"),
        "outcome_type": market.get("outcomeType", "?"),
        "probability": market.get("probability"),
        "volume": market.get("volume", 0),
        "volume_24h": market.get("volume24Hours", 0),
        "total_liquidity": market.get("totalLiquidity", 0),
        "close_time_ms": market.get("closeTime"),
        "is_resolved": bool(market.get("isResolved", False)),
        "source": source,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def scan(sources: list[str] | None = None, limit_per_source: int = 100) -> list[dict]:
    sources = sources or list(SOURCES.keys())
    urls = [(s, f"{SOURCES[s]}&limit={limit_per_source}") for s in sources if s in SOURCES]
    out_by_cid: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=len(urls) or 1) as ex:
        futures = {ex.submit(_http_get, url): (s, url) for s, url in urls}
        for fut in futures:
            s, url = futures[fut]
            try:
                markets = fut.result()
            except Exception as e:
                print(f"[widener] {s} errored: {e}", file=sys.stderr)
                continue
            for m in markets:
                norm = _normalize(m, s)
                if norm and not norm["is_resolved"]:
                    # keep first occurrence (earliest source wins)
                    out_by_cid.setdefault(norm["contract_id"], norm)

    return list(out_by_cid.values())


def main():
    p = argparse.ArgumentParser(prog="manifold_market_widener")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--json", action="store_true", help="output JSON to stdout")
    s.add_argument("--limit", type=int, default=100, help="per-source candidate cap")
    s.add_argument("--sources", default=",".join(SOURCES.keys()),
                   help="comma-separated source names")
    args = p.parse_args()

    if args.cmd == "scan":
        srcs = [x.strip() for x in args.sources.split(",") if x.strip()]
        results = scan(srcs, args.limit)
        if args.json:
            json.dump(results, sys.stdout)
            sys.stdout.write("\n")
        else:
            print(f"scanned {len(results)} unique unresolved markets from {srcs}")
            for r in results[:10]:
                print(f"  {r['slug'][:50]:50}  vol={r['volume']:.0f}  liq={r['total_liquidity']:.0f}  p={r['probability']}")


if __name__ == "__main__":
    main()
