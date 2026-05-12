"""
Earnings Date DB — 真实财报日数据库
========================================
用 NASDAQ API 遍历过去 8 季度（约 2 年），为 S&P100 公司建立真实财报日索引。
替代 earnings_reaction_scanner 里的 volume 尖峰代理。

产物:
  wolf_scanner.db::earnings_dates
    (symbol, report_date, time_of_day, eps_forecast, eps_actual)

用法:
  python earnings_date_db.py build --symbols TSLA,AAPL,MSFT
  python earnings_date_db.py build --sp100
  python earnings_date_db.py reactions --symbol TSLA   # 真实反应分布
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
import statistics
import math

DB_PATH = Path(__file__).parent / "wolf_scanner.db"
UA = {"User-Agent": "Mozilla/5.0"}
NASDAQ = "https://api.nasdaq.com/api/calendar/earnings"

SP100_TOP = [
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","BRK.B","UNH","XOM",
    "JNJ","JPM","V","LLY","PG","MA","AVGO","HD","CVX","MRK",
    "ABBV","KO","PEP","BAC","COST","WMT","TMO","PFE","MCD","CSCO",
    "ACN","ADBE","CRM","DHR","LIN","ABT","TXN","NKE","NEE","DIS",
    "PM","RTX","HON","QCOM","T","UPS","INTC","IBM","AMD","COP"
]


def ensure_schema():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS earnings_dates (
            symbol TEXT NOT NULL,
            report_date TEXT NOT NULL,
            time_of_day TEXT,
            eps_forecast TEXT,
            eps_actual TEXT,
            scraped_at TEXT,
            PRIMARY KEY (symbol, report_date)
        );
        """)
    conn.commit()
    conn.close()


def fetch_day(date_str):
    try:
        req = urllib.request.Request(f"{NASDAQ}?date={date_str}", headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=10).read())
        return d.get("data", {}).get("rows") or []
    except Exception:
        return []


