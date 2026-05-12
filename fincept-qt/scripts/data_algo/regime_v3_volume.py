"""
Regime Classifier v3: ADX + BBW + Volume Delta (propfirm-inspired)
====================================================================
v1/v2 的 regime 只看价格（ADX + BB width）。v3 加 volume 维度:

  - volume_delta = up_volume - down_volume  (OFI 近似)
  - volume_regime: 'accumulation' / 'distribution' / 'balanced'
  - 组合: trend + accumulation → 强 long bias
          range + balanced → MM 风格
          divergence (价格 up 但 volume 偏 distribution) → skip

灵感: Cont-Kukanov-Stoikov 2013 OFI + Avellaneda-Stoikov 2008 reservation
       + TradingLite VPVR/TPO 的 "auction process" 概念
"""
import argparse, sys, os, math, statistics
sys.path.insert(0, os.path.dirname(__file__))
from strategy_bake_off import fetch_binance, ema, sma, stdev, s1_trend_ema, s6_mtf_combo, run_strategy
from _attic.regime_dual_engine import adx, bb_width, classify_regime, fetch_any, run_dual_engine


def volume_delta(bars, period=20):
    """
    近似 OFI: 对每根 bar,
      up_vol = volume if close>open else 0
      down_vol = volume if close<open else 0
    rolling net delta / rolling total → [-1, +1]
    """
    out = [0.0] * len(bars)
    for i in range(len(bars)):
        w = bars[max(0, i-period+1):i+1]
        up = sum(b["volume"] for b in w if b["close"] > b["open"])
        dn = sum(b["volume"] for b in w if b["close"] < b["open"])
        tot = up + dn
        out[i] = (up - dn) / tot if tot > 0 else 0
    return out


def volume_regime(bars, i, vd_series, lookback=50):
    """
    返回 'accumulation' / 'distribution' / 'balanced' / 'unknown'
    """
    if i < lookback or i >= len(vd_series):
        return "unknown"
    vd = vd_series[i]
    hist = vd_series[max(0, i-lookback):i]
    if not hist:
        return "unknown"
    mean_vd = statistics.mean(hist)
    if vd > 0.15 and vd > mean_vd + 0.1:
        return "accumulation"
    if vd < -0.15 and vd < mean_vd - 0.1:
        return "distribution"
    if abs(vd) < 0.08:
        return "balanced"
    return "unknown"


def classify_v3(bars, i, adx_s, bbw_s, vd_s):
    """
    组合 regime:
      trend + accumulation  → 'strong_long'   (S1 long only)
      trend + distribution  → 'strong_short'  (S1 short only)
      trend + unknown       → 'weak_trend'    (S1 both sides, small size)
      range + balanced      → 'mm_range'      (S6 both sides)
      range + accumulation/distribution → 'range_divergent' (S6 single side)
      transition            → 'skip'
      divergence            → 'avoid' (价格趋势但 volume 相反)
    """
    price_r = classify_regime(bars, i, adx_s, bbw_s)
    vol_r = volume_regime(bars, i, vd_s)

    if price_r == "transition":
        return "skip"

    if price_r == "trend":
        # Check divergence: trend up but distribution = avoid
        if len(bars) > i >= 1:
            recent_up = bars[i]["close"] > bars[i-1]["close"]
            if recent_up and vol_r == "distribution":
                return "avoid"  # bull trap risk
            if (not recent_up) and vol_r == "accumulation":
                return "avoid"  # bear trap risk
        if vol_r == "accumulation":
            return "strong_long"
        if vol_r == "distribution":
            return "strong_short"
        return "weak_trend"

    if price_r == "range":
        if vol_r == "balanced":
            return "mm_range"
        if vol_r in ("accumulation", "distribution"):
            return "range_divergent"
        return "skip"

    return "skip"


