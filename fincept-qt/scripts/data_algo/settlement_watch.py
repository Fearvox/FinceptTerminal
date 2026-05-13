"""
Settlement Watch: 到期前 30 分钟盯 Manifold WTI/Brent 仓位，自动算 cash-out 机会。

逻辑:
1. 每 60s 抓 Manifold 现价 + Yahoo WTI/Brent spot
2. 如果 threshold 已锁定 (WTI <98, Brent <115) 且市场价格 >95% 公允 → 提示 cash out
3. 如果 WTI spot 突然靠近结算 threshold → 警告

不自动执行交易 — 只打印信号。
"""
import urllib.request, json, time, argparse, sys

UA = {"User-Agent": "Mozilla/5.0"}

POSITIONS = [
    {"slug": "will-the-wti-crude-oil-spot-price-b", "side": "NO", "threshold": 98, "symbol": "CL=F", "size_m": 50},
    {"slug": "will-brent-crude-oil-close-above-1", "side": "NO", "threshold": 115, "symbol": "BZ=F", "size_m": 15},
    {"slug": "will-wti-crude-oil-hit-75-in-april", "side": "YES", "threshold": 75, "symbol": "CL=F", "size_m": 8, "mode": "touch"},
    {"slug": "will-the-price-of-oil-reach-150-be", "side": "NO", "threshold": 150, "symbol": "CL=F", "size_m": 25},
]


def yahoo_spot(symbol):
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=5m&range=1d"
    d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10).read())
    meta = d["chart"]["result"][0]["meta"]
    return meta["regularMarketPrice"], meta.get("regularMarketDayHigh", 0), meta.get("regularMarketDayLow", 0)


def manifold_market(slug):
    """Manifold 用 slug 直接查"""
    url = f"https://api.manifold.markets/v0/slug/{slug}"
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10).read())
    except Exception as e:
        return {"error": str(e)}


def snapshot():
    print(f"\n{'='*85}")
    print(f"  Settlement Watch — {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print(f"{'='*85}")
    for p in POSITIONS:
        spot, high, low = yahoo_spot(p["symbol"])
        m = manifold_market(p["slug"])
        if "error" in m:
            print(f"  {p['slug'][:50]:<50} ERR: {m['error']}")
            continue
        prob = m.get("probability", 0)
        liquidity = m.get("totalLiquidity", 0)

        # 判断是否锁定
        thr = p["threshold"]
        if p["side"] == "NO" and p.get("mode") != "touch":
            locked = spot < thr * 0.97  # 安全边际 3%
            fair = 0.02 if locked else max(0.05, prob)
        elif p["side"] == "YES" and p.get("mode") == "touch":
            hit = low < thr  # 已触?
            locked = hit
            fair = 0.98 if hit else prob
        else:
            locked = False; fair = prob

        status = "🟢 LOCKED" if locked else ("🟡 WATCH" if abs(spot-thr)/thr < 0.1 else "🔵 SAFE")
        cur_side_price = (1 - prob) if p["side"] == "NO" else prob

        print(f"  {p['slug'][:45]:<45} {p['side']:<3} size M${p['size_m']:<4}")
        print(f"    spot ${spot:.2f} (H ${high} / L ${low}) vs threshold ${thr} → {status}")
        print(f"    market prob YES={prob:.3f} | my side price={cur_side_price:.3f} | fair est={fair:.3f} | liq M${liquidity:.0f}")
        if locked and cur_side_price > 0.95:
            edge = cur_side_price - fair
            print(f"    ⚡ CASH OUT EDGE: sell @ {cur_side_price:.3f} now to lock +{edge*p['size_m']:.2f} vs wait resolve")


def loop(interval=60):
    try:
        while True:
            snapshot()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  interrupted")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--loop", action="store_true")
    p.add_argument("--interval", type=int, default=60)
    args = p.parse_args()
    if args.loop:
        loop(args.interval)
    else:
        snapshot()
