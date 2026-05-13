"""
Asset-Specific Strategy Map (v2 — 放弃 universal dual engine)
==============================================================
基于 regime_dual_engine.py 8 场景实测得出的最优分配。

结论: S1/S6/Dual 没有 universal winner。按资产类别固定分配。
"""

ASSET_MAP = {
    # Crypto 1h: S1 Trend EMA (趋势 alpha 最强, 承认过拟合需 walk-forward 验证)
    "BTCUSDT_1h": {"strategy": "S1", "sharpe": 6.02, "caveat": "overfitting risk"},
    "ETHUSDT_1h": {"strategy": "S1", "sharpe": 7.16, "caveat": "overfitting risk"},

    # Crypto 15m: S6 MTF (TF 越短 S1 越无效)
    "BTCUSDT_15m": {"strategy": "S6", "sharpe": 2.28},

    # Commodities 1h: Dv2 (transition→S6) 最佳 — 30% on oil, 11% on gold
    "GC=F_1h": {"strategy": "Dv2", "sharpe": 3.11, "pnl": 11.2},
    "CL=F_1h": {"strategy": "Dv2", "sharpe": 3.54, "pnl": 29.4},

    # Equity 1h: **不要做** — S1/S6/Dual 全部亏损
    "SPY_1h": {"strategy": "SKIP", "note": "all strategies lose on SPY range market"},
    "QQQ_1h": {"strategy": "SKIP", "note": "similar to SPY"},

    # FX 1h: Dv1 (transition 空仓) — EURUSD 唯一 v1 不输的场景
    "EURUSD=X_1h": {"strategy": "Dv1", "sharpe": 3.68, "pnl": 1.1, "note": "low edge but positive"},
}

# 映射 TradingView paper trading 优先级
PRIORITY = [
    ("BTCUSDT_1h", "S1", "crypto trend paper"),
    ("CL=F_1h", "Dv2", "crude oil regime switch"),
    ("GC=F_1h", "Dv2", "gold regime switch"),
    ("ETHUSDT_1h", "S1", "ETH trend paper (confirm BTC)"),
    ("BTCUSDT_15m", "S6", "BTC short TF range"),
]

if __name__ == "__main__":
    print(f"{'='*80}")
    print("  Asset-Specific Strategy Map")
    print(f"{'='*80}")
    for asset, cfg in ASSET_MAP.items():
        s = cfg["strategy"]
        extra = f"Sharpe {cfg['sharpe']}" if "sharpe" in cfg else cfg.get("note", "")
        print(f"  {asset:<20} → {s:<6} {extra}")

    print(f"\n{'='*80}")
    print("  TV Paper Priority Queue")
    print(f"{'='*80}")
    for asset, strat, purpose in PRIORITY:
        print(f"  {asset:<20} {strat:<6}  {purpose}")