def build(symbols: list[str], lookback_days: int = 750):
    """倒推 lookback_days 天，拉财报日历并筛符合 symbols 的条目"""
    ensure_schema()
    today = datetime.now(timezone.utc).date()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    target = set(symbols)

    collected = 0
    # 每隔 1 天拉一次（避免噪声），但 NASDAQ 工作日才有数据
    for i in range(0, lookback_days, 1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        # 跳过周末
        if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5:
            continue
        rows = fetch_day(d)
        for r in rows:
            sym = r.get("symbol")
            if sym not in target:
                continue
            c.execute("""INSERT OR REPLACE INTO earnings_dates
                        (symbol, report_date, time_of_day, eps_forecast, eps_actual, scraped_at)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (sym, d, r.get("time",""), r.get("epsForecast",""),
                      r.get("eps",""), datetime.now(timezone.utc).isoformat()))
            collected += 1
        if i % 30 == 0:
            conn.commit()
            print(f"  {d}: 累计收集 {collected} 条", file=sys.stderr)
        time.sleep(0.15)
    conn.commit()
    conn.close()
    print(f"✓ 共写入 {collected} 条财报记录")


def reactions(symbol: str, recency_halflife_quarters: float = 4.0):
    """
    基于真实财报日计算 T+1 反应分布:
      - 上涨率 / 下跌率
      - Wilson 95% CI
      - 近期加权（指数衰减，半衰期 4Q = 1 年）
      - 平均幅度 / 标准差
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT report_date FROM earnings_dates WHERE symbol=? ORDER BY report_date DESC", (symbol,))
    dates = [r[0] for r in c.fetchall()]
    conn.close()

    if not dates:
        return {"error": f"无 {symbol} 历史财报记录，先 build"}

    # 拉 Yahoo 2y 价格
    try:
        req = urllib.request.Request(f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5y", headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=15).read())
        result = d["chart"]["result"][0]
        ts = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception as e:
        return {"error": f"Yahoo: {e}"}

    rows = [(datetime.fromtimestamp(ts[i], timezone.utc).strftime("%Y-%m-%d"), closes[i])
            for i in range(len(ts)) if closes[i] is not None]
    date_to_close = {r[0]: r[1] for r in rows}
    sorted_dates = sorted(date_to_close.keys())

    def next_td(d):
        try:
            idx = sorted_dates.index(d)
            return sorted_dates[idx+1] if idx+1 < len(sorted_dates) else None
        except ValueError:
            # 找最近的下个交易日
            after = [x for x in sorted_dates if x > d]
            return after[0] if after else None

    events = []
    for ed in dates:
        # 如果是 after-hours 报，T (=ed) 的 close 是 pre-earnings；如果 pre-market，则 T-1 close 才是 pre
        # 保守：假设 after-hours，T close 是 pre，T+1 close 是 reaction
        pre_close = date_to_close.get(ed)
        if pre_close is None:
            # 用最近的上一个交易日
            before = [x for x in sorted_dates if x <= ed]
            if not before:
                continue
            ed_eff = before[-1]
            pre_close = date_to_close[ed_eff]
        else:
            ed_eff = ed
        nxt = next_td(ed_eff)
        if not nxt:
            continue
        post = date_to_close[nxt]
        mv = (post - pre_close) / pre_close
        events.append({"date": ed, "move": mv, "up": mv > 0})

    events.sort(key=lambda x: x["date"], reverse=True)
    if not events:
        return {"error": "无匹配价格数据"}

    up = sum(1 for e in events if e["up"])
    n = len(events)
    moves = [e["move"] for e in events]

    # Wilson 95% CI on up_rate
    p_hat = up / n
    z = 1.96
    denom = 1 + z**2/n
    center = (p_hat + z**2/(2*n)) / denom
    margin = z * math.sqrt(p_hat*(1-p_hat)/n + z**2/(4*n**2)) / denom
    ci_low = max(0, center - margin)
    ci_high = min(1, center + margin)

    # 近期加权：指数衰减，最新权重 1.0，每 1 季度衰减 0.5^(1/halflife)
    decay = 0.5 ** (1 / recency_halflife_quarters)
    weights = [decay**i for i in range(n)]  # events 按时间降序
    weighted_up = sum(w for w, e in zip(weights, events) if e["up"])
    weighted_sum = sum(weights)
    recency_up_rate = weighted_up / weighted_sum if weighted_sum > 0 else p_hat

    return {
        "symbol": symbol,
        "n": n,
        "up_rate": p_hat,
        "ci_95": [ci_low, ci_high],
        "recency_weighted_up_rate": recency_up_rate,
        "avg_move": statistics.mean(moves),
        "std_move": statistics.stdev(moves) if n > 1 else 0,
        "events": events[:12],  # 只返回最近 12 个
    }


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("--symbols", help="逗号分隔")
    b.add_argument("--sp100", action="store_true")
    b.add_argument("--days", type=int, default=750)

    r = sub.add_parser("reactions")
    r.add_argument("--symbol", required=True)
    r.add_argument("--halflife-q", type=float, default=4.0)

    args = p.parse_args()

    if args.cmd == "build":
        syms = SP100_TOP if args.sp100 else (args.symbols or "").split(",")
        syms = [s.strip().upper() for s in syms if s.strip()]
        if not syms:
            print("❌ 需要 --symbols 或 --sp100")
            sys.exit(1)
        print(f"🏗️  构建 {len(syms)} 个 symbol × {args.days} 天历史")
        build(syms, args.days)

    elif args.cmd == "reactions":
        res = reactions(args.symbol, args.halflife_q)
        if "error" in res:
            print(f"❌ {res['error']}")
            return
        print(f"\n{res['symbol']} 真实财报 T+1 反应")
        print(f"  样本: {res['n']}")
        print(f"  原始上涨率: {res['up_rate']*100:.1f}%")
        print(f"  Wilson 95% CI: [{res['ci_95'][0]*100:.1f}%, {res['ci_95'][1]*100:.1f}%]")
        print(f"  近期加权上涨率: {res['recency_weighted_up_rate']*100:.1f}%  (halflife={args.halflife_q}Q)")
        print(f"  平均幅度: {res['avg_move']*100:+.2f}%  std={res['std_move']*100:.2f}%")
        print(f"\n  最近 12 次事件:")
        for e in res["events"]:
            arrow = "📈" if e["up"] else "📉"
            print(f"    {e['date']}  {e['move']*100:+6.2f}%  {arrow}")


if __name__ == "__main__":
    main()
