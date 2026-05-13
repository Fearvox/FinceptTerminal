"""
Political Calendar Scanner — 政治/选举事件日历扫描
=========================================================
思路:
  1. 维护未来 90 天的全球政治事件列表（选举、公投、政策截止日）
  2. 对每个事件搜 Manifold 对应市场
  3. 识别流动性 < M$500 且价格极端（<20% 或 >80%）的市场
     - 这些很可能 stale / 未被主要交易者发现
  4. 过滤掉美国主流事件（Vegas + 专业交易员扎堆）
  5. 输出候选

为什么这个 scanner 特别重要:
  - Virginia 教训：美国主流政治市场（公投、选举）市场效率高，反共识押注高风险
  - 非美国事件 (德国、法国、日本地方选举等) 美国交易员不熟 → 更易错价

用法:
  python political_calendar_scanner.py scan
  python political_calendar_scanner.py calendar   # 只看事件列表
"""
import argparse
import json
import sqlite3
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

MANIFOLD = "https://api.manifold.markets/v0"
UA = {"User-Agent": "Mozilla/5.0"}
DB_PATH = Path(__file__).parent / "wolf_scanner.db"


# 硬编码未来 90 天重要事件（手动维护 + 自动扩充）
# 格式: (date, country, event, keyword)
POLITICAL_EVENTS = [
    ("2026-04-21", "US",       "Virginia redistricting referendum",     "virginia redistricting"),
    ("2026-04-28", "Canada",   "Alberta provincial election (hypo)",    "alberta election"),
    ("2026-05-01", "UK",       "Local elections (May Day)",             "UK local election"),
    ("2026-05-07", "UK",       "Local council elections",               "UK council"),
    ("2026-05-15", "Germany",  "Bundesrat reshuffling",                 "germany bundesrat"),
    ("2026-05-24", "Australia","Federal budget debate",                 "australia budget"),
    ("2026-06-01", "Japan",    "Fiscal year Q1 policy review",          "japan fiscal"),
    ("2026-06-10", "Brazil",   "Senate midterm primary",                "brazil election"),
    ("2026-06-15", "France",   "Legislative snap election (if called)", "france election"),
    ("2026-07-01", "India",    "Monsoon session start",                 "india parliament"),
    ("2026-07-20", "Mexico",   "State governor elections",              "mexico election"),
    # Non-election but politically sensitive:
    ("2026-04-25", "US",       "Fed FOMC minutes release",              "fed fomc"),
    ("2026-05-15", "US",       "Treasury refunding auction",            "treasury auction"),
    ("2026-06-05", "Global",   "G7 summit",                             "G7 summit"),
    ("2026-06-18", "US",       "Fed rate decision (FOMC)",              "fed rate decision"),
]


def _req(url, timeout=10):
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())
    except Exception:
        return None


def ensure_tables():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS political_events (
            event_date TEXT,
            country TEXT,
            event TEXT,
            keyword TEXT,
            PRIMARY KEY (event_date, event)
        );
    """)
    for ed, co, ev, kw in POLITICAL_EVENTS:
        c.execute("INSERT OR REPLACE INTO political_events VALUES (?, ?, ?, ?)",
                  (ed, co, ev, kw))
    conn.commit()
    conn.close()


def show_calendar(days: int = 90):
    ensure_tables()
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT event_date, country, event FROM political_events WHERE event_date >= ? AND event_date <= ? ORDER BY event_date",
              (today, cutoff))
    print(f"\n📅 政治/政策事件日历 (未来 {days} 天)")
    print(f"  {'日期':12} {'国家':10} {'事件'}")
    for ed, co, ev in c.fetchall():
        days_out = (datetime.strptime(ed, "%Y-%m-%d") - datetime.now(timezone.utc).replace(tzinfo=None)).days
        print(f"  {ed}  {co:10} +{days_out:>3}d  {ev}")
    conn.close()


def scan():
    """对每个事件搜 Manifold 市场，找低流动性错价"""
    ensure_tables()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cutoff = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%d")
    c.execute("SELECT event_date, country, event, keyword FROM political_events WHERE event_date >= ? AND event_date <= ? ORDER BY event_date",
              (today, cutoff))
    events = c.fetchall()
    conn.close()

    print(f"\n🔍 扫描 {len(events)} 个政治事件对应的 Manifold 市场...")
    candidates = []
    for ed, co, ev, kw in events:
        url = f"{MANIFOLD}/search-markets?term={urllib.parse.quote(kw)}&limit=8&filter=open"
        r = _req(url, timeout=10)
        if not r:
            continue
        for m in r:
            if m.get("outcomeType") != "BINARY":
                continue
            p = m.get("probability")
            if p is None:
                continue
            liq = m.get("totalLiquidity", 0)
            bettors = m.get("uniqueBettorCount", 0)
            # 筛选低流动性 + 极端价 + 少下注人
            is_stale = (liq < 500 and bettors < 20)
            is_extreme = (p < 0.20 or p > 0.80)
            if not (is_stale or is_extreme):
                continue
            q = m.get("question", "")
            # 过滤美国主流
            us_mainstream = any(x in q.lower() for x in ["trump","biden","harris","vance","pelosi","mcconnell","presidential"])
            if us_mainstream and co == "US" and "redistricting" not in q.lower():
                continue
            close_ms = m.get("closeTime", 0)
            days_to_close = (close_ms/1000 - time.time()) / 86400 if close_ms else 999
            if days_to_close < 0 or days_to_close > 120:
                continue
            candidates.append({
                "event": ev,
                "country": co,
                "event_date": ed,
                "market": q[:70],
                "slug": m.get("slug"),
                "prob": p,
                "liq": liq,
                "bettors": bettors,
                "days_to_close": days_to_close,
            })
        time.sleep(0.3)

    if not candidates:
        print("  无匹配候选")
        return

    # 按 (极端度 × 少人下注) 排
    candidates.sort(key=lambda x: -(abs(x["prob"] - 0.5) + 1/(x["bettors"]+1)))
    print(f"\n🎯 发现 {len(candidates)} 个候选\n")
    print(f"  {'事件':25} {'国':6} {'市场价':>5} {'液':>4} {'下注':>4} {'到期':>5}")
    for c in candidates[:15]:
        print(f"  {c['event'][:25]:25} {c['country']:6} {c['prob']*100:>4.1f}% {c['liq']:>4.0f} {c['bettors']:>4} {c['days_to_close']:>4.1f}d")
        print(f"       → {c['market']}")
        print(f"       → https://manifold.markets/-/{c['slug']}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    c = sub.add_parser("calendar")
    c.add_argument("--days", type=int, default=90)

    args = p.parse_args()
    if args.cmd == "scan":
        scan()
    elif args.cmd == "calendar":
        show_calendar(args.days)


if __name__ == "__main__":
    main()
