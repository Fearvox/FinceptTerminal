"""
Regime-Switching Dual Engine: S1 Trend EMA + S6 MTF Combo
===========================================================
根据 ADX + BB width 自动切换趋势/震荡策略。
  - 趋势市 (ADX > 25):   S1 Trend EMA (EMA 20/50 cross)
  - 震荡市 (ADX < 20):   S6 MTF Combo (HTF bias + LTF entry)
  - 过渡 (20 ≤ ADX ≤ 25): 空仓

同时作为跨资产 sanity check 工具: BTC / ETH / SPY / GOLD / OIL / EURUSD。

用法:
  python regime_dual_engine.py                    # 默认全资产矩阵
  python regime_dual_engine.py --symbol BTCUSDT --interval 1h
  python regime_dual_engine.py --compare          # 对比单策略 vs 双引擎
"""
import argparse, json, urllib.request, math, statistics
import sys, os

# 复用 bake_off 的 indicators / fetcher
sys.path.insert(0, os.path.dirname(__file__))
from strategy_bake_off import (
    fetch_binance, ema, sma, stdev, rsi, true_range, atr,
    s1_trend_ema, s6_mtf_combo, run_strategy,
)

UA = {"User-Agent": "Mozilla/5.0"}


# ─── Regime Detector ────────────────────────────────────────

def adx(bars, period=14):
    """Welles Wilder ADX: 趋势强度 0-100, >25 = 强趋势, <20 = 震荡"""
    if len(bars) < period * 2:
        return [0.0] * len(bars)
    plus_dm, minus_dm, tr_list = [0.0], [0.0], [0.0]
    for i in range(1, len(bars)):
        up = bars[i]["high"] - bars[i-1]["high"]
        down = bars[i-1]["low"] - bars[i]["low"]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr_list.append(max(
            bars[i]["high"] - bars[i]["low"],
            abs(bars[i]["high"] - bars[i-1]["close"]),
            abs(bars[i]["low"] - bars[i-1]["close"]),
        ))
    # Wilder smoothing
    def wilder(series, p):
        out = [0.0] * len(series)
        if len(series) < p:
            return out
        out[p-1] = sum(series[:p])
        for i in range(p, len(series)):
            out[i] = out[i-1] - out[i-1]/p + series[i]
        return out
    atr_w = wilder(tr_list, period)
    plus_w = wilder(plus_dm, period)
    minus_w = wilder(minus_dm, period)
    dx = [0.0] * len(bars)
    for i in range(period, len(bars)):
        if atr_w[i] == 0:
            continue
        pdi = 100 * plus_w[i] / atr_w[i]
        mdi = 100 * minus_w[i] / atr_w[i]
        if pdi + mdi == 0:
            continue
        dx[i] = 100 * abs(pdi - mdi) / (pdi + mdi)
    # ADX = Wilder smooth of DX
    adx_out = [0.0] * len(bars)
    if len(bars) >= 2 * period:
        adx_out[2*period-1] = sum(dx[period:2*period]) / period
        for i in range(2*period, len(bars)):
            adx_out[i] = (adx_out[i-1] * (period-1) + dx[i]) / period
    return adx_out


def bb_width(closes, period=20):
    """Bollinger Band width %: (upper - lower) / middle, 压缩 = 震荡酝酿"""
    m = sma(closes, period)
    d = stdev(closes, period)
    return [(4 * d[i] / m[i] * 100) if m[i] > 0 else 0 for i in range(len(closes))]


def classify_regime(bars, i, adx_series, bbw_series, bbw_lookback=100):
    """返回: 'trend' / 'range' / 'transition'"""
    if i < 30 or i >= len(adx_series):
        return "transition"
    a = adx_series[i]
    bbw = bbw_series[i]
    # BB width 相对历史分位
    hist = bbw_series[max(0, i-bbw_lookback):i]
    if not hist:
        return "transition"
    bbw_pct = sum(1 for x in hist if x < bbw) / len(hist)

    if a > 25:
        return "trend"
    if a < 20 and bbw_pct < 0.4:  # 低 ADX + 窄带
        return "range"
    return "transition"


# ─── Dual Engine ────────────────────────────────────────────