def run_v3_engine(bars, sl=0.015, tp=0.03):
    """Regime v3 驱动的混合策略"""
    closes = [b["close"] for b in bars]
    adx_s = adx(bars, 14)
    bbw_s = bb_width(closes, 20)
    vd_s = volume_delta(bars, 20)

    pos = 0
    entry = 0
    active = None  # regime identifier
    trades = []
    regime_count = {}

    for i in range(50, len(bars)-1):
        bar = bars[i]
        r = classify_v3(bars, i, adx_s, bbw_s, vd_s)
        regime_count[r] = regime_count.get(r, 0) + 1

        # Close if regime incompatible or avoid
        if pos != 0:
            incompat = False
            if r in ("skip", "avoid"):
                incompat = True
            elif active == "strong_long" and r in ("strong_short", "range_divergent"):
                incompat = True
            elif active == "strong_short" and r == "strong_long":
                incompat = True
            if incompat:
                pnl = (bar["close"]/entry - 1)*100 if pos == 1 else (1 - bar["close"]/entry)*100
                trades.append({"side": "L" if pos==1 else "S", "regime": active,
                              "entry": entry, "exit": bar["close"], "pnl_pct": pnl, "reason": "regime_incompat"})
                pos = 0; active = None

        if pos != 0:
            # SL/TP
            if pos == 1:
                if bar["low"] <= entry*(1-sl):
                    trades.append({"side":"L","regime":active,"entry":entry,"exit":entry*(1-sl),"pnl_pct":-sl*100,"reason":"SL"})
                    pos = 0; active = None
                elif bar["high"] >= entry*(1+tp):
                    trades.append({"side":"L","regime":active,"entry":entry,"exit":entry*(1+tp),"pnl_pct":tp*100,"reason":"TP"})
                    pos = 0; active = None
            elif pos == -1:
                if bar["high"] >= entry*(1+sl):
                    trades.append({"side":"S","regime":active,"entry":entry,"exit":entry*(1+sl),"pnl_pct":-sl*100,"reason":"SL"})
                    pos = 0; active = None
                elif bar["low"] <= entry*(1-tp):
                    trades.append({"side":"S","regime":active,"entry":entry,"exit":entry*(1-tp),"pnl_pct":tp*100,"reason":"TP"})
                    pos = 0; active = None
            continue

        # Entry logic
        if r == "strong_long":
            sig = s1_trend_ema(bars, i, {})
            if sig == "long":
                pos = 1; entry = bar["close"]; active = r
        elif r == "strong_short":
            sig = s1_trend_ema(bars, i, {})
            if sig == "short":
                pos = -1; entry = bar["close"]; active = r
        elif r == "mm_range":
            sig = s6_mtf_combo(bars, i, {})
            if sig == "long":
                pos = 1; entry = bar["close"]; active = r
            elif sig == "short":
                pos = -1; entry = bar["close"]; active = r
        elif r == "weak_trend":
            # S1 but half size (simulated by tighter SL)
            sig = s1_trend_ema(bars, i, {})
            if sig == "long":
                pos = 1; entry = bar["close"]; active = r
            elif sig == "short":
                pos = -1; entry = bar["close"]; active = r
        # range_divergent / avoid / skip → no trade

    if not trades:
        return {"name": "v3", "trades": 0, "regime_count": regime_count}

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    total = sum(t["pnl_pct"] for t in trades)
    pnls = [t["pnl_pct"] for t in trades]
    sharpe = (statistics.mean(pnls)/statistics.stdev(pnls))*math.sqrt(252) if len(pnls)>1 and statistics.stdev(pnls)>0 else 0
    cum=0; peak=0; dd=0
    for t in trades:
        cum += t["pnl_pct"]; peak = max(peak, cum); dd = max(dd, peak-cum)

    by_regime = {}
    for t in trades:
        r = t.get("regime", "?")
        by_regime.setdefault(r, []).append(t["pnl_pct"])

    return {
        "name": "v3", "trades": len(trades),
        "win_rate": wins/len(trades)*100,
        "sharpe": sharpe, "total_pnl": total, "max_dd": dd,
        "regime_count": regime_count,
        "by_regime": {r: {"n": len(v), "pnl": sum(v)} for r, v in by_regime.items()},
    }


def main():
    SCENARIOS = [
        ("BTCUSDT", "1h", 1500), ("ETHUSDT", "1h", 1500),
        ("SPY", "1h", 1500), ("GC=F", "1h", 1500),
        ("CL=F", "1h", 1500), ("EURUSD=X", "1h", 1500),
        ("BTCUSDT", "15m", 2000),
    ]
    print(f"{'='*100}")
    print(f"  Regime v3 (ADX + BBW + Volume Delta) vs v2 baseline")
    print(f"{'='*100}")
    print(f"  {'Scenario':<18} {'v3 trades':>9} {'v3 WR':>6} {'v3 PnL':>8} {'v3 Sh':>6}  {'top regime distribution'}")
    print("-"*100)
    for sym, itv, n in SCENARIOS:
        try:
            bars = fetch_any(sym, itv, n)
            if len(bars) < 200:
                print(f"  {sym} {itv} - insufficient")
                continue
            r = run_v3_engine(bars)
            rc = r.get("regime_count", {})
            total = sum(rc.values()) or 1
            top = sorted(rc.items(), key=lambda x: -x[1])[:3]
            rc_str = " | ".join(f"{k[:8]}:{v*100//total}%" for k, v in top)
            print(f"  {sym+' '+itv:<18} {r.get('trades',0):>9} {r.get('win_rate',0):>5.1f}% {r.get('total_pnl',0):>+7.1f}% {r.get('sharpe',0):>5.2f}  {rc_str}")
        except Exception as e:
            print(f"  {sym} {itv} - ERR: {e}")


if __name__ == "__main__":
    main()
