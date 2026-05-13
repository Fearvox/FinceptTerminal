"""
Earnings Reaction Scanner — 财报股价反应方向扫描器
===================================================
Manifold 没有 EPS beat 市场，但有 "Will [TICKER] close higher on [earnings day]" 市场。
这些市场流动性通常 <M$200，且散户直觉偏向积极→历史偏差可能可利用。

思路（基于 Wolf Hour 框架适配）:
  1. 拉 NASDAQ 财报日历（未来 7 天）
  2. 对每家报公司，搜 Manifold 是否有对应 earnings day 方向市场
  3. 拉 Yahoo 历史：过去 8 季度该股票财报日 (T+1) 收涨/收跌分布
  4. 如果 Manifold 价格偏离历史基率 > 10pp → 候选
  5. 输出可 pipe 进 strategy_auditor 的命令

与 wolf_hour_scanner 共享 wolf_scanner.db::candidates 表

用法:
  python earnings_reaction_scanner.py scan --days 7
  python earnings_reaction_scanner.py history --symbol TSLA    # 看历史反应分布
  python earnings_reaction_scanner.py market --symbol TSLA --date 2026-04-23  # 找对应市场
"""

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

NASDAQ_API = "https://api.nasdaq.com/api/calendar/earnings"
YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart"
MANIFOLD_API = "https://api.manifold.markets/v0"
DB_PATH = Path(__file__).parent / "wolf_scanner.db"
UA = {"User-Agent": "Mozilla/5.0"}


def _req(url: str, timeout: int = 10):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


# ─── NASDAQ 财报日历 ──────────────────────────────────────────────────

def fetch_earnings(date_str: str) -> list[dict]:
    """date_str: YYYY-MM-DD"""
    try:
        d = _req(f"{NASDAQ_API}?date={date_str}")
        rows = d.get("data", {}).get("rows") or []
        return [{
            "symbol": r.get("symbol"),
            "name": r.get("name", ""),
            "time": r.get("time", "").replace("time-", ""),
            "eps_fcst": r.get("epsForecast", ""),
            "report_date": date_str,
        } for r in rows if r.get("symbol")]
    except Exception as e:
        print(f"⚠️ {date_str}: {e}", file=sys.stderr)
        return []


def fetch_earnings_week(start: datetime, days: int = 7) -> list[dict]:
    all_rows = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        rows = fetch_earnings(d)
        all_rows.extend(rows)
        time.sleep(0.3)
    return all_rows


# ─── Yahoo 历史股价 → 财报日反应分布 ─────────────────────────────────

def fetch_historical_reactions(symbol: str, lookback_quarters: int = 8) -> dict:
    """
    拉过去 2 年日线，识别 8 个财报日附近的 T+1 涨跌分布。
    财报日在 NASDAQ API 查不到历史，用 volume 尖峰代理：
      - 过去 2 年 volume 排名前 15 天（约对应 8 次财报）的次日涨跌
    """
    try:
        d = _req(f"{YAHOO_CHART}/{symbol}?interval=1d&range=2y")
        result = d["chart"]["result"][0]
        ts = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        volumes = result["indicators"]["quote"][0]["volume"]

        # 剔除 None
        rows = [(ts[i], closes[i], volumes[i]) for i in range(len(ts))
                if closes[i] is not None and volumes[i] is not None]

        # volume 排序，取 top-15
        vol_sorted = sorted(rows, key=lambda x: -x[2])
        top_vol = vol_sorted[:15]
        top_indices = sorted([rows.index(r) for r in top_vol])

        up_count = 0
        down_count = 0
        moves = []
        for idx in top_indices:
            if idx + 1 < len(rows):
                prev_close = rows[idx][1]
                next_close = rows[idx + 1][1]
                move = (next_close - prev_close) / prev_close
                moves.append(move)
                if move > 0:
                    up_count += 1
                else:
                    down_count += 1

        total = up_count + down_count
        if total == 0:
            return {"error": "no earnings-like events found"}

        avg_move = sum(moves) / len(moves)
        return {
            "symbol": symbol,
            "n_events": total,
            "up_rate": up_count / total,
            "down_rate": down_count / total,
            "avg_move": avg_move,
            "moves": moves,
        }
    except Exception as e:
        return {"error": str(e)}


