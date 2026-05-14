"""
Decision Scoring System — 决策结果跟踪 + 校准评分 + 自学习权重
===============================================================
融合 strategy_auditor.py。每次决策从 audit → record → resolve → score → learn。

核心指标：
  1. Brier Score: (p - outcome)²  校准度
  2. Calibration Curve: 分 10 档置信区间，看 hit rate
  3. Bias Precision: 当 auditor 触发某偏差时，这笔交易失败概率
  4. Inertia Predictive Power: inertia_score 与实际损失的相关性
  5. Verdict Backtest: GO / REDUCE / NO-GO 的事后表现

自学习机制（与 Wolf Hour 文章最大区别）：
  - 每次偏差触发后失败 → bias_weight += 0.05
  - 每次偏差触发后成功 → bias_weight -= 0.02
  - 新 audit 时，bias 权重用历史数据加权
  - 长期下来，真正有用的偏差检测器会越来越被听取

用法:
  python decision_scoring.py record --audit-id 1                    # 从 audit 创建 decision
  python decision_scoring.py resolve --dec-id 3 --actual YES --pnl 437
  python decision_scoring.py report
  python decision_scoring.py calibration
  python decision_scoring.py biases
  python decision_scoring.py weights                                 # 查看学习后的偏差权重
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "audit_log.db"


# ─── Schema 演化 ───────────────────────────────────────────────────────

def ensure_schema():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audit_id INTEGER,
            market_id TEXT NOT NULL,
            outcome_side TEXT NOT NULL,        -- YES / NO
            my_prob REAL NOT NULL,
            market_prob REAL NOT NULL,
            amount REAL NOT NULL,
            verdict TEXT NOT NULL,             -- GO / REDUCE / NO-GO
            inertia_score REAL NOT NULL,
            biases_triggered TEXT NOT NULL,    -- JSON list of bias names
            biases_high TEXT NOT NULL,         -- JSON list of high-severity bias names
            decision_time TEXT NOT NULL,
            resolved INTEGER DEFAULT 0,
            actual_outcome TEXT,               -- YES / NO / NULL
            pnl REAL,                          -- profit (+) / loss (-) in M$
            resolve_time TEXT,
            brier REAL,                        -- (my_prob_for_actual - 1)²
            hit INTEGER,                       -- 1 if we won, 0 if lost, NULL if didn't bet
            FOREIGN KEY (audit_id) REFERENCES audits(id)
        );
        CREATE TABLE IF NOT EXISTS bias_weights (
            name TEXT PRIMARY KEY,
            weight REAL DEFAULT 1.0,           -- 初始 1.0，最低 0.1，最高 2.0
            total_triggered INTEGER DEFAULT 0,
            triggered_and_lost INTEGER DEFAULT 0,
            triggered_and_won INTEGER DEFAULT 0,
            last_updated TEXT
        );
        CREATE TABLE IF NOT EXISTS running_pnl (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            bankroll REAL NOT NULL,
            delta REAL NOT NULL,
            source TEXT                        -- decision id or 'deposit' / 'adj'
        );
    """)
    conn.commit()
    conn.close()


# ─── 记录决策（从 audit 派生） ──────────────────────────────────────────

def record_from_audit(audit_id: int, amount_override: float | None = None):
    """从 audits 表拉最新 report，创建 decision 记录"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, market_id, outcome, verdict, score, amount, my_prob, mkt_prob, timestamp, report_json FROM audits WHERE id = ?", (audit_id,))
    row = c.fetchone()
    if not row:
        print(f"❌ audit_id={audit_id} 不存在")
        conn.close()
        sys.exit(1)

    aid, market_id, outcome, verdict, score, amount, my_prob, mkt_prob, ts, report_json = row
    report = json.loads(report_json)

    biases_triggered = [b["name"] for b in report.get("bias_checks", []) if b.get("triggered")]
    biases_high = [b["name"] for b in report.get("bias_checks", []) if b.get("triggered") and b.get("severity") == "high"]

    final_amount = amount_override if amount_override is not None else amount

    c.execute("""
        INSERT INTO decisions
        (audit_id, market_id, outcome_side, my_prob, market_prob, amount,
         verdict, inertia_score, biases_triggered, biases_high, decision_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        aid, market_id, outcome, my_prob, mkt_prob, final_amount,
        verdict, score, json.dumps(biases_triggered), json.dumps(biases_high),
        datetime.now(timezone.utc).isoformat()
    ))
    dec_id = c.lastrowid
    conn.commit()
    conn.close()
    print(f"✓ decision_id={dec_id} 已记录: {market_id} {outcome} M${final_amount} ({verdict})")
    return dec_id


# ─── 决议 ───────────────────────────────────────────────────────────────