def run_dual_engine(bars, sl_trend=0.02, tp_trend=0.04, sl_range=0.015, tp_range=0.03,
                    transition_mode="s6"):
    """
    趋势市跑 S1, 震荡市跑 S6。
    transition_mode:
      - "s6": transition 默认也跑 S6（v2 — 承认 S6 更 universal）
      - "flat": transition 空仓（v1）
    """
    closes = [b["close"] for b in bars]
    adx_series = adx(bars, 14)
    bbw_series = bb_width(closes, 20)

    pos = 0
    entry_price = 0
    active_strategy = None  # 'S1' or 'S6'
    trades = []
    regime_log = {"trend": 0, "range": 0, "transition": 0}

    for i in range(50, len(bars)-1):
        bar = bars[i]
        regime = classify_regime(bars, i, adx_series, bbw_series)
        regime_log[regime] += 1

        # 若换 regime 且有仓位, 平仓
        need_close = False
        if pos != 0:
            if regime == "range" and active_strategy == "S1":
                need_close = True
            elif regime == "trend" and active_strategy == "S6":
                need_close = True
            elif regime == "transition" and transition_mode == "flat":
                need_close = True
        if need_close:
            pnl_pct = (bar["close"]/entry_price - 1)*100 if pos == 1 else (1 - bar["close"]/entry_price)*100
            trades.append({"side": "L" if pos==1 else "S", "strategy": active_strategy,
                          "entry": entry_price, "exit": bar["close"], "pnl_pct": pnl_pct, "reason": "regime_switch"})
            pos = 0
            active_strategy = None

        # 生成信号
        signal = None
        if regime == "trend":
            signal = s1_trend_ema(bars, i, {})
            if signal in ("long", "short") and pos == 0:
                active_strategy = "S1"
        elif regime == "range" or (regime == "transition" and transition_mode == "s6"):
            signal = s6_mtf_combo(bars, i, {})
            if signal in ("long", "short") and pos == 0:
                active_strategy = "S6"

        # SL/TP 参数按当前策略
        sl = sl_trend if active_strategy == "S1" else sl_range
        tp = tp_trend if active_strategy == "S1" else tp_range

        # 执行
        if pos == 0 and signal == "long":
            pos = 1; entry_price = bar["close"]
        elif pos == 0 and signal == "short":
            pos = -1; entry_price = bar["close"]
        elif pos == 1:
            if bar["low"] <= entry_price*(1-sl):
                trades.append({"side":"L","strategy":active_strategy,"entry":entry_price,
                              "exit":entry_price*(1-sl),"pnl_pct":-sl*100,"reason":"SL"})
                pos = 0; active_strategy = None
            elif bar["high"] >= entry_price*(1+tp):
                trades.append({"side":"L","strategy":active_strategy,"entry":entry_price,
                              "exit":entry_price*(1+tp),"pnl_pct":tp*100,"reason":"TP"})
                pos = 0; active_strategy = None
        elif pos == -1:
            if bar["high"] >= entry_price*(1+sl):
                trades.append({"side":"S","strategy":active_strategy,"entry":entry_price,
                              "exit":entry_price*(1+sl),"pnl_pct":-sl*100,"reason":"SL"})
                pos = 0; active_strategy = None
            elif bar["low"] <= entry_price*(1-tp):
                trades.append({"side":"S","strategy":active_strategy,"entry":entry_price,
                              "exit":entry_price*(1-tp),"pnl_pct":tp*100,"reason":"TP"})
                pos = 0; active_strategy = None

    if not trades:
        return {"name": "Dual_Engine", "trades": 0, "regime_log": regime_log}, regime_log

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    total = sum(t["pnl_pct"] for t in trades)
    pnl_series = [t["pnl_pct"] for t in trades]
    sharpe = (statistics.mean(pnl_series) / statistics.stdev(pnl_series)) * math.sqrt(252) if len(pnl_series) > 1 and statistics.stdev(pnl_series) > 0 else 0

    s1_trades = [t for t in trades if t.get("strategy") == "S1"]
    s6_trades = [t for t in trades if t.get("strategy") == "S6"]

    cum = 0; peak = 0; max_dd = 0
    for t in trades:
        cum += t["pnl_pct"]; peak = max(peak, cum); max_dd = max(max_dd, peak - cum)

    return {
        "name": "Dual_Engine",
        "trades": len(trades),
        "win_rate": wins/len(trades)*100,
        "sharpe": sharpe,
        "total_pnl": total,
        "max_dd": max_dd,
        "s1_count": len(s1_trades),
        "s6_count": len(s6_trades),
        "s1_pnl": sum(t["pnl_pct"] for t in s1_trades),
        "s6_pnl": sum(t["pnl_pct"] for t in s6_trades),
        "regime_log": regime_log,
    }, regime_log