# ─── Manifold 对应市场发现 ───────────────────────────────────────────

def find_manifold_market(symbol: str, report_date: str) -> list[dict]:
    """
    搜索 "Will [SYMBOL] close higher on [date]" 类市场。
    report_date: YYYY-MM-DD
    """
    candidates = []
    # 尝试多种查询
    queries = [
        f"{symbol} close higher",
        f"{symbol} stock {report_date[:7]}",
        f"{symbol} earnings {report_date[:4]}",
    ]
    seen = set()
    for q in queries:
        try:
            url = f"{MANIFOLD_API}/search-markets?term={urllib.parse.quote(q)}&limit=10&filter=open"
            r = _req(url, timeout=8)
            for m in r:
                slug = m.get("slug")
                if slug in seen:
                    continue
                seen.add(slug)
                q_text = m.get("question", "").upper()
                # 必须包含 ticker
                if symbol.upper() not in q_text:
                    continue
                # 倾向 binary + 有概率
                if m.get("outcomeType") != "BINARY" or m.get("probability") is None:
                    continue
                candidates.append(m)
        except Exception:
            continue
    return candidates


# ─── 信号评分 ────────────────────────────────────────────────────────

def score_candidate(symbol: str, hist: dict, mkt_prob: float, direction: str = "up") -> dict:
    """
    direction='up': 市场问「会涨吗」，对比历史 up_rate
    """
    if "error" in hist:
        return {"edge": 0, "reason": hist["error"]}

    base_rate = hist["up_rate"] if direction == "up" else hist["down_rate"]
    edge = base_rate - mkt_prob  # 正数 = 市场低估上涨概率 → 买 YES
    return {
        "symbol": symbol,
        "my_prob": base_rate,
        "market_prob": mkt_prob,
        "edge": edge,
        "n_events": hist["n_events"],
        "avg_move": hist["avg_move"],
        "reason": f"historical {direction}_rate={base_rate:.2f} over {hist['n_events']} events",
    }


# ─── 主扫描 ───────────────────────────────────────────────────────────

