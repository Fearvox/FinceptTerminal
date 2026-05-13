"""
TradingView Paper Trader — 监控 Precision Sniper + SATS 信号，自动 paper 下单
============================================================================

基于 TV MCP 实时轮询指标，检测 entry/exit 信号，在内部 DB 模拟 $100k paper 账户。
不动 TV UI（TV paper 是独立系统），我们自己的 paper 记录可以无限扩展、备份、回测。

信号规则（融合 Precision Sniper 逻辑 + SATS）:
  1. **多头 entry**:
     - Precision Sniper: Price > EMA Fast > EMA Slow > EMA Trend (4 层堆叠)
     - SATS: Price > SuperTrend (bullish side)
     - 要求同时成立，才开多
  2. **空头 entry**: 反之
  3. **Exit**:
     - 止损：SL = entry - 1×ATR (用 EMA Fast-Slow 差值近似 ATR)
     - TP1 = 1:1 R  → 50% 仓位平
     - TP2 = 1:2 R  → 再平 25%
     - TP3 = 1:3 R  → 剩 25% trail 止损到 TP2
  4. **Character flip**: EMA Fast 下穿 Slow 时强制平仓

用法:
  python tv_paper_trader.py poll-once          # 检查一次信号
  python tv_paper_trader.py run --interval 30  # 持续轮询
  python tv_paper_trader.py positions          # 看 paper 仓位
  python tv_paper_trader.py stats              # 胜率/PnL 统计
  python tv_paper_trader.py reset              # 清空重新开始
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "tv_paper.db"
TV_CLI = "/Users/0xvox/tradingview-mcp/src/cli/index.js"
PAPER_CAPITAL = 100_000.0
RISK_PER_TRADE_PCT = 0.01   # 1% 账户风险/笔 = $1000 max 损失/笔


def tv(cmd: list) -> dict:
    """执行 TV CLI 并返回 JSON"""
    try:
        r = subprocess.run(["node", TV_CLI] + cmd, capture_output=True, timeout=15)
        return json.loads(r.stdout.decode(errors="ignore"))
    except Exception as e:
        return {"error": str(e), "success": False}


def ensure_schema():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,            -- LONG / SHORT
            entry_price REAL NOT NULL,
            entry_ts TEXT NOT NULL,
            size_usd REAL NOT NULL,
            shares REAL NOT NULL,
            sl REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            tp3 REAL NOT NULL,
            signal_reason TEXT,
            indicator_snapshot TEXT,       -- JSON of all indicator values at entry
            closed INTEGER DEFAULT 0,
            exit_price REAL,
            exit_ts TEXT,
            exit_reason TEXT,
            realized_pnl_usd REAL
        );
        CREATE TABLE IF NOT EXISTS account_snapshots (
            ts TEXT PRIMARY KEY,
            equity REAL NOT NULL,
            cash REAL NOT NULL,
            open_trades INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS last_signal (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            symbol TEXT,
            bias TEXT,                     -- BULL / BEAR / NEUTRAL
            ts TEXT,
            snapshot TEXT
        );
    """)
    conn.commit()
    conn.close()


def read_market() -> dict:
    """拉当前 symbol + 所有指标值"""
    q = tv(["quote"])
    st = tv(["state"])
    v = tv(["values"])
    if not q.get("success"):
        return {"error": "quote failed"}

    symbol = q.get("symbol")
    price = q.get("last") or q.get("close")
    resolution = st.get("resolution")

    inds = {}
    for s in v.get("studies", []):
        name = s.get("name", "")
        vals = s.get("values", {})
        if "Precision Sniper" in name:
            inds["ema_fast"] = _parse_num(vals.get("EMA Fast"))
            inds["ema_slow"] = _parse_num(vals.get("EMA Slow"))
            inds["ema_trend"] = _parse_num(vals.get("EMA Trend"))
        elif "Self-Aware" in name:
            inds["supertrend"] = _parse_num(vals.get("SuperTrend"))
        elif "Volume" == name:
            inds["volume"] = _parse_num(vals.get("Volume"))
        elif "ICT Killzone" in name or "Killzone" in name:
            inds["session"] = vals  # full dict
        elif "Market Structure" in name:
            inds["ms_values"] = vals
        elif "Smart Money" in name:
            inds["smc_values"] = vals
    return {"symbol": symbol, "price": price, "resolution": resolution, "indicators": inds}


