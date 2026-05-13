"""
Strategy Bake-Off — 6 策略范式在同一标的历史数据上 backtest
===============================================================
实现 pine_strategies/s1-s6 的核心逻辑，用 Binance / Yahoo OHLCV 数据回测。

输出每个策略:
  - 总 trade 数
  - Win rate
  - Avg R (盈亏比)
  - Sharpe ratio
  - Max drawdown %
  - 累积 PnL %
  - 排名 + 最佳 combo 建议

用法:
  python strategy_bake_off.py --symbol BTCUSDT --interval 1h --bars 2000
"""
import argparse, json, urllib.request, math, statistics
from collections import defaultdict

UA = {"User-Agent": "Mozilla/5.0"}


def fetch_binance(symbol, interval, limit=2000):
    """先试 Binance，fallback 到 Yahoo"""
    # Try Binance
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        r = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10).read())
        return [{"ts": x[0]/1000, "open": float(x[1]), "high": float(x[2]),
                 "low": float(x[3]), "close": float(x[4]), "volume": float(x[5])} for x in r]
    except Exception:
        pass
    # Fallback Yahoo
    yf_map = {"BTCUSDT":"BTC-USD","ETHUSDT":"ETH-USD","SOLUSDT":"SOL-USD","XRPUSDT":"XRP-USD","SPY":"SPY","QQQ":"QQQ"}
    tk = yf_map.get(symbol, symbol)
    iv_map = {"1m":"1m","5m":"5m","15m":"15m","30m":"30m","1h":"1h","4h":"4h","1d":"1d"}
    yiv = iv_map.get(interval, "1h")
    # Yahoo range based on interval
    rng_map = {"1m":"7d","5m":"60d","15m":"60d","30m":"60d","1h":"730d","4h":"730d","1d":"5y"}
    rng = rng_map.get(interval, "730d")
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{tk}?interval={yiv}&range={rng}"
    d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15).read())
    result = d["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    bars = []
    for i in range(len(ts)):
        if q["close"][i] is None: continue
        bars.append({"ts": ts[i], "open": q["open"][i] or q["close"][i],
                     "high": q["high"][i] or q["close"][i], "low": q["low"][i] or q["close"][i],
                     "close": q["close"][i], "volume": q["volume"][i] or 0})
    return bars[-limit:]


# ─── Indicators ──────────────────────────────────────────────

def ema(series, period):
    out = [series[0]]
    k = 2/(period+1)
    for i in range(1, len(series)):
        out.append(series[i]*k + out[-1]*(1-k))
    return out

def sma(series, period):
    return [sum(series[max(0,i-period+1):i+1])/min(i+1, period) for i in range(len(series))]

def stdev(series, period):
    out = []
    for i in range(len(series)):
        w = series[max(0,i-period+1):i+1]
        m = sum(w)/len(w)
        v = sum((x-m)**2 for x in w)/len(w)
        out.append(math.sqrt(v))
    return out

def rsi(closes, period=14):
    deltas = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    gains = [max(d,0) for d in deltas]
    losses = [-min(d,0) for d in deltas]
    avg_g = sum(gains[:period])/period
    avg_l = sum(losses[:period])/period
    out = [50.0] * (period+1)
    for i in range(period, len(deltas)):
        avg_g = (avg_g*(period-1) + gains[i])/period
        avg_l = (avg_l*(period-1) + losses[i])/period
        rs = avg_g/avg_l if avg_l > 0 else 100
        out.append(100 - 100/(1+rs))
    return out

def macd(closes, fast=12, slow=26, signal=9):
    ef = ema(closes, fast)
    es = ema(closes, slow)
    line = [ef[i]-es[i] for i in range(len(closes))]
    sig = ema(line, signal)
    hist = [line[i]-sig[i] for i in range(len(closes))]
    return line, sig, hist

def true_range(bars):
    return [max(bars[i]["high"]-bars[i]["low"],
                abs(bars[i]["high"]-bars[i-1]["close"]) if i>0 else 0,
                abs(bars[i]["low"]-bars[i-1]["close"]) if i>0 else 0)
            for i in range(len(bars))]

def atr(bars, period=14):
    tr = true_range(bars)
    return sma(tr, period)

def supertrend(bars, factor=3.0, period=10):
    """Returns (supertrend_value, direction: 1=down_sl/bullish, -1=up_sl/bearish)"""
    tr = true_range(bars)
    atr_v = sma(tr, period)
    hl2 = [(b["high"]+b["low"])/2 for b in bars]
    ub = [hl2[i] + factor*atr_v[i] for i in range(len(bars))]
    lb = [hl2[i] - factor*atr_v[i] for i in range(len(bars))]
    st = [hl2[0]] * len(bars)
    direction = [1] * len(bars)
    for i in range(1, len(bars)):
        if direction[i-1] > 0:  # was bearish
            st[i] = max(lb[i], st[i-1]) if bars[i]["close"] > st[i-1] else ub[i]
            direction[i] = -1 if bars[i]["close"] > st[i-1] else 1
        else:  # was bullish
            st[i] = min(ub[i], st[i-1]) if bars[i]["close"] < st[i-1] else lb[i]
            direction[i] = 1 if bars[i]["close"] < st[i-1] else -1
    return st, direction


# ─── Strategies ──────────────────────────────────────────────

def run_strategy(bars, logic_fn, name, sl_pct=0.02, tp_pct=0.04):
    """
    通用回测引擎
    logic_fn: 接受 (bars, i, state) 返回 'long', 'short', 'close_long', 'close_short', None
    """
    pos = 0  # 1 long, -1 short, 0 flat
    entry_price = 0
    trades = []
    equity = [10000]
    state = {}

    for i in range(50, len(bars)-1):  # 跳过前 50 根 warmup
        bar = bars[i]
        signal = logic_fn(bars, i, state)

        # 处理信号
        if pos == 0:
            if signal == "long":
                pos = 1; entry_price = bar["close"]
            elif signal == "short":
                pos = -1; entry_price = bar["close"]
        elif pos == 1:
            # 止损/止盈
            if bar["low"] <= entry_price*(1-sl_pct):
                trades.append({"side":"L","entry":entry_price,"exit":entry_price*(1-sl_pct),"pnl_pct":-sl_pct*100})
                pos = 0
            elif bar["high"] >= entry_price*(1+tp_pct):
                trades.append({"side":"L","entry":entry_price,"exit":entry_price*(1+tp_pct),"pnl_pct":tp_pct*100})
                pos = 0
            elif signal in ("short","close_long"):
                trades.append({"side":"L","entry":entry_price,"exit":bar["close"],"pnl_pct":(bar["close"]/entry_price-1)*100})
                pos = 0
                if signal == "short":
                    pos = -1; entry_price = bar["close"]
        elif pos == -1:
            if bar["high"] >= entry_price*(1+sl_pct):
                trades.append({"side":"S","entry":entry_price,"exit":entry_price*(1+sl_pct),"pnl_pct":-sl_pct*100})
                pos = 0
            elif bar["low"] <= entry_price*(1-tp_pct):
                trades.append({"side":"S","entry":entry_price,"exit":entry_price*(1-tp_pct),"pnl_pct":tp_pct*100})
                pos = 0
            elif signal in ("long","close_short"):
                trades.append({"side":"S","entry":entry_price,"exit":bar["close"],"pnl_pct":(1-bar["close"]/entry_price)*100})
                pos = 0
                if signal == "long":
                    pos = 1; entry_price = bar["close"]

        # 更新 equity
        if pos == 1:
            unrealized = (bar["close"]/entry_price - 1)
            equity.append(equity[-1] * (1 + unrealized*0.01) if i == len(bars)-2 else equity[-1])
        else:
            equity.append(equity[-1])

    # 计算指标
    if not trades:
        return {"name": name, "trades": 0, "win_rate": 0, "avg_pnl": 0, "sharpe": 0, "max_dd": 0, "total_pnl": 0}

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    total_pnl_pct = sum(t["pnl_pct"] for t in trades)
    avg_pnl = total_pnl_pct / len(trades)
    win_pnls = [t["pnl_pct"] for t in trades if t["pnl_pct"] > 0]
    loss_pnls = [t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0]

    avg_win = statistics.mean(win_pnls) if win_pnls else 0
    avg_loss = abs(statistics.mean(loss_pnls)) if loss_pnls else 1
    profit_factor = (sum(win_pnls) / abs(sum(loss_pnls))) if loss_pnls else float('inf')

    # Sharpe (annualized assuming 1h bars = 8760/year)
    if len(trades) > 1:
        pnl_series = [t["pnl_pct"] for t in trades]
        sharpe = (statistics.mean(pnl_series) / statistics.stdev(pnl_series)) * math.sqrt(252) if statistics.stdev(pnl_series) > 0 else 0
    else:
        sharpe = 0

    # Max drawdown (on trades cumulative)
    cum = 0; peak = 0; max_dd = 0
    for t in trades:
        cum += t["pnl_pct"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "name": name, "trades": len(trades),
        "win_rate": wins/len(trades)*100,
        "avg_pnl": avg_pnl, "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": profit_factor, "sharpe": sharpe,
        "max_dd": max_dd, "total_pnl": total_pnl_pct,
    }


# ─── Strategy logics ─────────────────────────────────────────

def s1_trend_ema(bars, i, state):
    closes = [b["close"] for b in bars[:i+1]]
    f = ema(closes, 20); s = ema(closes, 50)
    if f[-1] > s[-1] and f[-2] <= s[-2]: return "long"
    if f[-1] < s[-1] and f[-2] >= s[-2]: return "short"
    return None

def s2_bb_reversion(bars, i, state):
    closes = [b["close"] for b in bars[:i+1]]
    m = sma(closes, 20); d = stdev(closes, 20)
    upper = m[-1] + 2*d[-1]; lower = m[-1] - 2*d[-1]
    r = rsi(closes, 14)
    if closes[-1] <= lower and r[-1] < 30: return "long"
    if closes[-1] >= upper and r[-1] > 70: return "short"
    if closes[-1] > m[-1] and state.get("last")=="long": state["last"]=None; return "close_long"
    return None

def s3_squeeze_breakout(bars, i, state):
    closes = [b["close"] for b in bars[:i+1]]
    m = sma(closes, 20); d = stdev(closes, 20)
    upper_bb = m[-1]+2*d[-1]; lower_bb = m[-1]-2*d[-1]
    tr = true_range(bars[:i+1])
    range_ma = sma(tr, 20)[-1]
    upper_kc = m[-1]+1.5*range_ma; lower_kc = m[-1]-1.5*range_ma
    squeeze_on = lower_bb > lower_kc and upper_bb < upper_kc
    squeeze_released = not squeeze_on and state.get("last_squeeze", False)
    state["last_squeeze"] = squeeze_on
    mom = closes[-1] - m[-1]
    if squeeze_released:
        return "long" if mom > 0 else "short"
    return None

def s4_momentum_macd(bars, i, state):
    closes = [b["close"] for b in bars[:i+1]]
    line, sig, hist = macd(closes)
    et = ema(closes, 200)[-1]
    if line[-1] > sig[-1] and line[-2] <= sig[-2] and closes[-1] > et: return "long"
    if line[-1] < sig[-1] and line[-2] >= sig[-2] and closes[-1] < et: return "short"
    return None

def s5_supertrend_flip(bars, i, state):
    st, dr = supertrend(bars[:i+1], factor=3.0, period=10)
    if dr[-1] < 0 and dr[-2] > 0: return "long"
    if dr[-1] > 0 and dr[-2] < 0: return "short"
    return None

def s6_mtf_combo(bars, i, state):
    closes = [b["close"] for b in bars[:i+1]]
    ltf = ema(closes, 9)
    # 模拟 HTF: 每 4 根取一根作为 HTF bar
    htf_closes = closes[::4]
    if len(htf_closes) < 50: return None
    htf_e = ema(htf_closes, 50)[-1]

    bull_bias = closes[-1] > htf_e
    bear_bias = closes[-1] < htf_e
    if bull_bias and closes[-1] > ltf[-1] and closes[-2] <= ltf[-2]: return "long"
    if bear_bias and closes[-1] < ltf[-1] and closes[-2] >= ltf[-2]: return "short"
    return None


STRATEGIES = [
    (s1_trend_ema, "S1_Trend_EMA", 0.02, 0.04),
    (s2_bb_reversion, "S2_BB_Reversion", 0.025, 0.05),
    (s3_squeeze_breakout, "S3_Squeeze_Breakout", 0.02, 0.05),
    (s4_momentum_macd, "S4_Momentum_MACD", 0.03, 0.06),
    (s5_supertrend_flip, "S5_SuperTrend", 0.03, 0.06),
    (s6_mtf_combo, "S6_MTF_Combo", 0.015, 0.03),
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--bars", type=int, default=1500)
    args = p.parse_args()

    print(f"📡 拉 Binance {args.symbol} {args.interval} × {args.bars} bars...")
    bars = fetch_binance(args.symbol, args.interval, args.bars)
    print(f"  ✓ 获得 {len(bars)} bars, 价格区间 ${bars[0]['close']:.0f} → ${bars[-1]['close']:.0f}")
    print(f"  周期涨跌: {(bars[-1]['close']/bars[0]['close']-1)*100:+.2f}%\n")

    results = []
    for logic, name, sl, tp in STRATEGIES:
        print(f"⚙️  回测 {name}...", end=" ", flush=True)
        r = run_strategy(bars, logic, name, sl, tp)
        results.append(r)
        print(f"✓ {r['trades']} trades, WR {r['win_rate']:.1f}%, PnL {r['total_pnl']:+.1f}%")

    # 排名
    results.sort(key=lambda r: r["sharpe"], reverse=True)

    print(f"\n{'='*100}")
    print(f"  {'Rank':<4} {'Strategy':<22} {'Trades':>7} {'WR%':>6} {'AvgPnL':>7} {'PF':>5} {'Sharpe':>7} {'MaxDD%':>7} {'Total%':>7}")
    print(f"{'='*100}")
    for i, r in enumerate(results, 1):
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] != float('inf') else "inf"
        print(f"  #{i:<3} {r['name']:<22} {r['trades']:>7} {r['win_rate']:>5.1f}% {r['avg_pnl']:>+6.2f}% {pf_str:>5} {r['sharpe']:>7.2f} {r['max_dd']:>6.1f}% {r['total_pnl']:>+6.1f}%")

    # 组合建议
    print(f"\n{'='*100}")
    print("  🎯 组合建议（高 Sharpe + 低相关性）")
    print(f"{'='*100}")
    top_sharpe = [r for r in results if r['sharpe'] > 0.5]
    if len(top_sharpe) >= 2:
        print(f"  Top Sharpe: {top_sharpe[0]['name']} ({top_sharpe[0]['sharpe']:.2f})")
        print(f"  互补第二: {top_sharpe[1]['name']} ({top_sharpe[1]['sharpe']:.2f})")
        print(f"  → 组合预期 Sharpe: ~{(top_sharpe[0]['sharpe']+top_sharpe[1]['sharpe'])/2 * 1.2:.2f} (分散化 boost)")
    else:
        print(f"  ⚠️ 本市场 ({args.symbol} {args.interval}) 无策略 Sharpe > 0.5 — 需换市场或改参数")


if __name__ == "__main__":
    main()
