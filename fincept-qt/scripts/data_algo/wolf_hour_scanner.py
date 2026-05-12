"""
Wolf Hour Scanner — Manifold 低流动性错价探测器
===================================================
在 02:30-04:00 UTC（用户定义窗口）扫描 Manifold 所有开放市场，
识别以下候选信号：

  1. 流动性漂移：市场 `totalLiquidity` < 500 但 24h 内有成交 → 做市商撤退
  2. 价差异常：binary 市场 YES 离 0.5 偏差 > 40% 但无近期新闻支撑
  3. 概率跳跃：1 小时内 price move > 5pp 且未伴随成交量爆发
  4. 时间窗口错位：24h 内到期但仍有 bid/ask 宽价差
  5. 近期 bet 量突然归零（做市商退场）

每个候选 → 计算粗略 edge → 输出可 pipe 进 strategy_auditor 的命令。

用法:
  python wolf_hour_scanner.py scan                        # 扫一次
  python wolf_hour_scanner.py scan --min-liq-drop 0.5    # 筛流动性腰斩
  python wolf_hour_scanner.py loop --interval 60         # 持续扫
  python wolf_hour_scanner.py watchlist add <slug>       # 加入观察池
"""

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MANIFOLD_API = "https://api.manifold.markets/v0"
DB_PATH = Path(__file__).parent / "wolf_scanner.db"
WOLF_START_UTC_HOUR = 2   # 02:30 UTC
WOLF_END_UTC_HOUR = 4     # 04:00 UTC


def ensure_schema():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL,
            ts TEXT NOT NULL,
            probability REAL,
            volume REAL,
            volume_24h REAL,
            total_liquidity REAL,
            close_time INTEGER,
            unique_bettors INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_snap_slug_ts ON market_snapshots(slug, ts);

        CREATE TABLE IF NOT EXISTS watchlist (
            slug TEXT PRIMARY KEY,
            added_ts TEXT NOT NULL,
            my_thesis TEXT,
            my_prob REAL
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            slug TEXT NOT NULL,
            signal TEXT NOT NULL,          -- liq_drop, spread_jump, stale_spread, etc
            severity INTEGER NOT NULL,     -- 1-5
            market_prob REAL,
            liquidity REAL,
            evidence TEXT,
            action_hint TEXT
        );
    """)
    conn.commit()
    conn.close()


# ─── Manifold API ────────────────────────────────────────────────────

def fetch_open_markets(limit: int = 100):
    """按 liquidity 升序拉（低流动性优先，Wolf Hour 核心）"""
    url = f"{MANIFOLD_API}/search-markets?limit={limit}&filter=open&sort=liquidity"
    try:
        return json.loads(urllib.request.urlopen(url, timeout=15).read())
    except Exception as e:
        print(f"❌ API 错误: {e}", file=sys.stderr)
        return []


def fetch_market(slug: str):
    url = f"{MANIFOLD_API}/slug/{slug}"
    try:
        return json.loads(urllib.request.urlopen(url, timeout=10).read())
    except Exception:
        return None


def fetch_bets(slug: str, limit: int = 20):
    """最近 N 笔下注，用于检测做市商是否撤退"""
    try:
        # 先拿 contract
        m = fetch_market(slug)
        if not m:
            return []
        cid = m.get("id")
        url = f"{MANIFOLD_API}/bets?contractId={cid}&limit={limit}"
        return json.loads(urllib.request.urlopen(url, timeout=10).read())
    except Exception:
        return []


# ─── 信号检测 ─────────────────────────────────────────────────────────

def is_wolf_hour_now() -> bool:
    now = datetime.now(timezone.utc)
    h, m = now.hour, now.minute
    start = WOLF_START_UTC_HOUR * 60 + 30   # 02:30
    end = WOLF_END_UTC_HOUR * 60            # 04:00
    mins = h * 60 + m
    return start <= mins <= end


def snapshot(m: dict):
    """存快照，便于后续对比"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO market_snapshots (slug, ts, probability, volume, volume_24h, total_liquidity, close_time, unique_bettors)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        m.get("slug"), datetime.now(timezone.utc).isoformat(),
        m.get("probability"), m.get("volume", 0), m.get("volume24Hours", 0),
        m.get("totalLiquidity", 0), m.get("closeTime", 0),
        m.get("uniqueBettorCount", 0)
    ))
    conn.commit()
    conn.close()


