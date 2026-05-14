"""
Weather Arb Scanner — 自动记录天气预测市场候选
=======================================================

追踪 @AlterEgo_eth / @coldmath / @theyseemebuyingtheyhatin 类 playbook:
- 稳定城市 (Jeddah, Buenos Aires, Seoul, Tel Aviv, Cape Town) — ECMWF 收敛
- 避开 Spring Midwest convective chaos
- Ensemble agreement 高 → edge 可信
- 锚定 round-number 温度阈值 (25°C, 30°C, 35°C, 85°F, 90°F ...)

数据源:
- Open-Meteo Forecast API  (free, no key, ECMWF + GFS 组合)
- Open-Meteo Ensemble API  (free, 多模型 spread)

市场对接 (TODO):
- Polymarket gamma-api (adapter 待写)
- Kalshi /trade-api/v2/markets (weather 系列 ticker 待 probe)

用法:
  python3 weather_arb_scanner.py scan                        # 扫一次
  python3 weather_arb_scanner.py scan --format json          # JSON 输出
  python3 weather_arb_scanner.py loop --interval 1800        # 每 30 min 扫
  python3 weather_arb_scanner.py report --days 7             # 最近 7 天 edge 候选
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "weather_scanner.db"
OM_FORECAST = "https://api.open-meteo.com/v1/forecast"
OM_ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"

# City universe — 来自 @AlterEgo_eth / @Kropanchik 长文推荐
# (lat, lon, tz, label)
CITIES = {
    "jeddah":        (21.4858, 39.1925, "Asia/Riyadh",     "Jeddah (desert, ECMWF 精)"),
    "buenos_aires":  (-34.6037, -58.3816, "America/Argentina/Buenos_Aires", "Buenos Aires (stable autumn)"),
    "seoul":         (37.5665, 126.9780, "Asia/Seoul",     "Seoul (JMA 支援)"),
    "tel_aviv":      (32.0853, 34.7818, "Asia/Jerusalem",  "Tel Aviv (稳定)"),
    "cape_town":     (-33.9249, 18.4241, "Africa/Johannesburg", "Cape Town (coldmath 偏好)"),
    # Comparison: convective chaos — 用于对照组
    "chicago":       (41.8781, -87.6298, "America/Chicago", "Chicago (AVOID, 对流飘)"),
    "dallas":        (32.7767, -96.7970, "America/Chicago", "Dallas (AVOID, 对流飘)"),
}

# Round-number thresholds (°C) — @coldmath 风格锚点
ANCHOR_THRESHOLDS_C = [15, 20, 25, 30, 35, 40]


def ensure_schema() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS forecast_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,           -- YYYY-MM-DD 预测目标日
            high_c REAL,
            low_c REAL,
            high_f REAL,
            low_f REAL,
            source TEXT NOT NULL                 -- 'forecast' or 'ensemble_median'
        );
        CREATE INDEX IF NOT EXISTS idx_fc_city_date ON forecast_snapshots(city, target_date);

        CREATE TABLE IF NOT EXISTS ensemble_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            members INTEGER NOT NULL,
            high_c_median REAL,
            high_c_stdev REAL,
            agreement_score REAL                 -- 0-1, 越高越一致
        );
        CREATE INDEX IF NOT EXISTS idx_ens_city_date ON ensemble_snapshots(city, target_date);

        CREATE TABLE IF NOT EXISTS threshold_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            threshold_c REAL NOT NULL,
            direction TEXT NOT NULL,             -- 'above' or 'below'
            my_prob REAL NOT NULL,               -- 0-1 基于 ensemble
            ensemble_agreement REAL,
            action_hint TEXT                     -- e.g. "NO Jeddah >35C 2026-04-25"
        );
        CREATE INDEX IF NOT EXISTS idx_tc_city_date ON threshold_candidates(city, target_date);
        """
    )
    conn.commit()
    conn.close()


