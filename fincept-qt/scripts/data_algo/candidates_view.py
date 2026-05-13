"""
Candidates View — 统一查看所有 scanner 产出的候选
========================================================
聚合 wolf_hour_scanner + earnings_reaction_scanner + (未来 commodity/political)
按 severity × edge 排序，显示 action_hint，支持按时间/信号类型过滤。

用法:
  python candidates_view.py list                       # 所有未处理候选
  python candidates_view.py list --signal earnings     # 只看财报类
  python candidates_view.py list --min-sev 3           # 只看高严重度
  python candidates_view.py dismiss --id 5             # 标记已看过
  python candidates_view.py stats                      # 各 scanner 命中率
"""
import argparse
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "wolf_scanner.db"


def ensure_dismissed_col():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("ALTER TABLE candidates ADD COLUMN dismissed INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.close()


def list_candidates(signal=None, min_sev=1, hours=48, limit=30):
    ensure_dismissed_col()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    q = """SELECT id, ts, slug, signal, severity, market_prob, liquidity, evidence, action_hint
           FROM candidates
           WHERE dismissed=0 AND severity >= ? AND ts > ?"""
    params = [min_sev, (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()]
    if signal:
        q += " AND signal LIKE ?"
        params.append(f"%{signal}%")
    q += " ORDER BY severity DESC, id DESC LIMIT ?"
    params.append(limit)

    c.execute(q, params)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("  (无候选)")
        return

    print(f"\n  {'ID':>4} {'SEV':>4} {'信号':18} {'液':>6} {'市价':>6} {'evidence':40} {'slug'}")
    print(f"  {'-'*4} {'-'*4} {'-'*18} {'-'*6} {'-'*6} {'-'*40} {'-'*30}")
    for row in rows:
        id_, ts, slug, sig, sev, mp, liq, ev, hint = row
        sev_flag = "🔴" if sev >= 4 else ("🟡" if sev >= 2 else "🟢")
        mp_str = f"{mp:.2f}" if mp else "?"
        liq_str = f"{liq:.0f}" if liq else "?"
        print(f"  {id_:>4} {sev_flag}{sev:>2} {sig[:18]:18} {liq_str:>6} {mp_str:>6} {ev[:40]:40} {slug[:50]}")
        if hint:
            print(f"       → {hint[:200]}")


def dismiss(cid):
    ensure_dismissed_col()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE candidates SET dismissed=1 WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    print(f"✓ 候选 {cid} 已标记已处理")


def stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT signal, COUNT(*), AVG(severity), AVG(liquidity) FROM candidates GROUP BY signal ORDER BY COUNT(*) DESC")
    rows = c.fetchall()
    conn.close()

    print(f"\n  信号类型分布")
    print(f"  {'signal':25} {'n':>5} {'avg_sev':>8} {'avg_liq':>10}")
    for sig, n, s, l in rows:
        print(f"  {sig:25} {n:>5} {s:>8.2f} {l:>10.0f}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("list")
    l.add_argument("--signal")
    l.add_argument("--min-sev", type=int, default=1)
    l.add_argument("--hours", type=int, default=48)
    l.add_argument("--limit", type=int, default=30)

    d = sub.add_parser("dismiss")
    d.add_argument("--id", type=int, required=True)

    sub.add_parser("stats")

    args = p.parse_args()
    if args.cmd == "list":
        list_candidates(args.signal, args.min_sev, args.hours, args.limit)
    elif args.cmd == "dismiss":
        dismiss(args.id)
    elif args.cmd == "stats":
        stats()


if __name__ == "__main__":
    main()