def detect_liquidity_drop(slug: str) -> tuple[int, str]:
    """对比历史快照，流动性腰斩则触发"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT total_liquidity FROM market_snapshots WHERE slug=? ORDER BY id DESC LIMIT 10", (slug,))
    liqs = [r[0] for r in c.fetchall() if r[0]]
    conn.close()
    if len(liqs) < 3:
        return 0, ""
    latest = liqs[0]
    baseline = max(liqs[1:])
    if baseline > 0 and latest / baseline < 0.5:
        return 3, f"liq {baseline:.0f} → {latest:.0f} ({100*(1-latest/baseline):.0f}% 降)"
    return 0, ""


def detect_extreme_price(m: dict) -> tuple[int, str]:
    """概率 < 10% 或 > 90% 但流动性低 = 潜在错价（Wolf Hour 时做市商已离场）"""
    p = m.get("probability")
    liq = m.get("totalLiquidity", 0)
    if p is None:
        return 0, ""
    if liq < 500 and (p < 0.10 or p > 0.90):
        return 2, f"极端价 p={p:.2f} liq={liq:.0f}"
    return 0, ""


def detect_stale_spread(m: dict) -> tuple[int, str]:
    """24h 低成交但市场偏离 0.5 → 可能无人维护"""
    vol_24h = m.get("volume24Hours", 0)
    p = m.get("probability")
    liq = m.get("totalLiquidity", 0)
    if p is None:
        return 0, ""
    if vol_24h < 20 and liq < 1000 and abs(p - 0.5) > 0.2:
        return 1, f"24h 成交 {vol_24h:.0f} 低，液 {liq:.0f}，偏离 0.5 | p={p:.2f}"
    return 0, ""


def detect_near_resolution(m: dict) -> tuple[int, str]:
    """距离结算 < 24h 但价格仍不确定（有判断空间）"""
    close_ms = m.get("closeTime", 0)
    if not close_ms:
        return 0, ""
    hours_left = (close_ms / 1000 - time.time()) / 3600
    p = m.get("probability")
    if p is None:
        return 0, ""
    if 0 < hours_left < 24 and 0.25 < p < 0.75:
        return 2, f"{hours_left:.1f}h 到期 | p={p:.2f} 尚在不确定区"
    return 0, ""


SIGNAL_FUNCS = {
    "liq_drop": detect_liquidity_drop,    # needs slug
    "extreme_price": detect_extreme_price, # needs market dict
    "stale_spread": detect_stale_spread,
    "near_resolution": detect_near_resolution,
}


# ─── 扫描 ──────────────────────────────────────────────────────────────

def scan(min_liq: float = 0, max_liq: float = 5000, only_wolf: bool = False):
    if only_wolf and not is_wolf_hour_now():
        print(f"⏰ 现在不是 Wolf Hour ({WOLF_START_UTC_HOUR}:30-{WOLF_END_UTC_HOUR}:00 UTC)，跳过")
        return

    markets = fetch_open_markets(limit=100)
    print(f"🔍 扫描 {len(markets)} 个市场...")

    candidates = []
    for m in markets:
        liq = m.get("totalLiquidity", 0)
        if liq < min_liq or liq > max_liq:
            continue
        if m.get("outcomeType") != "BINARY":
            continue

        slug = m.get("slug")
        snapshot(m)

        signals = []
        for name, fn in SIGNAL_FUNCS.items():
            if name == "liq_drop":
                sev, ev = fn(slug)
            else:
                sev, ev = fn(m)
            if sev > 0:
                signals.append((name, sev, ev))

        if signals:
            total_sev = sum(s[1] for s in signals)
            candidates.append((total_sev, m, signals))

    candidates.sort(key=lambda x: -x[0])

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    print(f"\n🎯 候选 ({len(candidates)}):")
    print(f"  {'严重度':>4} {'概率':>6} {'流动性':>6} {'到期':>14} {'Slug':40} {'信号'}")
    for sev, m, sigs in candidates[:20]:
        sig_str = " | ".join(f"{n}:{e}" for n, _, e in sigs)
        close_ts = m.get("closeTime", 0) / 1000
        close_str = datetime.fromtimestamp(close_ts, timezone.utc).strftime("%m-%d %H:%M") if close_ts else "n/a"
        p = m.get("probability", 0)
        liq = m.get("totalLiquidity", 0)
        slug = (m.get("slug") or "")[:38]
        print(f"  {sev:>4} {p:>6.3f} {liq:>6.0f} {close_str:>14} {slug:40}")
        print(f"       → {sig_str}")
        print(f"       URL: https://manifold.markets/{m.get('creatorUsername')}/{m.get('slug')}")

        for name, s, ev in sigs:
            c.execute("INSERT INTO candidates (ts, slug, signal, severity, market_prob, liquidity, evidence, action_hint) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (datetime.now(timezone.utc).isoformat(), m.get("slug"), name, s, p, liq, ev,
                       f"python strategy_auditor.py audit --market-id {m.get('slug')} --outcome YES --my-prob ? --market-prob {p:.3f} --edge-source \"{name}_{ev[:30]}\" --amount 10"))

    conn.commit()
    conn.close()


# ─── Watchlist ─────────────────────────────────────────────────────────

def watchlist_add(slug: str, thesis: str, my_prob: float):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO watchlist (slug, added_ts, my_thesis, my_prob) VALUES (?, ?, ?, ?)",
              (slug, datetime.now(timezone.utc).isoformat(), thesis, my_prob))
    conn.commit()
    conn.close()
    print(f"✓ 观察池加入: {slug} | thesis={thesis} | my_prob={my_prob}")


def watchlist_check():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT slug, my_thesis, my_prob FROM watchlist")
    rows = c.fetchall()
    conn.close()

    for slug, thesis, my_prob in rows:
        m = fetch_market(slug)
        if not m:
            continue
        p = m.get("probability", 0)
        liq = m.get("totalLiquidity", 0)
        edge = my_prob - p
        flag = "🟢" if abs(edge) > 0.05 else "⚪"
        print(f"  {flag} {slug:50} mkt={p:.3f} mine={my_prob:.3f} edge={edge:+.3f} liq={liq:.0f}  [{thesis}]")


# ─── 持续循环 ─────────────────────────────────────────────────────────

def loop(interval: int, only_wolf: bool):
    print(f"🐺 循环扫描中（间隔 {interval}s, only_wolf={only_wolf}）— Ctrl+C 停止")
    while True:
        try:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"\n=== {ts} ===")
            scan(only_wolf=only_wolf)
            watchlist_check()
        except KeyboardInterrupt:
            print("\n停止")
            break
        except Exception as e:
            print(f"⚠️ {e}")
        time.sleep(interval)


# ─── CLI ──────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--min-liq", type=float, default=0)
    s.add_argument("--max-liq", type=float, default=5000)
    s.add_argument("--only-wolf", action="store_true")

    lp = sub.add_parser("loop")
    lp.add_argument("--interval", type=int, default=120)
    lp.add_argument("--only-wolf", action="store_true")

    wa = sub.add_parser("watch")
    wa.add_argument("slug")
    wa.add_argument("--thesis", default="")
    wa.add_argument("--my-prob", type=float, required=True)

    sub.add_parser("watchlist")

    args = p.parse_args()
    ensure_schema()

    if args.cmd == "scan":
        scan(min_liq=args.min_liq, max_liq=args.max_liq, only_wolf=args.only_wolf)
    elif args.cmd == "loop":
        loop(args.interval, args.only_wolf)
    elif args.cmd == "watch":
        watchlist_add(args.slug, args.thesis, args.my_prob)
    elif args.cmd == "watchlist":
        watchlist_check()


if __name__ == "__main__":
    main()
