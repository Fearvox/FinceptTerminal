"""
Miko Deep Dive — 链上 2000+ 笔分析
=========================================
超越 500 笔 sample，拉全部 Miko 历史活动，找:
  1. 每个合约 entry timing (relative to bar start)
  2. Win/loss 分布 — 不是所有 45K 都赢，识别 lose pattern
  3. 资产轮动规律 (什么时候切 BTC → XRP → SOL)
  4. 仓位分布（small scout vs big conviction）
  5. 合约类型分布（5m vs 15m vs 1h vs 4h）
"""
import urllib.request, json, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

UA = {"User-Agent": "Mozilla/5.0"}
WALLET = "0x6fdc687773d4ba8753ea406f4eb2a403051a953f"
DB = Path(__file__).parent / "miko_trades.db"


def fetch_all_activity(max_pages=10):
    all_tx = []
    for page in range(max_pages):
        offset = page * 500
        url = f"https://data-api.polymarket.com/activity?user={WALLET}&limit=500&offset={offset}"
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15).read())
            if not r: break
            all_tx.extend(r)
            print(f"  page {page}: +{len(r)} (total {len(all_tx)})")
            if len(r) < 500: break
            time.sleep(0.3)
        except Exception as e:
            print(f"  err page {page}: {e}")
            break
    return all_tx


def ensure_schema():
    c = sqlite3.connect(DB); cur = c.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS trades (
        tx_hash TEXT, ts INTEGER, side TEXT, outcome TEXT,
        market_slug TEXT, title TEXT, price REAL, size_usd REAL, shares REAL,
        asset TEXT, bar_type TEXT, bar_start INTEGER, bar_end INTEGER,
        entry_offset_sec INTEGER, PRIMARY KEY(tx_hash, ts))""")
    c.commit(); c.close()


def parse_market(title, ts):
    """提取 asset + bar_type 从 market title"""
    t = (title or "").lower()
    asset = None
    for a in ["btc","ethereum","eth","solana","sol","xrp"]:
        if a in t:
            asset = "ETH" if a in ("ethereum","eth") else "SOL" if a in ("solana","sol") else a.upper()
            break
    # bar_type: 5m/15m/1h/4h
    import re
    m5 = re.search(r'(\d{1,2}):(\d{2})(am|pm)-(\d{1,2}):(\d{2})(am|pm)', t)
    if m5:
        h1,m1,p1,h2,m2,p2 = m5.groups()
        mins1 = (int(h1) % 12 + (12 if p1=="pm" else 0)) * 60 + int(m1)
        mins2 = (int(h2) % 12 + (12 if p2=="pm" else 0)) * 60 + int(m2)
        dur = mins2 - mins1
        if dur < 0: dur += 1440
        bar_type = "5m" if dur <= 5 else "15m" if dur <= 15 else "1h" if dur <= 60 else "4h"
        return asset, bar_type
    return asset, "unknown"


def analyze(all_tx):
    ensure_schema()
    print(f"\n🔬 分析 {len(all_tx)} 笔\n")

    # 1. 合约类型分布
    bar_types = Counter()
    assets = Counter()
    sides = Counter()
    outcomes = Counter()
    hours = Counter()
    size_usd = []
    by_asset = defaultdict(list)

    # Price 分布（入场价格）
    price_buckets = Counter()

    for tx in all_tx:
        ts = tx.get("timestamp", 0)
        title = tx.get("title", "") or tx.get("question", "")
        asset, bar = parse_market(title, ts)
        dt = datetime.fromtimestamp(ts, timezone.utc)
        side = tx.get("side", "")
        outcome = tx.get("outcome", "")
        price = float(tx.get("price", 0) or 0)
        size = float(tx.get("usdcSize", 0) or 0)

        bar_types[bar] += 1
        if asset: assets[asset] += 1
        sides[side] += 1
        outcomes[outcome] += 1
        hours[dt.hour] += 1
        if size > 0: size_usd.append(size)
        if price > 0:
            p_bucket = int(price * 10) / 10   # 0.1 buckets
            price_buckets[p_bucket] += 1
        if asset: by_asset[asset].append((ts, size, price, outcome))

    print("=== 合约类型 ===")
    for bar, n in sorted(bar_types.items(), key=lambda x: -x[1]):
        pct = 100*n/len(all_tx)
        print(f"  {bar:10} {n:>6} ({pct:>5.1f}%)")

    print("\n=== 资产分布 ===")
    for a, n in sorted(assets.items(), key=lambda x: -x[1]):
        pct = 100*n/sum(assets.values())
        bar = "█" * int(pct/2)
        print(f"  {a:6} {n:>6} ({pct:>5.1f}%) {bar}")

    print("\n=== UTC 小时分布 (top 12) ===")
    for h, n in sorted(hours.items(), key=lambda x: -x[1])[:12]:
        pct = 100*n/len(all_tx)
        bar = "█" * int(pct)
        print(f"  UTC {h:02d}:00  {n:>5} ({pct:>5.1f}%) {bar}")

    print("\n=== Outcome 分布 (YES/NO 倾向) ===")
    for o, n in sorted(outcomes.items(), key=lambda x: -x[1])[:6]:
        print(f"  {o:>5}: {n:>6} ({100*n/sum(outcomes.values()):.1f}%)")

    print("\n=== 入场价格分布 (0.1 bucket) ===")
    for p, n in sorted(price_buckets.items()):
        bar = "█" * int(n/20)
        print(f"  {p:.1f} {n:>5} {bar}")

    if size_usd:
        size_usd.sort()
        n_sizes = len(size_usd)
        print(f"\n=== 仓位规模 (n={n_sizes}) ===")
        print(f"  Mean: ${sum(size_usd)/n_sizes:.2f}")
        print(f"  Median: ${size_usd[n_sizes//2]:.2f}")
        print(f"  p25: ${size_usd[n_sizes//4]:.2f}")
        print(f"  p75: ${size_usd[int(n_sizes*0.75)]:.2f}")
        print(f"  p90: ${size_usd[int(n_sizes*0.9)]:.2f}")
        print(f"  p99: ${size_usd[int(n_sizes*0.99)]:.2f}")
        print(f"  Max: ${max(size_usd):.2f}")

    print("\n=== 资产切换规律 (最后 40 笔按时间) ===")
    sorted_tx = sorted(all_tx, key=lambda x: x.get("timestamp",0))[-40:]
    for tx in sorted_tx:
        ts = tx.get("timestamp", 0)
        dt = datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d %H:%M")
        a, bar = parse_market(tx.get("title",""), ts)
        o = tx.get("outcome","")
        s = tx.get("usdcSize",0)
        print(f"  {dt}  {(a or "?"):5} {(bar or "?"):5} {(o or "?"):3} ${float(s):.1f}")


def main():
    print(f"🐺 拉 Miko 全部活动 ({WALLET[:10]}...)")
    all_tx = fetch_all_activity(max_pages=15)
    print(f"\n✓ 总 {len(all_tx)} 笔")
    analyze(all_tx)


if __name__ == "__main__":
    main()