def _parse_num(v):
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    s = str(v).replace(",", "").strip()
    try: return float(s)
    except: return None


def detect_bias(m: dict) -> tuple[str, str]:
    """返回 (BULL|BEAR|NEUTRAL, reason)"""
    inds = m["indicators"]
    price = m["price"]
    fast = inds.get("ema_fast")
    slow = inds.get("ema_slow")
    trend = inds.get("ema_trend")
    st = inds.get("supertrend")

    if None in (price, fast, slow, trend, st):
        return "NEUTRAL", "缺数据"

    # Bullish stack: price > fast > slow > trend AND price > st
    if price > fast > slow > trend and price > st:
        return "BULL", f"Stack多头: P({price})>F({fast:.2f})>S({slow:.2f})>T({trend:.2f}), ST({st:.2f}) below"
    # Bearish: 反之
    if price < fast < slow < trend and price < st:
        return "BEAR", f"Stack空头: P<F<S<T + ST above"
    return "NEUTRAL", f"P={price} F={fast:.2f} S={slow:.2f} T={trend:.2f} ST={st:.2f} 不成栈"


def check_and_trade():
    ensure_schema()
    m = read_market()
    if "error" in m:
        print(f"❌ {m['error']}")
        return

    bias, reason = detect_bias(m)
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {m['symbol']} @ {m['price']}")
    print(f"  Bias: {bias}  —  {reason}")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 更新 last_signal
    c.execute("INSERT OR REPLACE INTO last_signal (id, symbol, bias, ts, snapshot) VALUES (1, ?, ?, ?, ?)",
              (m["symbol"], bias, datetime.now(timezone.utc).isoformat(),
               json.dumps(m["indicators"], default=str)))

    # 看当前有没有 open trade
    c.execute("SELECT id, side, entry_price, sl, tp1, tp2, tp3, shares FROM paper_trades WHERE closed=0 AND symbol=?",
              (m["symbol"],))
    open_trades = c.fetchall()

    # 处理 open trades 的 SL/TP
    for tr in open_trades:
        tid, side, entry, sl, tp1, tp2, tp3, shares = tr
        exit_reason = None
        exit_price = None
        if side == "LONG":
            if m["price"] <= sl:
                exit_reason, exit_price = "SL_HIT", sl
            elif m["price"] >= tp3:
                exit_reason, exit_price = "TP3_HIT", tp3
            # 简化：TP1/TP2 部分止盈暂不做（需要跟踪剩余仓位），整笔 TP3 止盈
        else:  # SHORT
            if m["price"] >= sl:
                exit_reason, exit_price = "SL_HIT", sl
            elif m["price"] <= tp3:
                exit_reason, exit_price = "TP3_HIT", tp3
        # Character flip exit
        if bias == "BEAR" and side == "LONG":
            exit_reason, exit_price = "CHAR_FLIP_BEAR", m["price"]
        elif bias == "BULL" and side == "SHORT":
            exit_reason, exit_price = "CHAR_FLIP_BULL", m["price"]

        if exit_reason:
            pnl = shares * (exit_price - entry) if side == "LONG" else shares * (entry - exit_price)
            c.execute("""UPDATE paper_trades SET closed=1, exit_price=?, exit_ts=?, exit_reason=?, realized_pnl_usd=?
                         WHERE id=?""",
                      (exit_price, datetime.now(timezone.utc).isoformat(), exit_reason, pnl, tid))
            print(f"  ✅ Close #{tid} {side} @ {exit_price} ({exit_reason}) PnL ${pnl:+.2f}")

    # 看有没有新 entry signal
    if bias in ("BULL", "BEAR") and not open_trades:
        # 算 SL/TP
        fast = m["indicators"].get("ema_fast")
        slow = m["indicators"].get("ema_slow")
        price = m["price"]
        # 粗 ATR: |fast - slow| * 2
        atr_proxy = abs(fast - slow) * 2 if fast and slow else price * 0.002
        if bias == "BULL":
            sl = price - atr_proxy
            tp1 = price + atr_proxy      # 1:1
            tp2 = price + 2 * atr_proxy  # 1:2
            tp3 = price + 3 * atr_proxy  # 1:3
            side = "LONG"
        else:
            sl = price + atr_proxy
            tp1 = price - atr_proxy
            tp2 = price - 2 * atr_proxy
            tp3 = price - 3 * atr_proxy
            side = "SHORT"

        # Position sizing: 1% risk / (entry - sl)
        risk_usd = PAPER_CAPITAL * RISK_PER_TRADE_PCT
        risk_per_share = abs(price - sl)
        if risk_per_share < 0.001:
            print(f"  ⚠️ SL 太近 ({risk_per_share}), 跳过")
        else:
            shares = risk_usd / risk_per_share
            size_usd = shares * price

            c.execute("""INSERT INTO paper_trades
                (symbol, side, entry_price, entry_ts, size_usd, shares, sl, tp1, tp2, tp3,
                 signal_reason, indicator_snapshot)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (m["symbol"], side, price, datetime.now(timezone.utc).isoformat(),
                 size_usd, shares, sl, tp1, tp2, tp3, reason,
                 json.dumps(m["indicators"], default=str)))
            print(f"  🎯 OPEN {side} @ {price} size=${size_usd:.0f} ({shares:.2f} shares)")
            print(f"     SL={sl:.2f}  TP1={tp1:.2f}  TP2={tp2:.2f}  TP3={tp3:.2f}")

    conn.commit()
    conn.close()


def positions():
    ensure_schema()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT id, symbol, side, entry_price, size_usd, sl, tp3, entry_ts
                 FROM paper_trades WHERE closed=0""")
    open_t = c.fetchall()
    print(f"\n开仓 {len(open_t)}:")
    for t in open_t:
        tid, sym, side, ep, size, sl, tp3, ts = t
        print(f"  #{tid} {sym} {side} @ {ep} size=${size:.0f} SL={sl:.2f} TP3={tp3:.2f} opened {ts[:19]}")

    c.execute("""SELECT id, symbol, side, entry_price, exit_price, exit_reason, realized_pnl_usd
                 FROM paper_trades WHERE closed=1 ORDER BY id DESC LIMIT 10""")
    closed = c.fetchall()
    print(f"\n最近平仓 {len(closed)}:")
    for t in closed:
        tid, sym, side, ep, xp, reason, pnl = t
        print(f"  #{tid} {sym} {side} {ep}→{xp} ({reason}) PnL ${pnl:+.2f}")
    conn.close()