# ─── Cross-Asset Matrix ─────────────────────────────────────

SCENARIOS = [
    # (symbol, interval, bars, 类别)
    ("BTCUSDT", "1h", 1500, "Crypto"),
    ("ETHUSDT", "1h", 1500, "Crypto"),
    ("SPY", "1h", 1500, "Equity"),
    ("QQQ", "1h", 1500, "Equity"),
    ("GC=F", "1h", 1500, "Commodity"),  # Gold futures
    ("CL=F", "1h", 1500, "Commodity"),  # Crude oil
    ("EURUSD=X", "1h", 1500, "FX"),
    ("BTCUSDT", "15m", 2000, "Crypto"),
]


def fetch_any(symbol, interval, bars):
    """Yahoo-first for non-Binance symbols"""
    if "=" in symbol:
        # Yahoo direct
        iv_map = {"1m":"1m","5m":"5m","15m":"15m","30m":"30m","1h":"1h","4h":"4h","1d":"1d"}
        rng_map = {"1m":"7d","5m":"60d","15m":"60d","30m":"60d","1h":"730d","4h":"730d","1d":"5y"}
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval={iv_map.get(interval,'1h')}&range={rng_map.get(interval,'730d')}"
        d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15).read())
        result = d["chart"]["result"][0]
        ts = result["timestamp"]
        q = result["indicators"]["quote"][0]
        out = []
        for i in range(len(ts)):
            if q["close"][i] is None: continue
            out.append({"ts": ts[i], "open": q["open"][i] or q["close"][i],
                        "high": q["high"][i] or q["close"][i], "low": q["low"][i] or q["close"][i],
                        "close": q["close"][i], "volume": q["volume"][i] or 0})
        return out[-bars:]
    return fetch_binance(symbol, interval, bars)


def compare_mode():
    """对比 S1-only / S6-only / Dual-Engine 在所有场景"""
    print(f"{'='*110}")
    print(f"  Strategy Comparison Matrix (S1 vs S6 vs Dual-Engine)")
    print(f"{'='*110}")
    header = f"  {'Scenario':<20} {'S1 PnL':>9} {'S1 Sh':>6} | {'S6 PnL':>9} {'S6 Sh':>6} | {'Dv1 PnL':>9} | {'Dv2 PnL':>9} {'Dv2 Sh':>7} {'%trend/rng/trans':>16}"
    print(header); print("-"*120)

    summary = {"s1_wins": 0, "s6_wins": 0, "dual_wins": 0, "dual_losses": 0}

    for sym, itv, n, cat in SCENARIOS:
        try:
            bars = fetch_any(sym, itv, n)
            if len(bars) < 200:
                print(f"  {sym:<16} {itv:<4} - skipped (insufficient data: {len(bars)})")
                continue
            r_s1 = run_strategy(bars, s1_trend_ema, "S1", 0.02, 0.04)
            r_s6 = run_strategy(bars, s6_mtf_combo, "S6", 0.015, 0.03)
            r_dual_v1, _ = run_dual_engine(bars, transition_mode="flat")
            r_dual_v2, regime_log = run_dual_engine(bars, transition_mode="s6")
            total_regime = sum(regime_log.values()) or 1
            regime_pct = f"{regime_log['trend']*100//total_regime}/{regime_log['range']*100//total_regime}/{regime_log['transition']*100//total_regime}"

            print(f"  {sym+' '+itv:<20} {r_s1['total_pnl']:>+8.1f}% {r_s1['sharpe']:>5.2f} | "
                  f"{r_s6['total_pnl']:>+8.1f}% {r_s6['sharpe']:>5.2f} | "
                  f"{r_dual_v1.get('total_pnl',0):>+8.1f}% | {r_dual_v2.get('total_pnl',0):>+8.1f}% {r_dual_v2.get('sharpe',0):>6.2f} {regime_pct:>14}")

            # Win counting — use v2 as primary dual
            best = max(r_s1["total_pnl"], r_s6["total_pnl"], r_dual_v2.get("total_pnl", -999))
            if best == r_s1["total_pnl"]: summary["s1_wins"] += 1
            elif best == r_s6["total_pnl"]: summary["s6_wins"] += 1
            else: summary["dual_wins"] += 1

            if r_dual_v2.get("total_pnl", 0) < 0:
                summary["dual_losses"] += 1

        except Exception as e:
            print(f"  {sym:<16} {itv:<4} - ERROR: {e}")

    print("-"*110)
    print(f"  小结:  Dual 最佳次数 {summary['dual_wins']} / {len(SCENARIOS)}  "
          f"|  S1 最佳 {summary['s1_wins']}  |  S6 最佳 {summary['s6_wins']}  "
          f"|  Dual 负收益 {summary['dual_losses']}")


