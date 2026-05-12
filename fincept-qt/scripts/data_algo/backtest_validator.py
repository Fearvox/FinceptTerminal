"""
Backtest Validator — 验证 scanner 历史命中率
=====================================================
对 earnings_reaction_scanner 的逻辑做历史回测：
  1. 取过去 N 个财报日（earnings_dates 表）
  2. 模拟：若当时用我们的 base-rate model 预测
  3. 对比真实 T+1 结果
  4. 计算命中率 + Brier + 预期 vs 实际 PnL

用法:
  python backtest_validator.py earnings --symbols TSLA,AAPL,MSFT,NVDA,META,GOOGL --halflife-q 4
"""
import argparse
import json
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import math
import statistics

DB_PATH = Path(__file__).parent / "wolf_scanner.db"
UA = {"User-Agent": "Mozilla/5.0"}


def _req(url, timeout=10):
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())
    except Exception:
        return None


def wilson_ci(up, n, z=1.96):
    if n == 0:
        return (0, 0)
    p = up / n
    denom = 1 + z**2/n
    center = (p + z**2/(2*n)) / denom
    margin = z * math.sqrt(p*(1-p)/n + z**2/(4*n**2)) / denom
    return (max(0, center - margin), min(1, center + margin))


def backtest_symbol(symbol: str, halflife_q: float = 4.0):
    """
    Walk-forward backtest:
      For each historical earnings date i (sorted chronologically):
        - Use prior events 1..i-1 to compute base rate (recency-weighted)
        - Predict direction of event i
        - Compare to actual outcome
    Returns hit rate, Brier, and scenario PnL.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT report_date FROM earnings_dates WHERE symbol=? ORDER BY report_date ASC", (symbol,))
    dates = [r[0] for r in c.fetchall()]
    conn.close()

    if len(dates) < 4:
        return {"error": f"{symbol}: 历史样本不足 ({len(dates)})"}

    # 拉 Yahoo 价格
    d = _req(f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5y")
    if not d:
        return {"error": "Yahoo fail"}
    result = d["chart"]["result"][0]
    ts = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    rows = [(datetime.fromtimestamp(ts[i], timezone.utc).strftime("%Y-%m-%d"), closes[i])
            for i in range(len(ts)) if closes[i] is not None]
    date_to_close = {r[0]: r[1] for r in rows}
    sd = sorted(date_to_close.keys())

    def next_td(d):
        try:
            idx = sd.index(d)
            return sd[idx+1] if idx+1 < len(sd) else None
        except ValueError:
            after = [x for x in sd if x > d]
            return after[0] if after else None

    # 计算每个事件的 T+1 move
    events = []
    for ed in dates:
        pre = date_to_close.get(ed)
        if pre is None:
            before = [x for x in sd if x <= ed]
            if not before:
                continue
            ed_eff = before[-1]
            pre = date_to_close[ed_eff]
        else:
            ed_eff = ed
        nxt = next_td(ed_eff)
        if not nxt:
            continue
        post = date_to_close[nxt]
        mv = (post - pre) / pre
        events.append({"date": ed, "move": mv, "up": mv > 0})

    if len(events) < 4:
        return {"error": "events 不足"}

    # Walk-forward
    results = []
    decay = 0.5 ** (1/halflife_q)
    for i in range(3, len(events)):  # 至少 3 个历史样本后开始预测
        # Historical events 0..i-1
        history = events[:i]
        # 近期加权 up_rate（反向顺序，最近权重最大）
        weights = [decay**(len(history)-1-k) for k in range(len(history))]
        weighted_up = sum(w for w, e in zip(weights, history) if e["up"])
        total_w = sum(weights)
        predicted_p = weighted_up / total_w if total_w > 0 else 0.5

        # 实际结果
        actual = 1 if events[i]["up"] else 0

        # Brier for this prediction
        brier = (predicted_p - actual) ** 2

        results.append({
            "event_date": events[i]["date"],
            "predicted_p": predicted_p,
            "actual_up": actual,
            "actual_move": events[i]["move"],
            "brier": brier,
        })

    if not results:
        return {"error": "无 walk-forward 结果"}

    # 统计
    correct = sum(1 for r in results if (r["predicted_p"] > 0.5) == (r["actual_up"] == 1))
    n = len(results)
    avg_brier = statistics.mean([r["brier"] for r in results])

    # 情景 PnL：假设每次预测时押 M$10，按预测方向和 0.5 阈值
    total_pnl_if_market_at_05 = 0
    for r in results:
        if r["predicted_p"] > 0.55:  # 有 edge 才下
            bet_outcome = 1
            total_pnl_if_market_at_05 += (10 / 0.5) * (1 if r["actual_up"]==bet_outcome else 0) - 10
        elif r["predicted_p"] < 0.45:
            bet_outcome = 0
            total_pnl_if_market_at_05 += (10 / 0.5) * (1 if r["actual_up"]==bet_outcome else 0) - 10

    return {
        "symbol": symbol,
        "n": n,
        "hit_rate": correct / n,
        "avg_brier": avg_brier,
        "sample_pnl_at_market05": total_pnl_if_market_at_05,
        "last_prediction": results[-1]["predicted_p"] if results else None,
        "events": results,
    }


def run(symbols: list, halflife_q: float):
    print(f"\n🧪 Backtest Validator  —  halflife={halflife_q}Q\n")
    overall_bs = []
    overall_correct = 0
    overall_n = 0
    for sym in symbols:
        r = backtest_symbol(sym, halflife_q)
        if "error" in r:
            print(f"  ❌ {sym}: {r['error']}")
            continue
        ci_low, ci_high = wilson_ci(int(r["hit_rate"] * r["n"]), r["n"])
        print(f"  {sym}:")
        print(f"    样本: {r['n']} walk-forward 预测")
        print(f"    命中率: {r['hit_rate']*100:.1f}% (Wilson 95%: [{ci_low*100:.1f}%, {ci_high*100:.1f}%])")
        print(f"    平均 Brier: {r['avg_brier']:.4f}  (0=完美, 0.25=coinflip)")
        print(f"    假设市场 0.5，M$10 每下，累积 PnL: M${r['sample_pnl_at_market05']:+.2f}")
        print(f"    最新预测 (next earnings p_UP): {r['last_prediction']*100:.1f}%")
        print()
        overall_bs.append(r["avg_brier"])
        overall_correct += int(r["hit_rate"] * r["n"])
        overall_n += r["n"]

    if overall_n > 0:
        print(f"  ═══ 总览 ═══")
        print(f"  总样本: {overall_n}")
        print(f"  加权命中率: {overall_correct/overall_n*100:.1f}%")
        print(f"  平均 Brier: {statistics.mean(overall_bs):.4f}")
        if overall_correct/overall_n > 0.55:
            print(f"  ✅ 模型有显著 edge（>55%）")
        elif overall_correct/overall_n > 0.50:
            print(f"  ⚠️  模型接近 coinflip")
        else:
            print(f"  🔴 模型反向预测（慎用！）")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("earnings")
    e.add_argument("--symbols", required=True)
    e.add_argument("--halflife-q", type=float, default=4.0)

    args = p.parse_args()
    if args.cmd == "earnings":
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        run(syms, args.halflife_q)


if __name__ == "__main__":
    main()
