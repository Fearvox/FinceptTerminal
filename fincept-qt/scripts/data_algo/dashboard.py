"""
Dashboard — 一键总览
========================
聚合 decision_scoring + manifold_position + playbook + latest candidates
一条命令看全部状态。
"""
import sqlite3
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import sys

AUDIT_DB = Path(__file__).parent / "audit_log.db"
SCAN_DB = Path(__file__).parent / "wolf_scanner.db"
MANIFOLD = "https://api.manifold.markets/v0"
UA = {"User-Agent": "Mozilla/5.0"}


def _req(url, timeout=10):
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())
    except Exception:
        return None


def section(t):
    print(f"\n{'═'*72}")
    print(f"  {t}")
    print(f"{'═'*72}")


def live_position():
    u = _req(f"{MANIFOLD}/user/0xVox")
    if not u:
        return
    balance = u.get("balance", 0)
    deposits = u.get("totalDeposits", 0)
    print(f"  Manifold 余额:      M${balance}")
    print(f"  累计充值:           M${deposits}")

    # Virginia MTM
    bets = _req(f"{MANIFOLD}/bets?username=0xVox&limit=50")
    if not bets:
        return
    markets_touched = {}
    for b in bets:
        cid = b.get("contractId")
        if cid not in markets_touched:
            markets_touched[cid] = {"no_shares": 0, "yes_shares": 0, "invested": 0}
        side = b.get("outcome")
        if side == "NO":
            markets_touched[cid]["no_shares"] += b.get("shares", 0)
        else:
            markets_touched[cid]["yes_shares"] += b.get("shares", 0)
        markets_touched[cid]["invested"] += b.get("amount", 0)

    total_mtm = 0
    total_invested = 0
    for cid, pos in markets_touched.items():
        m = _req(f"{MANIFOLD}/market/{cid}")
        if not m:
            continue
        yes_prob = m.get("probability", 0.5)
        mtm = pos["no_shares"] * (1 - yes_prob) + pos["yes_shares"] * yes_prob
        pnl = mtm - pos["invested"]
        total_mtm += mtm
        total_invested += pos["invested"]
        side = "NO" if pos["no_shares"] > pos["yes_shares"] else "YES"
        shares = max(pos["no_shares"], pos["yes_shares"])
        print(f"\n  📊 {m.get('question','?')[:55]}")
        print(f"     仓位: {side} {shares:.0f} shares | 投入 M${pos['invested']:.0f} | MTM M${mtm:.2f} | PnL {pnl:+.2f}")
        print(f"     市场 YES: {yes_prob*100:.1f}% | 结算: {datetime.fromtimestamp(m.get('closeTime',0)/1000, timezone.utc).strftime('%Y-%m-%d')}")

    print(f"\n  总 MTM: M${total_mtm:.2f} / 投入 M${total_invested:.0f} ({(total_mtm-total_invested):+.2f})")
    print(f"  可用现金: M${balance}")
    print(f"  总流动性资产: M${balance + total_mtm:.2f}")


def scoring():
    conn = sqlite3.connect(AUDIT_DB)
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(hit), AVG(brier) FROM decisions WHERE resolved=1")
    n, w, br = c.fetchone()
    print(f"  已结算决策: {n or 0}  |  胜率: {w or 0}/{n or 0}  |  平均 Brier: {br or 0:.3f}")

    print(f"\n  ── 高权重偏差 ──")
    c.execute("SELECT name, weight, total_triggered FROM bias_weights WHERE weight > 1 ORDER BY weight DESC LIMIT 5")
    for name, w, t in c.fetchall():
        flag = "🔴" if w > 1.3 else "🟡"
        print(f"    {flag} {name:28} {w:.2f}  ({t} 触发)")
    conn.close()


def playbook_view():
    conn = sqlite3.connect(AUDIT_DB)
    c = conn.cursor()
    c.execute("SELECT rank, trigger_condition, side, edge_pp, recommended_amount_pct, market_q, market_slug FROM playbook WHERE status='pending' ORDER BY rank")
    rows = c.fetchall()
    conn.close()
    if not rows:
        print("  (playbook 为空)")
        return
    cur = None
    for rank, tr, side, edge, sz, q, slug in rows:
        if tr != cur:
            print(f"\n  ▼ 触发: {tr}")
            cur = tr
        print(f"    #{rank:<2} {side:>3} +{edge:>4.1f}pp  size={sz*100:>4.1f}%  {q[:55]}")
        print(f"        → {slug}")


def main():
    print(f"\n📊 FinceptTerminal / Manifold Trading Dashboard")
    print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  —  用户 0xVox")

    section("1. 实时仓位")
    live_position()

    section("2. 决策评分 + 学习状态")
    scoring()

    section("3. Playbook — 按触发条件排")
    playbook_view()

    section("4. 下一步建议")
    print("""
  ⚡ 立即可做（若能充 M$20-100）:
     - Rank #1: WTI > $98 NO (edge +86pp, 4/20 结算)
     - Rank #2: Brent > $115 NO (edge +46pp, 4/20 结算)
     这两笔胜率 >95%，合起来 M$30 投入可期望回报 M$40+

  ⏳ 等待中:
     - Virginia 仓位结算 4/23 00:59 UTC
     - 若 NO 赢 (11.2% 概率): 立即执行 VIRGINIA_NO_WINS 触发的 playbook

  🛑 不要做:
     - 不要 panic-sell Virginia（HOLD EV > SELL EV by ~M$10）
     - 不要对公开事件市场反向押大仓（除非有 3+ 独立信源）
     - 不要体育 ML 押注（Vegas 效率太高）
""")


if __name__ == "__main__":
    main()
