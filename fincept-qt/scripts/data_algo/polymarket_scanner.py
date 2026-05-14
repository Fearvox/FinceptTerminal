"""
Polymarket Scanner — 读公开 Gamma API 找 BMI 驱动 edge
========================================================
纯读取，无下单。支持 paper trade 记录。

用法:
  python polymarket_scanner.py scan --category inflation     # 扫分类
  python polymarket_scanner.py search "fed rate cut"          # 关键词
  python polymarket_scanner.py market <slug>                  # 市场详情
  python polymarket_scanner.py dryrun --slug X --side NO --usd 15 --thesis "..."
  python polymarket_scanner.py positions                       # 看 paper 仓位
"""
import argparse, json, sqlite3, urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
import sys

UA = {"User-Agent": "Mozilla/5.0"}
GAMMA = "https://gamma-api.polymarket.com"
DB_PATH = Path(__file__).parent / "polymarket.db"


CATEGORY_KEYWORDS = {
    "inflation": ["inflation ", "cpi"],
    "fed": ["fed rate", "fomc", "fed cut", "fed hike"],
    "gdp": ["gdp growth", "gdp recession"],
    "fx": ["usd/", "yen ", "euro dollar", "dollar index", "currency peg"],
    "commodity-oil": ["crude", "wti", "brent", "barrel"],
    "commodity-gold": ["gold $", "gold price", "/oz"],
    "elections-nonus": ["brazil election", "germany election", "france election", "japan election"],
    "em-crisis": ["argentina", "turkey lira", "egypt", "pakistan default"],
    "crypto": ["bitcoin ", "ethereum "],
}


def _req(url, timeout=15):
    try:
        r = urllib.request.Request(url, headers=UA)
        return json.loads(urllib.request.urlopen(r, timeout=timeout).read())
    except Exception as e:
        print(f"❌ {e}", file=sys.stderr)
        return None