def stats():
    ensure_schema()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(CASE WHEN realized_pnl_usd>0 THEN 1 ELSE 0 END), SUM(realized_pnl_usd) FROM paper_trades WHERE closed=1")
    n, w, pnl = c.fetchone()
    n = n or 0
    print(f"\n📊 Paper Trading Stats")
    print(f"   已平仓: {n}   |   胜: {w or 0}   |   胜率: {100*(w or 0)/n if n else 0:.1f}%")
    print(f"   累计 PnL: ${(pnl or 0):+.2f}   |   账户 equity: ${PAPER_CAPITAL + (pnl or 0):.2f}")

    c.execute("SELECT COUNT(*) FROM paper_trades WHERE closed=0")
    print(f"   开仓: {c.fetchone()[0]}")

    # 按 symbol 分组
    c.execute("""SELECT symbol, COUNT(*), SUM(realized_pnl_usd)
                 FROM paper_trades WHERE closed=1 GROUP BY symbol""")
    print(f"\n   按 symbol:")
    for sym, n, pnl in c.fetchall():
        print(f"     {sym:25} n={n} PnL ${pnl:+.2f}")
    conn.close()


def run_loop(interval: int):
    print(f"🔄 轮询模式, 每 {interval}s 检查一次。Ctrl+C 停止。")
    while True:
        try:
            check_and_trade()
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n停止。")
            break
        except Exception as e:
            print(f"⚠️ {e}")
            time.sleep(interval)


def reset():
    DB_PATH.unlink(missing_ok=True)
    ensure_schema()
    print(f"✓ 重置完成")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("poll-once")
    r = sub.add_parser("run"); r.add_argument("--interval", type=int, default=30)
    sub.add_parser("positions")
    sub.add_parser("stats")
    sub.add_parser("reset")

    args = p.parse_args()
    if args.cmd == "poll-once": check_and_trade()
    elif args.cmd == "run": run_loop(args.interval)
    elif args.cmd == "positions": positions()
    elif args.cmd == "stats": stats()
    elif args.cmd == "reset": reset()


if __name__ == "__main__":
    main()