def resolve(dec_id: int, actual: str, pnl: float, note: str = ""):
    """标记决策结果。actual=YES/NO，pnl=利润或亏损（M$）"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT market_id, outcome_side, my_prob, amount, verdict FROM decisions WHERE id = ?", (dec_id,))
    row = c.fetchone()
    if not row:
        print(f"❌ decision_id={dec_id} 不存在")
        conn.close()
        sys.exit(1)

    market_id, outcome_side, my_prob, amount, verdict = row
    actual = actual.upper()

    # Brier: 我对「真实结果」的概率分配
    # 如果我押 YES，my_prob 是我对 P(YES)
    # 如果实际 YES: brier = (my_prob - 1)²
    # 如果实际 NO: brier = my_prob²
    # 如果我押 NO，my_prob 原来是我的 NO 价值分配（审计中已处理）
    if outcome_side == "YES":
        p_of_actual = my_prob if actual == "YES" else (1 - my_prob)
    else:  # NO
        p_of_actual = my_prob if actual == "NO" else (1 - my_prob)
    brier = (1 - p_of_actual) ** 2

    hit = 1 if pnl > 0 else 0

    c.execute("""
        UPDATE decisions
        SET resolved=1, actual_outcome=?, pnl=?, resolve_time=?, brier=?, hit=?
        WHERE id=?
    """, (actual, pnl, datetime.now(timezone.utc).isoformat(), brier, hit, dec_id))

    # 更新 running_pnl
    c.execute("SELECT bankroll FROM running_pnl ORDER BY id DESC LIMIT 1")
    prev = c.fetchone()
    new_bankroll = (prev[0] if prev else 50.0) + pnl
    c.execute("INSERT INTO running_pnl (ts, bankroll, delta, source) VALUES (?, ?, ?, ?)",
              (datetime.now(timezone.utc).isoformat(), new_bankroll, pnl, f"dec_{dec_id}"))

    conn.commit()
    conn.close()

    # 触发偏差权重学习
    _update_bias_weights(dec_id)

    status = "✓ 赢" if hit else "✗ 输"
    print(f"{status} decision_id={dec_id} | PnL={pnl:+.2f} M$ | Brier={brier:.4f} | bankroll={new_bankroll:.2f}")


def _update_bias_weights(dec_id: int):
    """根据最新决策结果，更新偏差权重（自学习核心）"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT biases_triggered, hit FROM decisions WHERE id=?", (dec_id,))
    row = c.fetchone()
    if not row or row[1] is None:
        conn.close()
        return

    biases, hit = json.loads(row[0]), row[1]
    now = datetime.now(timezone.utc).isoformat()

    for bias in biases:
        c.execute("SELECT weight, total_triggered, triggered_and_lost, triggered_and_won FROM bias_weights WHERE name=?", (bias,))
        r = c.fetchone()
        if r is None:
            c.execute("INSERT INTO bias_weights (name, weight, total_triggered, triggered_and_lost, triggered_and_won, last_updated) VALUES (?, ?, ?, ?, ?, ?)",
                      (bias, 1.0, 0, 0, 0, now))
            w, t, l, wn = 1.0, 0, 0, 0
        else:
            w, t, l, wn = r

        # 权重调整：输了增加警示力（0.05），赢了降低（0.02）
        delta = 0.05 if hit == 0 else -0.02
        new_w = max(0.1, min(2.0, w + delta))
        t += 1
        if hit == 0:
            l += 1
        else:
            wn += 1

        c.execute("UPDATE bias_weights SET weight=?, total_triggered=?, triggered_and_lost=?, triggered_and_won=?, last_updated=? WHERE name=?",
                  (new_w, t, l, wn, now, bias))

    conn.commit()
    conn.close()


# ─── 报告视图 ────────────────────────────────────────────────────────────

