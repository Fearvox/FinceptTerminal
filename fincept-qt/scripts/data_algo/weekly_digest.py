"""
Weekly Digest — 每日/每周交易决策简报
============================================
聚合所有子系统输出，形成一个 single pane of glass：

  1. 当前 bankroll + 未决仓位
  2. 过去 24h/7d 的 PnL + Brier + 胜率
  3. 未结算决策（等待 resolve）
  4. 未处理候选（按 severity 排名）
  5. 学习权重 top-5 警戒偏差
  6. 接下来 7 天财报/事件日历（可交易）

用法:
  python weekly_digest.py
  python weekly_digest.py --days 7
"""
import argparse
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

AUDIT_DB = Path(__file__).parent / "audit_log.db"
SCAN_DB = Path(__file__).parent / "wolf_scanner.db"


def section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def digest(days: int = 7):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    print(f"\n📊 交易简报  —  {now.strftime('%Y-%m-%d %H:%M UTC')}  (回看 {days} 天)")

    # ── 1. Bankroll + pending
    section("1. Bankroll & 未结算")
    conn = sqlite3.connect(AUDIT_DB)
    c = conn.cursor()
    c.execute("SELECT bankroll FROM running_pnl ORDER BY id DESC LIMIT 1")
    br = c.fetchone()
    bankroll = br[0] if br else "—"
    print(f"  当前 bankroll: M${bankroll}")

    c.execute("""SELECT id, market_id, outcome_side, my_prob, amount, verdict, decision_time
                 FROM decisions WHERE resolved=0""")
    pending = c.fetchall()
    print(f"  未结算决策: {len(pending)}")
    for p in pending:
        did, mid, side, mp, amt, v, dt = p
        dt_short = dt[:10] if dt else "?"
        print(f"    dec_{did}  {side} M${amt}  {v}  @ {mp:.2f}  {dt_short}  {mid[:50]}")

    # ── 2. 近期表现
    section(f"2. 近 {days} 天表现")
    c.execute("""SELECT COUNT(*), SUM(hit), SUM(pnl), AVG(brier)
                 FROM decisions WHERE resolved=1 AND resolve_time > ?""", (cutoff.isoformat(),))
    n, w, pnl, brier = c.fetchone()
    n = n or 0
    if n == 0:
        print("  无已结算决策")
    else:
        hit_rate = 100 * (w or 0) / n
        print(f"  决策数: {n}")
        print(f"  胜率: {w or 0}/{n} = {hit_rate:.1f}%")
        print(f"  PnL: {(pnl or 0):+.2f} M$")
        print(f"  平均 Brier: {(brier or 0):.4f}")

    # ── 3. 偏差权重 top 警戒
    section("3. 偏差权重 Top 5（权重 > 1 为历史失败关联）")
    c.execute("""SELECT name, weight, total_triggered, triggered_and_lost
                 FROM bias_weights WHERE weight > 1.0 ORDER BY weight DESC LIMIT 5""")
    rows = c.fetchall()
    if not rows:
        print("  (暂无高权重偏差 — 样本不足)")
    else:
        for name, w, t, l in rows:
            flag = "🔴" if w > 1.3 else "🟡"
            print(f"  {flag} {name:28}  权重 {w:.2f}   触发 {t}  输 {l}")
    conn.close()

    # ── 4. 候选池 top-10
    section(f"4. 未处理候选（近 {days*24}h，按严重度 × 流动性）")
    try:
        sc = sqlite3.connect(SCAN_DB)
        cc = sc.cursor()
        # 加 dismissed 列防 schema 错
        try:
            cc.execute("ALTER TABLE candidates ADD COLUMN dismissed INTEGER DEFAULT 0")
            sc.commit()
        except sqlite3.OperationalError:
            pass
        cc.execute("""SELECT id, slug, signal, severity, market_prob, liquidity, evidence, action_hint
                      FROM candidates WHERE dismissed=0 AND ts > ?
                      ORDER BY severity DESC, liquidity DESC LIMIT 10""", (cutoff.isoformat(),))
        rows = cc.fetchall()
        if not rows:
            print("  (候选池为空，跑 scanner 刷新)")
        else:
            print(f"  {'ID':>3} {'SEV':>3} {'信号':20} {'液':>6} {'价':>5}  {'slug':40}")
            for r in rows:
                cid, slug, sig, sev, mp, liq, ev, _ = r
                print(f"  {cid:>3} {sev:>3} {sig[:20]:20} {(liq or 0):>6.0f} {(mp or 0):>5.2f}  {slug[:40]}")
        sc.close()
    except Exception as e:
        print(f"  ⚠️ scanner DB: {e}")

    # ── 5. 下周建议行动
    section("5. 建议执行清单")
    print("""  [ ] 跑 wolf_hour_scanner.py scan --only-wolf （02:30-04:00 UTC）
  [ ] 跑 earnings_reaction_scanner.py scan --days 7
  [ ] 检查 pending 决策（Virginia 4/21-23）是否已结算 → decision_scoring.py resolve
  [ ] 回顾 candidates_view.py list --min-sev 2
  [ ] 看 weights 权重漂移 → decision_scoring.py weights""")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    args = p.parse_args()
    digest(args.days)


if __name__ == "__main__":
    main()