def ensure_schema():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL,
            question TEXT NOT NULL,
            side TEXT NOT NULL,            -- YES / NO
            entry_price REAL NOT NULL,
            usd_amount REAL NOT NULL,
            my_prob REAL NOT NULL,
            edge_pp REAL NOT NULL,
            thesis TEXT,
            bmi_reference TEXT,
            opened_ts TEXT NOT NULL,
            resolved INTEGER DEFAULT 0,
            actual_outcome TEXT,
            realized_pnl_usd REAL,
            resolved_ts TEXT
        );
    """)
    conn.commit()
    conn.close()


def fetch_all_markets():
    """分页拉所有 active markets"""
    all_m = []
    for offset in range(0, 5000, 500):
        r = _req(f"{GAMMA}/markets?limit=500&active=true&closed=false&offset={offset}")
        if not isinstance(r, list) or not r:
            break
        all_m.extend(r)
    return all_m


def _extract(m):
    """规范化 market 字段"""
    try:
        prices = json.loads(m.get("outcomePrices", "[]"))
        yes = float(prices[0]) if prices else None
    except:
        yes = None
    return {
        "slug": m.get("slug"),
        "question": m.get("question", ""),
        "yes": yes,
        "liq": float(m.get("liquidity") or 0),
        "vol": float(m.get("volume") or 0),
        "vol24h": float(m.get("volume24hr") or 0),
        "end": (m.get("endDate") or "")[:10],
        "tags": m.get("tags", []),
    }


def scan(category: str, limit: int = 10):
    keywords = CATEGORY_KEYWORDS.get(category)
    if not keywords:
        print(f"未知 category. 可选: {list(CATEGORY_KEYWORDS.keys())}")
        return

    print(f"🔍 拉所有 active markets...")
    all_m = fetch_all_markets()
    print(f"  共 {len(all_m)}")

    matches = []
    for m in all_m:
        q = (m.get("question", "") or "").lower()
        if any(kw in q for kw in keywords):
            x = _extract(m)
            if x["vol"] < 500: continue
            matches.append(x)

    matches.sort(key=lambda x: -x["liq"])
    print(f"\n🎯 [{category}] 匹配 {len(matches)} 个\n")
    for x in matches[:limit]:
        yes = f"{x['yes']*100:>5.1f}%" if x['yes'] is not None else "  ?  "
        print(f"  yes={yes} liq=${x['liq']:>8.0f} vol=${x['vol']:>10.0f} end={x['end']}  {x['question'][:75]}")
        print(f"       → {x['slug']}")


def search(term: str, limit: int = 15):
    print(f"🔍 搜索 '{term}'...")
    all_m = fetch_all_markets()
    matches = []
    lterm = term.lower()
    for m in all_m:
        q = (m.get("question", "") or "").lower()
        if lterm in q:
            x = _extract(m)
            matches.append(x)

    matches.sort(key=lambda x: -x["liq"])
    print(f"\n🎯 匹配 {len(matches)} 个\n")
    for x in matches[:limit]:
        yes = f"{x['yes']*100:>5.1f}%" if x['yes'] is not None else "  ?  "
        print(f"  yes={yes} liq=${x['liq']:>8.0f} vol=${x['vol']:>10.0f} end={x['end']}  {x['question'][:75]}")
        print(f"       → {x['slug']}")


def market_detail(slug: str):
    r = _req(f"{GAMMA}/markets?slug={urllib.parse.quote(slug)}")
    if not isinstance(r, list) or not r:
        print(f"❌ 未找到 {slug}")
        return
    m = r[0]
    x = _extract(m)
    print(f"\n📊 {m.get('question')}")
    print(f"   Slug: {slug}")
    print(f"   Description: {(m.get('description','') or '')[:400]}")
    print(f"   YES price: {x['yes']*100:.2f}% | liq: ${x['liq']:.0f} | vol: ${x['vol']:.0f}")
    print(f"   End date: {x['end']}")
    print(f"   Condition ID: {m.get('conditionId','?')}")
    print(f"   Clob token IDs: {m.get('clobTokenIds','?')}")
    print(f"   URL: https://polymarket.com/event/{slug}")


def dryrun(slug: str, side: str, usd: float, my_prob: float, thesis: str, bmi_ref: str = ""):
    ensure_schema()
    r = _req(f"{GAMMA}/markets?slug={urllib.parse.quote(slug)}")
    if not isinstance(r, list) or not r:
        print(f"❌ 未找到 {slug}")
        return
    m = r[0]
    x = _extract(m)
    side = side.upper()
    entry_price = x["yes"] if side == "YES" else (1 - x["yes"]) if x["yes"] else None
    if entry_price is None:
        print(f"❌ 无价格数据")
        return
    edge_pp = (my_prob - entry_price) * 100

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO paper_positions
                 (slug, question, side, entry_price, usd_amount, my_prob, edge_pp,
                  thesis, bmi_reference, opened_ts)
                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
              (slug, x["question"], side, entry_price, usd, my_prob, edge_pp,
               thesis, bmi_ref, datetime.now(timezone.utc).isoformat()))
    pid = c.lastrowid
    conn.commit()
    conn.close()

    shares = usd / entry_price
    payout_if_win = shares
    profit_if_win = payout_if_win - usd
    print(f"\n📝 Paper 仓位记录 #{pid}")
    print(f"   市场: {x['question']}")
    print(f"   方向: {side} @ {entry_price:.4f} = ${usd}")
    print(f"   我的 prob: {my_prob:.3f}  |  Edge: {edge_pp:+.1f}pp")
    print(f"   Shares: {shares:.2f}")
    print(f"   如赢: +${profit_if_win:.2f} ({profit_if_win/usd*100:+.1f}%)")
    print(f"   如输: -${usd}")
    print(f"   BMI ref: {bmi_ref}")


def positions():
    ensure_schema()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT id, slug, question, side, entry_price, usd_amount, my_prob,
                 edge_pp, thesis, bmi_reference, opened_ts, resolved
                 FROM paper_positions ORDER BY id DESC""")
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("  (无 paper 仓位)")
        return

    print(f"\n📝 Paper 仓位总览 ({len(rows)})\n")
    # Live price check
    for row in rows:
        pid, slug, q, side, entry, usd, my_p, edge, thesis, bmi, opened, resolved = row
        r = _req(f"{GAMMA}/markets?slug={urllib.parse.quote(slug)}")
        current_yes = None
        if isinstance(r, list) and r:
            try:
                prices = json.loads(r[0].get("outcomePrices","[]"))
                current_yes = float(prices[0]) if prices else None
            except: pass
        cur_side_price = current_yes if side == "YES" else (1 - current_yes) if current_yes else None
        mtm_pct = ((cur_side_price - entry) / entry * 100) if cur_side_price else 0
        print(f"  #{pid}  {q[:60]}")
        print(f"        {side} @ {entry:.3f} → 当前 {cur_side_price:.3f}  MTM: {mtm_pct:+.1f}%  (${usd * mtm_pct/100:+.2f})")
        print(f"        BMI: {bmi}")
        print(f"        thesis: {(thesis or '')[:80]}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--category", required=True)
    s.add_argument("--limit", type=int, default=10)

    se = sub.add_parser("search"); se.add_argument("term"); se.add_argument("--limit", type=int, default=15)
    m = sub.add_parser("market"); m.add_argument("slug")

    d = sub.add_parser("dryrun")
    d.add_argument("--slug", required=True)
    d.add_argument("--side", required=True, choices=["YES","NO","yes","no"])
    d.add_argument("--usd", type=float, required=True)
    d.add_argument("--my-prob", type=float, required=True)
    d.add_argument("--thesis", default="")
    d.add_argument("--bmi-ref", default="")

    sub.add_parser("positions")

    args = p.parse_args()
    if args.cmd == "scan": scan(args.category, args.limit)
    elif args.cmd == "search": search(args.term, args.limit)
    elif args.cmd == "market": market_detail(args.slug)
    elif args.cmd == "dryrun":
        dryrun(args.slug, args.side, args.usd, args.my_prob, args.thesis, args.bmi_ref)
    elif args.cmd == "positions": positions()


if __name__ == "__main__":
    main()