def report_overall():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(hit), SUM(pnl), AVG(brier), AVG(inertia_score) FROM decisions WHERE resolved=1")
    total, wins, pnl, avg_brier, avg_inertia = c.fetchone()
    total = total or 0
    wins = wins or 0
    pnl = pnl or 0

    c.execute("SELECT COUNT(*) FROM decisions WHERE resolved=0")
    pending = c.fetchone()[0]

    c.execute("SELECT bankroll FROM running_pnl ORDER BY id DESC LIMIT 1")
    br = c.fetchone()
    bankroll = br[0] if br else "—"

    print(f"\n{'='*60}")
    print(f"  决策评分报告 — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    print(f"  总决策数:   {total + pending} ({pending} pending)")
    print(f"  已决议:     {total}")
    if total > 0:
        hit_rate = 100 * wins / total
        print(f"  胜率:       {wins}/{total} = {hit_rate:.1f}%")
        print(f"  累积 PnL:   {pnl:+.2f} M$")
        print(f"  平均 Brier: {avg_brier:.4f}  (越低越准, 0=完美, 0.25=coinflip)")
        print(f"  平均 Inertia: {avg_inertia*100:.1f}%")
        print(f"  当前 bankroll: {bankroll}")

    # Verdict breakdown
    print(f"\n  ── Verdict 分布 ──")
    c.execute("SELECT verdict, COUNT(*), SUM(hit), SUM(pnl) FROM decisions WHERE resolved=1 GROUP BY verdict")
    for v, n, w, p in c.fetchall():
        print(f"    {v:10} n={n} wins={w or 0} pnl={(p or 0):+.2f}")

    conn.close()


def calibration():
    """校准曲线：我说 70% 赢的，真实赢率是多少？"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT my_prob, hit FROM decisions WHERE resolved=1")
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("暂无已决议决策")
        return

    buckets = {(i*10, (i+1)*10): [] for i in range(10)}
    for p, h in rows:
        bucket_key = (int(p*10)*10, (int(p*10)+1)*10)
        if bucket_key in buckets:
            buckets[bucket_key].append(h)

    print(f"\n  校准曲线 (n={len(rows)})")
    print(f"  {'区间':12} {'样本':>6} {'宣称概率':>8} {'实际胜率':>8} {'偏差':>8}")
    for (lo, hi), hits in buckets.items():
        if not hits:
            continue
        claimed = (lo + hi) / 2 / 100
        actual = sum(hits) / len(hits)
        diff = actual - claimed
        bar = "█" * int(actual * 30)
        print(f"  [{lo:>3}-{hi:>3}%]  n={len(hits):>3}  {claimed:.2f}     {actual:.2f}     {diff:+.3f}  {bar}")


def bias_report():
    """每个偏差：触发后损失率 + 权重"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name, weight, total_triggered, triggered_and_lost, triggered_and_won FROM bias_weights ORDER BY weight DESC")
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("暂无偏差数据（需要先 resolve 几笔决策）")
        return

    print(f"\n  偏差预测力分析")
    print(f"  {'偏差':28} {'权重':>6} {'触发次数':>8} {'输':>4} {'赢':>4} {'失败率':>8}")
    for name, w, t, l, wn in rows:
        loss_rate = l / t if t > 0 else 0
        flag = "🔴" if w > 1.3 else ("🟡" if w > 1.0 else "🟢")
        print(f"  {flag} {name:28} {w:.2f}   {t:>4}   {l:>4}   {wn:>4}   {loss_rate*100:>6.1f}%")


def pnl_curve():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT ts, bankroll, delta, source FROM running_pnl ORDER BY id")
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("暂无 PnL 记录")
        return

    print(f"\n  Bankroll 演化曲线")
    print(f"  {'时间':20} {'bankroll':>10} {'变动':>10} {'来源':12}")
    for ts, br, d, s in rows:
        ts_short = ts[:19].replace("T", " ")
        bar = "█" * min(30, int(br / 50))
        print(f"  {ts_short}  {br:>8.2f}    {d:+>+8.2f}  {s:10}  {bar}")


def weights_view():
    bias_report()
    print(f"\n  ─ 说明 ─")
    print(f"  权重 > 1.3 🔴 = 这个偏差触发时，你的下注历史显示高失败率，强烈警告")
    print(f"  权重 < 1.0 🟢 = 这个偏差可能过度警报，审计时应降低影响")
    print(f"  新 audit 运行时，strategy_auditor.py 可读这些权重调整 inertia_score")


# ─── CLI ──────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="从 audit 创建 decision")
    r.add_argument("--audit-id", type=int, required=True)
    r.add_argument("--amount", type=float, default=None, help="实际下注金额（可覆盖 audit 中的原计划）")

    rv = sub.add_parser("resolve", help="标记决策结果")
    rv.add_argument("--dec-id", type=int, required=True)
    rv.add_argument("--actual", choices=["YES", "NO", "yes", "no"], required=True)
    rv.add_argument("--pnl", type=float, required=True, help="利润（+）或亏损（-），单位 M$")
    rv.add_argument("--note", default="")

    sub.add_parser("report", help="总览")
    sub.add_parser("calibration", help="校准曲线")
    sub.add_parser("biases", help="偏差预测力")
    sub.add_parser("pnl", help="bankroll 演化")
    sub.add_parser("weights", help="学习后的偏差权重")

    args = p.parse_args()
    ensure_schema()

    if args.cmd == "record":
        record_from_audit(args.audit_id, args.amount)
    elif args.cmd == "resolve":
        resolve(args.dec_id, args.actual, args.pnl, args.note)
    elif args.cmd == "report":
        report_overall()
    elif args.cmd == "calibration":
        calibration()
    elif args.cmd == "biases":
        bias_report()
    elif args.cmd == "pnl":
        pnl_curve()
    elif args.cmd == "weights":
        weights_view()


if __name__ == "__main__":
    main()