def scan(days: int = 7, min_edge: float = 0.05):
    now = datetime.now(timezone.utc)
    print(f"📅 拉取未来 {days} 天财报日历...")
    earnings = fetch_earnings_week(now, days)
    print(f"  共 {len(earnings)} 条\n")

    # 按 symbol 去重（一个 symbol 一天一行）
    seen = set()
    unique = []
    for e in earnings:
        key = (e["symbol"], e["report_date"])
        if key not in seen:
            seen.add(key)
            unique.append(e)

    # 优先扫高知名度 symbol（更可能有 Manifold 市场）
    major = {"TSLA","AAPL","MSFT","GOOGL","GOOG","AMZN","META","NVDA","NFLX",
             "AMD","INTC","IBM","DIS","BA","COST","WMT","JPM","BAC","GS",
             "KO","MCD","PFE","JNJ","UNH","XOM","CVX","T","VZ","V","MA",
             "PYPL","SHOP","COIN","UBER","LYFT","RBLX","SNAP","PINS","SQ"}
    unique.sort(key=lambda x: (0 if x["symbol"] in major else 1, x["report_date"]))

    candidates_found = []
    for e in unique[:80]:   # 限制扫描深度避免 rate limit
        sym = e["symbol"]
        report_date = e["report_date"]
        reaction_date = (datetime.strptime(report_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

        # Manifold 市场查找
        markets = find_manifold_market(sym, reaction_date)
        if not markets:
            continue

        # 历史反应
        hist = fetch_historical_reactions(sym)
        if "error" in hist:
            continue

        # 每个市场评分
        for m in markets:
            direction = "up" if any(kw in m.get("question", "").lower() for kw in ["higher", "up", "increase"]) else "down"
            sc = score_candidate(sym, hist, m.get("probability"), direction)
            if abs(sc["edge"]) < min_edge:
                continue

            q = m.get("question", "")[:70]
            liq = m.get("totalLiquidity", 0)
            side = "YES" if sc["edge"] > 0 else "NO"
            hint = (f"python strategy_auditor.py audit "
                    f"--market-id {m.get('slug')} --outcome {side} "
                    f"--my-prob {sc['my_prob']:.3f} --market-prob {m.get('probability'):.3f} "
                    f"--edge-source \"{sym}_earnings_hist_{hist['n_events']}q_{direction}_{sc['my_prob']:.2f}\" "
                    f"--amount 15")

            candidates_found.append({
                "symbol": sym,
                "market_slug": m.get("slug"),
                "market_q": q,
                "my_prob": sc["my_prob"],
                "mkt_prob": m.get("probability"),
                "edge": sc["edge"],
                "liq": liq,
                "hint": hint,
                "report_date": report_date,
                "eps_fcst": e["eps_fcst"],
            })

        time.sleep(0.5)  # rate limit

    # 按 |edge| × log(liq) 排序（边际 × 可交易性）
    import math
    candidates_found.sort(key=lambda x: -abs(x["edge"]) * math.log(max(x["liq"], 10)))

    print(f"🎯 候选 ({len(candidates_found)}):\n")
    print(f"  {'SYM':6} {'edge':>7} {'my':>5} {'mkt':>5} {'liq':>5} {'date':10} | 市场")
    _ensure_candidates_table()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for cand in candidates_found[:15]:
        edge_pp = cand["edge"] * 100
        print(f"  {cand['symbol']:6} {edge_pp:+6.1f}% {cand['my_prob']:.2f}  {cand['mkt_prob']:.2f}  {cand['liq']:>5.0f} {cand['report_date']} | {cand['market_q']}")
        print(f"         → {cand['hint']}")
        c.execute("INSERT INTO candidates (ts, slug, signal, severity, market_prob, liquidity, evidence, action_hint) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (datetime.now(timezone.utc).isoformat(), cand["market_slug"], "earnings_reaction",
                   min(5, int(abs(cand["edge"]) * 20)), cand["mkt_prob"], cand["liq"],
                   f"{cand['symbol']} edge={edge_pp:+.1f}pp",
                   cand["hint"]))
    conn.commit()
    conn.close()


def _ensure_candidates_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            slug TEXT NOT NULL,
            signal TEXT NOT NULL,
            severity INTEGER NOT NULL,
            market_prob REAL,
            liquidity REAL,
            evidence TEXT,
            action_hint TEXT
        );
    """)
    conn.commit()
    conn.close()


# ─── CLI ───────────────────────────────────────────────────────────

def cmd_history(symbol: str):
    h = fetch_historical_reactions(symbol)
    if "error" in h:
        print(f"❌ {h['error']}")
        return
    print(f"\n{symbol} 历史财报反应（volume 尖峰代理 T+1）")
    print(f"  样本数: {h['n_events']}")
    print(f"  上涨率: {h['up_rate']*100:.1f}%")
    print(f"  下跌率: {h['down_rate']*100:.1f}%")
    print(f"  平均幅度: {h['avg_move']*100:+.2f}%")
    print(f"  moves: {[f'{m*100:+.1f}%' for m in h['moves']]}")


def cmd_market(symbol: str, date: str):
    ms = find_manifold_market(symbol, date)
    print(f"\n{symbol} @ {date} 对应 Manifold 市场 ({len(ms)}):")
    for m in ms:
        p = m.get("probability", 0)
        liq = m.get("totalLiquidity", 0)
        q = m.get("question", "")[:80]
        print(f"  p={p:.3f} liq={liq:.0f}  {q}")
        print(f"  URL: https://manifold.markets/{m.get('creatorUsername')}/{m.get('slug')}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--min-edge", type=float, default=0.05)

    h = sub.add_parser("history")
    h.add_argument("--symbol", required=True)

    m = sub.add_parser("market")
    m.add_argument("--symbol", required=True)
    m.add_argument("--date", required=True, help="YYYY-MM-DD")

    args = p.parse_args()
    if args.cmd == "scan":
        scan(args.days, args.min_edge)
    elif args.cmd == "history":
        cmd_history(args.symbol)
    elif args.cmd == "market":
        cmd_market(args.symbol, args.date)


if __name__ == "__main__":
    main()