def _http_json(url: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(f"{url}?{qs}", headers={"User-Agent": "fincept-weather-scanner/1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def fetch_forecast(city: str) -> dict:
    lat, lon, tz, _ = CITIES[city]
    return _http_json(OM_FORECAST, {
        "latitude": lat, "longitude": lon, "timezone": tz,
        "daily": "temperature_2m_max,temperature_2m_min",
        "forecast_days": 7,
    })


def fetch_ensemble(city: str) -> dict:
    lat, lon, tz, _ = CITIES[city]
    # ECMWF IFS + GFS ensemble members daily max
    return _http_json(OM_ENSEMBLE, {
        "latitude": lat, "longitude": lon, "timezone": tz,
        "models": "gfs_seamless,ecmwf_ifs025",
        "hourly": "temperature_2m",
        "forecast_days": 7,
    })


def _c2f(c: float) -> float:
    return c * 9 / 5 + 32


@dataclass
class Candidate:
    city: str
    target_date: str
    threshold_c: float
    direction: str  # above | below
    my_prob: float
    agreement: float
    action: str


def _compute_ensemble_stats(ens: dict) -> dict:
    """parse open-meteo ensemble hourly → daily max per member, return per-date stats"""
    hourly = ens.get("hourly", {})
    times = hourly.get("time", [])
    # ensemble members: temperature_2m_member01, member02, etc
    member_keys = [k for k in hourly.keys() if k.startswith("temperature_2m_member")]
    if not member_keys:
        # fall back to gfs/ecmwf temperature_2m (single trace)
        return {}

    # bucket by date
    by_date: dict[str, dict[str, list[float]]] = {}
    for i, t in enumerate(times):
        d = t[:10]
        by_date.setdefault(d, {})
        for mk in member_keys:
            vals = hourly[mk]
            if i < len(vals) and vals[i] is not None:
                by_date[d].setdefault(mk, []).append(vals[i])

    # per-date per-member daily max, then median + stdev across members
    daily_stats = {}
    for d, per_member in by_date.items():
        member_maxes = [max(v) for v in per_member.values() if v]
        if len(member_maxes) < 3:
            continue
        med = statistics.median(member_maxes)
        sd = statistics.stdev(member_maxes) if len(member_maxes) > 1 else 0.0
        # agreement: exp(-sd) roughly, capped [0,1]. sd=0→1, sd=3°C→~0.05
        agreement = max(0.0, min(1.0, 1.0 - sd / 4.0))
        daily_stats[d] = {"members": len(member_maxes), "median": med, "stdev": sd, "agreement": agreement}
    return daily_stats


def _threshold_prob(median: float, stdev: float, threshold: float, direction: str) -> float:
    """Gaussian approx: P(X > threshold) where X ~ N(median, stdev)"""
    if stdev < 0.1:
        return 1.0 if (direction == "above" and median > threshold) or (direction == "below" and median < threshold) else 0.0
    # Z-score
    z = (threshold - median) / stdev
    # CDF via erf approximation
    import math
    cdf = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return 1 - cdf if direction == "above" else cdf


def scan_city(city: str) -> list[Candidate]:
    ens = fetch_ensemble(city)
    stats = _compute_ensemble_stats(ens)
    if not stats:
        return []

    ts = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    candidates: list[Candidate] = []
    for d, s in stats.items():
        # store ensemble snapshot
        c.execute(
            "INSERT INTO ensemble_snapshots(ts,city,target_date,members,high_c_median,high_c_stdev,agreement_score) VALUES(?,?,?,?,?,?,?)",
            (ts, city, d, s["members"], s["median"], s["stdev"], s["agreement"]),
        )
        # for each anchor threshold, compute both directions.
        # Only keep thresholds within ±7°C of ensemble median — beyond that, market wouldn't exist / is trivial.
        # Within that band, flag high-confidence tails (0.80 ≤ p ≤ 0.95 is the sweet spot — extreme but not laughable).
        for th in ANCHOR_THRESHOLDS_C:
            if abs(th - s["median"]) > 7.0:
                continue
            for direction in ("above", "below"):
                p = _threshold_prob(s["median"], s["stdev"], th, direction)
                # Sweet-spot tails: 80-99% confidence = non-trivial but strongly biased
                if (0.80 <= p <= 0.99) or (0.01 <= p <= 0.20):
                    side = "YES" if p >= 0.80 else "NO"
                    # for NO side we're fading the "above" assertion, so reprice
                    action = f"{side} {city} {direction}{th}°C on {d} (my_p={p:.2f}, agree={s['agreement']:.2f})"
                    c.execute(
                        "INSERT INTO threshold_candidates(ts,city,target_date,threshold_c,direction,my_prob,ensemble_agreement,action_hint) VALUES(?,?,?,?,?,?,?,?)",
                        (ts, city, d, th, direction, p, s["agreement"], action),
                    )
                    candidates.append(Candidate(city, d, th, direction, p, s["agreement"], action))
    conn.commit()
    conn.close()
    return candidates


def cmd_scan(args):
    ensure_schema()
    results: list[Candidate] = []
    for city in CITIES:
        try:
            c = scan_city(city)
            results.extend(c)
            print(f"✓ {city:15} — {len(c)} high-confidence candidates")
        except Exception as e:
            print(f"✗ {city:15} — error: {e}", file=sys.stderr)

    if args.format == "json":
        print(json.dumps([c.__dict__ for c in results], indent=2))
        return

    # pretty print top candidates — sort by agreement first, then by extremity of prob (closer to 0 or 1 = more certain)
    results.sort(key=lambda r: (r.agreement, abs(r.my_prob - 0.5)), reverse=True)
    print(f"\n🎯 Top 15 sweet-spot candidates (80-99% or 1-20%, within ±7°C of median):")
    print(f"{'city':15} {'date':12} {'thresh':>8} {'dir':>5} {'my_p':>6} {'agree':>6}  action")
    for r in results[:15]:
        print(f"{r.city:15} {r.target_date:12} {r.threshold_c:>6.1f}°C {r.direction:>5} {r.my_prob:>6.2%} {r.agreement:>6.2f}  {r.action}")


def cmd_loop(args):
    ensure_schema()
    print(f"🌦  Weather scanner loop — every {args.interval}s  — Ctrl+C to stop")
    while True:
        try:
            print(f"\n=== {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC scan ===", flush=True)
            cmd_scan(argparse.Namespace(format="pretty"))
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n👋 stopped.")
            break
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
            time.sleep(30)


def cmd_report(args):
    ensure_schema()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    since = (datetime.now(timezone.utc).timestamp() - args.days * 86400)
    c.execute(
        """SELECT city, target_date, threshold_c, direction, my_prob, ensemble_agreement, action_hint, ts
           FROM threshold_candidates
           WHERE ts >= datetime(?, 'unixepoch')
           ORDER BY ensemble_agreement DESC, abs(my_prob - 0.5) DESC
           LIMIT 30""",
        (since,),
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        print(f"no candidates in last {args.days} day(s)")
        return
    print(f"📊 last {args.days}d — top 30 by agreement × extremity:")
    print(f"{'city':15} {'date':12} {'thresh':>8} {'dir':>5} {'my_p':>6} {'agree':>6}  action")
    for row in rows:
        city, d, th, dr, p, ag, act, _ = row
        print(f"{city:15} {d:12} {th:>6.1f}°C {dr:>5} {p:>6.2%} {ag:>6.2f}  {act}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--format", choices=("pretty", "json"), default="pretty")
    s.set_defaults(func=cmd_scan)

    l = sub.add_parser("loop")
    l.add_argument("--interval", type=int, default=1800)  # 30 min default
    l.set_defaults(func=cmd_loop)

    r = sub.add_parser("report")
    r.add_argument("--days", type=int, default=7)
    r.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
