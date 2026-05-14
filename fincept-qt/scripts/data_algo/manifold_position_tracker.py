"""
Manifold Position Tracker — 正确的 mark-to-market 计算
========================================================
修正之前错误的 "价差百分比" 分析方法。
在 Manifold CPMM 上，mark-to-market = shares × current_side_price

用法:
  python manifold_position_tracker.py scan --username 0xVox
  python manifold_position_tracker.py detail --market R96CQ5gEcn --username 0xVox
"""
import argparse
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MANIFOLD = "https://api.manifold.markets/v0"
UA = {"User-Agent": "Mozilla/5.0"}
DB_PATH = Path(__file__).parent / "audit_log.db"


def _req(url, timeout=10):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())


def get_all_bets(username: str, limit: int = 200) -> list:
    try:
        return _req(f"{MANIFOLD}/bets?username={username}&limit={limit}")
    except Exception as e:
        print(f"❌ bets: {e}")
        return []


def get_market(contract_id: str) -> dict:
    try:
        return _req(f"{MANIFOLD}/market/{contract_id}")
    except Exception:
        return {}


def compute_position(contract_id: str, username: str) -> dict:
    """
    拉用户在特定市场的所有 bets，汇总成净仓位 + mark-to-market
    """
    bets = _req(f"{MANIFOLD}/bets?username={username}&contractId={contract_id}")
    if not bets:
        return {"error": "no bets"}

    market = get_market(contract_id)
    yes_prob = market.get("probability")
    if yes_prob is None:
        return {"error": "market probability missing"}

    no_price = 1 - yes_prob
    net_yes_shares = sum(b.get("shares", 0) for b in bets if b.get("outcome") == "YES")
    net_no_shares = sum(b.get("shares", 0) for b in bets if b.get("outcome") == "NO")
    total_invested = sum(b.get("amount", 0) for b in bets)

    # Mark-to-market: each share pays $1 if its side wins
    mtm_yes = net_yes_shares * yes_prob
    mtm_no = net_no_shares * no_price
    mtm = mtm_yes + mtm_no
    pnl = mtm - total_invested
    pnl_pct = pnl / total_invested * 100 if total_invested else 0

    # Leverage (shares / invested per side)
    primary_side = "YES" if net_yes_shares > net_no_shares else "NO"
    primary_shares = net_yes_shares if primary_side == "YES" else net_no_shares
    leverage = primary_shares / total_invested if total_invested else 0

    # Payout scenarios
    payout_yes = net_yes_shares
    payout_no = net_no_shares
    ev_at_resolution = yes_prob * payout_yes + no_price * payout_no

    return {
        "market_question": market.get("question"),
        "market_slug": market.get("slug"),
        "contractId": contract_id,
        "current_yes_prob": yes_prob,
        "primary_side": primary_side,
        "shares": {"YES": net_yes_shares, "NO": net_no_shares},
        "total_invested": total_invested,
        "mark_to_market": mtm,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "leverage": leverage,
        "payout_if_yes": payout_yes,
        "payout_if_no": payout_no,
        "ev_at_resolution": ev_at_resolution,
        "market_close": market.get("closeTime"),
        "total_liquidity": market.get("totalLiquidity"),
    }


def scan_user_positions(username: str):
    bets = get_all_bets(username)
    contracts = {}
    for b in bets:
        cid = b.get("contractId")
        if cid not in contracts:
            contracts[cid] = []
        contracts[cid].append(b)

    print(f"\n📊 {username} 全部仓位 ({len(contracts)} 个市场)\n")
    total_invested = 0
    total_mtm = 0
    total_ev = 0

    positions = []
    for cid in contracts:
        p = compute_position(cid, username)
        if "error" in p:
            continue
        positions.append(p)
        total_invested += p["total_invested"]
        total_mtm += p["mark_to_market"]
        total_ev += p["ev_at_resolution"]

    # 按 |pnl| 排序
    positions.sort(key=lambda p: -abs(p["pnl"]))

    print(f"  {'市场':35} {'方向':>4} {'shares':>8} {'投入':>7} {'MTM':>7} {'PnL':>8} {'leverage':>8}")
    for p in positions:
        q = p["market_question"][:35] if p["market_question"] else "?"
        side = p["primary_side"]
        sh = p["shares"][side]
        color = "🟢" if p["pnl"] >= 0 else "🔴"
        print(f"  {color} {q:35} {side:>4} {sh:>8.0f} M${p['total_invested']:>5.1f} M${p['mark_to_market']:>5.1f} {p['pnl']:>+7.2f} {p['leverage']:>6.1f}x")

    print(f"\n  ═══ 汇总 ═══")
    print(f"  总投入:       M${total_invested:.2f}")
    print(f"  当前 MTM:     M${total_mtm:.2f}")
    print(f"  净损益:       M${total_mtm - total_invested:+.2f} ({(total_mtm-total_invested)/total_invested*100 if total_invested else 0:+.1f}%)")
    print(f"  Resolution EV: M${total_ev:.2f}")


def detail(contract_id: str, username: str):
    p = compute_position(contract_id, username)
    if "error" in p:
        print(f"❌ {p['error']}")
        return
    print(f"\n📊 仓位详情")
    print(f"  市场: {p['market_question']}")
    print(f"  市场 slug: {p['market_slug']}")
    print(f"  当前 YES 概率: {p['current_yes_prob']:.4f}")
    print(f"  当前 NO 价格:  {1 - p['current_yes_prob']:.4f}")
    print(f"\n  我方仓位:")
    print(f"    主方向: {p['primary_side']}")
    print(f"    YES shares: {p['shares']['YES']:.2f}")
    print(f"    NO shares:  {p['shares']['NO']:.2f}")
    print(f"    总投入:    M${p['total_invested']:.2f}")
    print(f"    Leverage:  {p['leverage']:.2f}x (shares / invested)")
    print(f"\n  估值:")
    print(f"    Mark-to-market:  M${p['mark_to_market']:.2f}")
    print(f"    未实现 PnL:      M${p['pnl']:+.2f} ({p['pnl_pct']:+.1f}%)")
    print(f"\n  结算情景:")
    print(f"    若 YES resolve: 拿 M${p['payout_if_yes']:.2f} ({p['current_yes_prob']*100:.1f}% 概率)")
    print(f"    若 NO  resolve: 拿 M${p['payout_if_no']:.2f} ({(1-p['current_yes_prob'])*100:.1f}% 概率)")
    print(f"    期望值:          M${p['ev_at_resolution']:.2f}")
    print(f"\n  决策建议:")
    hold_ev = p["ev_at_resolution"]
    sell_now = p["mark_to_market"] * 0.80   # 假设 20% AMM 滑点
    if hold_ev > sell_now:
        print(f"    ✅ HOLD  (hold EV M${hold_ev:.2f} > sell-now ~M${sell_now:.2f})")
    else:
        print(f"    ⚠️ SELL (sell-now ~M${sell_now:.2f} > hold EV M${hold_ev:.2f})")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("--username", required=True)
    d = sub.add_parser("detail"); d.add_argument("--market", required=True); d.add_argument("--username", required=True)
    args = p.parse_args()
    if args.cmd == "scan":
        scan_user_positions(args.username)
    elif args.cmd == "detail":
        detail(args.market, args.username)


if __name__ == "__main__":
    main()