def single_mode(symbol, interval, bars_n):
    print(f"📡 {symbol} {interval} × {bars_n} bars...")
    bars = fetch_any(symbol, interval, bars_n)
    print(f"  ✓ {len(bars)} bars, ${bars[0]['close']:.2f} → ${bars[-1]['close']:.2f} "
          f"({(bars[-1]['close']/bars[0]['close']-1)*100:+.2f}%)\n")

    adx_series = adx(bars, 14)
    closes = [b["close"] for b in bars]
    bbw_series = bb_width(closes, 20)
    avg_adx = statistics.mean([x for x in adx_series[-500:] if x > 0])
    avg_bbw = statistics.mean(bbw_series[-500:])
    print(f"  📊 Regime: avg ADX {avg_adx:.1f} | avg BB width {avg_bbw:.2f}%")

    print(f"\n⚙️  单策略基准...")
    r_s1 = run_strategy(bars, s1_trend_ema, "S1_Trend_EMA", 0.02, 0.04)
    r_s6 = run_strategy(bars, s6_mtf_combo, "S6_MTF_Combo", 0.015, 0.03)
    print(f"  S1: {r_s1['trades']} trades, WR {r_s1['win_rate']:.1f}%, PnL {r_s1['total_pnl']:+.1f}%, Sharpe {r_s1['sharpe']:.2f}")
    print(f"  S6: {r_s6['trades']} trades, WR {r_s6['win_rate']:.1f}%, PnL {r_s6['total_pnl']:+.1f}%, Sharpe {r_s6['sharpe']:.2f}")

    print(f"\n🔀 Dual Engine...")
    r_dual, regime_log = run_dual_engine(bars)
    total = sum(regime_log.values()) or 1
    print(f"  Regime 分布: trend {regime_log['trend']*100//total}% | range {regime_log['range']*100//total}% | transition {regime_log['transition']*100//total}%")
    print(f"  总 trades: {r_dual['trades']}  (S1 {r_dual.get('s1_count',0)} | S6 {r_dual.get('s6_count',0)})")
    print(f"  WR {r_dual.get('win_rate',0):.1f}% | Sharpe {r_dual.get('sharpe',0):.2f} | PnL {r_dual.get('total_pnl',0):+.1f}% | MaxDD {r_dual.get('max_dd',0):.1f}%")
    print(f"  S1 贡献 {r_dual.get('s1_pnl',0):+.1f}% | S6 贡献 {r_dual.get('s6_pnl',0):+.1f}%")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=None)
    p.add_argument("--interval", default="1h")
    p.add_argument("--bars", type=int, default=1500)
    p.add_argument("--compare", action="store_true", help="跨资产矩阵对比")
    args = p.parse_args()

    if args.compare or args.symbol is None:
        compare_mode()
    else:
        single_mode(args.symbol, args.interval, args.bars)


if __name__ == "__main__":
    main()
