"""
Commodity Scanner — IMF PCPS 驱动的大宗商品错价探测
======================================================
思路:
  1. IMF Primary Commodity Price System (PCPS) 月度更新
  2. Yahoo Finance 实时商品期货（CL=F, GC=F, NG=F）
  3. Manifold "oil/gold by X date" 市场
  4. 如果 Manifold 价格假设 vs IMF 月度基线/当前现货偏差 > 阈值 → 候选

注意：IMF PCPS 官方 JSON 端点需要 SDMX 查询，简化用 FRED 替代（等价数据）。

信号类型:
  - spot_vs_target: 现货 $ vs Manifold 假设触发价
  - imf_forecast_dev: Manifold 隐含概率 vs IMF 月度预测的偏差
  - volatility_anchor: Manifold 错价 = |spot - strike| / 1σ_weekly

用法:
  python commodity_scanner.py snapshot                     # 当前商品快照
  python commodity_scanner.py scan                         # 扫 Manifold 错价
  python commodity_scanner.py history --symbol CL=F        # 历史波动
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
import math
import statistics

DB_PATH = Path(__file__).parent / "wolf_scanner.db"
UA = {"User-Agent": "Mozilla/5.0"}
MANIFOLD = "https://api.manifold.markets/v0"

# 商品到 Yahoo / FRED symbol
COMMODITIES = {
    "oil_wti":    {"yahoo": "CL=F",  "fred": "DCOILWTICO",  "label": "WTI 原油 ($/bbl)"},
    "oil_brent":  {"yahoo": "BZ=F",  "fred": "DCOILBRENTEU","label": "Brent 原油 ($/bbl)"},
    "natgas":     {"yahoo": "NG=F",  "fred": "DHHNGSP",     "label": "Henry Hub 天然气 ($/MMBtu)"},
    "gold":       {"yahoo": "GC=F",  "fred": "GOLDPMGBD228NLBM", "label": "黄金 ($/oz)"},
    "silver":     {"yahoo": "SI=F",  "fred": "SLVPRUSD",    "label": "白银 ($/oz)"},
    "copper":     {"yahoo": "HG=F",  "fred": "PCOPPUSDM",   "label": "铜 ($/lb)"},
}


def _req(url, timeout=10):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())


def yahoo_spot(sym):
    try:
        d = _req(f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=3mo")
        r = d["chart"]["result"][0]
        closes = [c for c in r["indicators"]["quote"][0]["close"] if c is not None]
        if not closes:
            return None
        latest = closes[-1]
        weekly_vol = statistics.stdev(closes[-20:]) / statistics.mean(closes[-20:]) if len(closes) >= 20 else 0
        return {"spot": latest, "weekly_vol_pct": weekly_vol*100, "series": closes}
    except Exception:
        return None


def snapshot():
    print(f"\n📦 Commodity Snapshot  —  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
    print(f"  {'Commodity':20} {'现货':>12} {'20d σ':>8}  {'Yahoo':8}")
    for name, meta in COMMODITIES.items():
        s = yahoo_spot(meta["yahoo"])
        if s:
            print(f"  {meta['label'][:20]:20} {s['spot']:>12.2f}  {s['weekly_vol_pct']:>6.2f}%  {meta['yahoo']:8}")
        else:
            print(f"  {meta['label'][:20]:20} {'n/a':>12}  {'n/a':>8}  {meta['yahoo']:8}")


def scan():
    """搜 Manifold 商品相关市场"""
    print(f"\n🔍 扫描 Manifold 商品市场...")
    queries = [
        ("oil", ["oil", "crude", "WTI", "Brent"]),
        ("gold", ["gold", "XAU"]),
        ("natgas", ["natural gas", "Henry Hub"]),
        ("copper", ["copper"]),
        ("silver", ["silver"]),
    ]
    seen = set()
    candidates = []
    for cat, terms in queries:
        for term in terms:
            try:
                url = f"{MANIFOLD}/search-markets?term={urllib.parse.quote(term)}&limit=10&filter=open"
                r = _req(url, timeout=8)
                for m in r:
                    slug = m.get("slug")
                    if slug in seen:
                        continue
                    seen.add(slug)
                    if m.get("outcomeType") != "BINARY":
                        continue
                    q = m.get("question","").lower()
                    if not any(t.lower() in q for t in terms):
                        continue
                    p = m.get("probability")
                    liq = m.get("totalLiquidity", 0)
                    if p is None or liq < 50:
                        continue
                    candidates.append((cat, m))
            except Exception:
                continue

    _save_candidates(candidates)

    if not candidates:
        print("  无符合候选市场")
        return

    print(f"\n  发现 {len(candidates)} 个市场\n")
    for cat, m in candidates[:20]:
        p = m.get("probability")
        liq = m.get("totalLiquidity", 0)
        q = m.get("question","")[:70]
        close_ms = m.get("closeTime", 0)
        close_str = datetime.fromtimestamp(close_ms/1000, timezone.utc).strftime("%m-%d") if close_ms else "?"
        print(f"  [{cat:8}] p={p:.3f} liq={liq:>5.0f} close={close_str}  {q}")
        print(f"           URL: https://manifold.markets/{m.get('creatorUsername')}/{m.get('slug')}")


def _save_candidates(cands):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, slug TEXT NOT NULL, signal TEXT NOT NULL,
            severity INTEGER NOT NULL, market_prob REAL, liquidity REAL,
            evidence TEXT, action_hint TEXT
        );
    """)
    now = datetime.now(timezone.utc).isoformat()
    for cat, m in cands:
        p = m.get("probability")
        liq = m.get("totalLiquidity", 0)
        slug = m.get("slug")
        hint = (f"python strategy_auditor.py audit --market-id {slug} "
                f"--outcome YES --my-prob ? --market-prob {p:.3f} "
                f"--edge-source \"commodity_{cat}_{m.get('question','')[:40]}\" --amount 10")
        c.execute("INSERT INTO candidates (ts, slug, signal, severity, market_prob, liquidity, evidence, action_hint) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (now, slug, f"commodity_{cat}", 2, p, liq,
                   f"{cat} Manifold 市场待研究", hint))
    conn.commit()
    conn.close()


def history(symbol):
    s = yahoo_spot(symbol)
    if not s:
        print(f"❌ 无 {symbol} 数据")
        return
    series = s["series"]
    returns = [(series[i]/series[i-1] - 1) for i in range(1, len(series))]
    print(f"\n  {symbol}  最近 3 个月日线")
    print(f"  当前: {s['spot']:.2f}")
    print(f"  20日 σ: {s['weekly_vol_pct']:.2f}%")
    print(f"  60日 平均回报: {statistics.mean(returns)*100:+.3f}%")
    print(f"  60日 回报 σ: {statistics.stdev(returns)*100:.2f}%")
    print(f"  60日 max drawdown: {min(returns)*100:+.2f}%")
    print(f"  60日 max upday: {max(returns)*100:+.2f}%")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("scan")
    h = sub.add_parser("history"); h.add_argument("--symbol", required=True)

    args = p.parse_args()
    if args.cmd == "snapshot":
        snapshot()
    elif args.cmd == "scan":
        scan()
    elif args.cmd == "history":
        history(args.symbol)


if __name__ == "__main__":
    main()
