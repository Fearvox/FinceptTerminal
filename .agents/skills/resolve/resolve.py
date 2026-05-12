"""
/resolve — prediction-market trader reverse-engineering.

stdlib-only (urllib + statistics + argparse). No new deps.

Usage:
    python3 resolve.py <target> [--bets=N] [--save]

Target formats:
    ChristopherRandles               → manifold:ChristopherRandles
    manifold:ChristopherRandles      → explicit
    https://manifold.markets/Randles → URL inferred
    polymarket:0x6fdc...             → (future)

Output: markdown dossier to stdout. With --save, also writes to
~/.claude/projects/-Users-0xvox-Documents-GitHub-FinceptTerminal/memory/
<handle>-reverse-engineering.md and appends MEMORY.md index entry.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import urllib.request
import urllib.error
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

MEMORY_DIR = os.path.expanduser(
    "~/.claude/projects/-Users-0xvox-Documents-GitHub-FinceptTerminal/memory"
)

# ─── Parsing ─────────────────────────────────────────────────────────────

def parse_target(raw: str) -> tuple[str, str]:
    """Return (platform, handle) from raw user input."""
    raw = raw.strip()
    m_url = re.match(r"https?://(?:www\.)?manifold\.markets/(?:user/)?([A-Za-z0-9_\-]+)", raw)
    if m_url:
        return ("manifold", m_url.group(1))
    m_url = re.match(r"https?://(?:www\.)?polymarket\.com/.*?/([A-Za-z0-9_\-]+)", raw)
    if m_url:
        return ("polymarket", m_url.group(1))
    if ":" in raw:
        platform, handle = raw.split(":", 1)
        return (platform.lower(), handle)
    # Bare handle → default to manifold
    return ("manifold", raw)


# ─── HTTP ────────────────────────────────────────────────────────────────

def http_get(url: str, timeout: int = 15) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "resolve/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ─── Manifold adapter ────────────────────────────────────────────────────

MANIFOLD_BASE = "https://api.manifold.markets/v0"


def manifold_fetch_profile(handle: str) -> dict:
    return http_get(f"{MANIFOLD_BASE}/user/{handle}")


def manifold_fetch_bets(user_id: str, limit: int = 200) -> list[dict]:
    """Manifold paginates via 'before' cursor; pull up to `limit` total."""
    all_bets: list[dict] = []
    before = None
    while len(all_bets) < limit:
        page_size = min(1000, limit - len(all_bets))
        url = f"{MANIFOLD_BASE}/bets?userId={user_id}&limit={page_size}"
        if before:
            url += f"&before={before}"
        try:
            page = http_get(url, timeout=25)
        except Exception:
            break
        if not page:
            break
        all_bets.extend(page)
        before = page[-1].get("id")
        if len(page) < page_size:
            break
    return all_bets[:limit]


def manifold_fetch_market_titles(contract_ids: list[str]) -> dict[str, str]:
    """Best-effort: fetch market titles for up to 30 contract IDs.
    Failures are silent (title lookup is nice-to-have, not critical)."""
    out: dict[str, str] = {}
    for cid in contract_ids[:30]:
        try:
            mkt = http_get(f"{MANIFOLD_BASE}/market/{cid}", timeout=8)
            out[cid] = mkt.get("question", "")
        except Exception:
            continue
    return out


# ─── Analysis ────────────────────────────────────────────────────────────

def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def analyze_bets(bets: list[dict]) -> dict:
    """Compute all 8 probe dimensions."""
    if not bets:
        return {"empty": True}

    # ── 1. Activity streak (consecutive UTC days with ≥1 bet)
    days_with_bets = set()
    for b in bets:
        d = _ts(b.get("createdTime", 0)).date()
        days_with_bets.add(d)
    sorted_days = sorted(days_with_bets)
    longest_streak = cur = 1
    for i in range(1, len(sorted_days)):
        if (sorted_days[i] - sorted_days[i - 1]).days == 1:
            cur += 1
            longest_streak = max(longest_streak, cur)
        else:
            cur = 1

    # Current streak (up to today)
    today = datetime.now(timezone.utc).date()
    current_streak = 0
    check_day = today
    while check_day in days_with_bets:
        current_streak += 1
        check_day = check_day - timedelta(days=1)

    # ── 2. Direction bias
    outcomes = Counter(b.get("outcome", "?") for b in bets)
    total = sum(outcomes.values())
    yes_pct = outcomes.get("YES", 0) / total * 100 if total else 0
    no_pct = outcomes.get("NO", 0) / total * 100 if total else 0

    # ── 3. Order style (limit if has limitProb / isFilled)
    limit_count = sum(1 for b in bets if b.get("limitProb") is not None
                      or b.get("isFilled") is not None)
    limit_pct = limit_count / total * 100 if total else 0

    # ── 4. Size distribution
    sizes = [abs(b.get("amount", 0)) for b in bets]
    buckets = {"tiny_<50": 0, "small_50_500": 0, "mid_500_5k": 0, "big_>=5k": 0}
    for s in sizes:
        if s < 50: buckets["tiny_<50"] += 1
        elif s < 500: buckets["small_50_500"] += 1
        elif s < 5000: buckets["mid_500_5k"] += 1
        else: buckets["big_>=5k"] += 1
    size_stats = {
        "median": statistics.median(sizes) if sizes else 0,
        "mean": statistics.mean(sizes) if sizes else 0,
        "max": max(sizes) if sizes else 0,
    }

    # ── 5. Probability targeting
    probs = [b.get("probBefore", 0) for b in bets if "probBefore" in b]
    tail_low = sum(1 for p in probs if p <= 0.05)
    tail_high = sum(1 for p in probs if p >= 0.95)
    middle = sum(1 for p in probs if 0.3 <= p <= 0.7)
    prob_stats = {
        "tail_low_pct": tail_low / len(probs) * 100 if probs else 0,
        "tail_high_pct": tail_high / len(probs) * 100 if probs else 0,
        "middle_pct": middle / len(probs) * 100 if probs else 0,
        "tail_total_pct": (tail_low + tail_high) / len(probs) * 100 if probs else 0,
    }

    # ── 6. Session timing (UTC hour histogram)
    hours = [_ts(b.get("createdTime", 0)).hour for b in bets]
    hour_hist = Counter(hours)
    top_hours = hour_hist.most_common(3)
    active_hour_span = max(hour_hist.keys()) - min(hour_hist.keys()) if hour_hist else 0

    # ── 7. Category focus (inferred from market title substrings)
    # We don't have categories in bet payload; report "N distinct markets"
    distinct_markets = len({b.get("contractId", "") for b in bets})

    # ── 8. Streak signatures: runs of 20+ same-direction bets
    dir_runs = []
    cur_dir = None
    cur_run = 0
    longest_dir_run = (None, 0)
    for b in sorted(bets, key=lambda x: x.get("createdTime", 0)):
        d = b.get("outcome", "?")
        if d == cur_dir:
            cur_run += 1
        else:
            if cur_run >= 20:
                dir_runs.append((cur_dir, cur_run))
            if cur_run > longest_dir_run[1]:
                longest_dir_run = (cur_dir, cur_run)
            cur_dir = d
            cur_run = 1
    if cur_run > longest_dir_run[1]:
        longest_dir_run = (cur_dir, cur_run)

    # Limit-order run
    longest_limit_run = 0
    cur_limit_run = 0
    for b in sorted(bets, key=lambda x: x.get("createdTime", 0)):
        is_limit = (b.get("limitProb") is not None
                    or b.get("isFilled") is not None)
        if is_limit:
            cur_limit_run += 1
            longest_limit_run = max(longest_limit_run, cur_limit_run)
        else:
            cur_limit_run = 0

    return {
        "n_bets": total,
        "activity": {
            "distinct_days": len(days_with_bets),
            "longest_daily_streak": longest_streak,
            "current_streak": current_streak,
            "first_bet": sorted_days[0].isoformat() if sorted_days else None,
            "last_bet": sorted_days[-1].isoformat() if sorted_days else None,
        },
        "direction": {
            "yes_pct": round(yes_pct, 1),
            "no_pct": round(no_pct, 1),
            "longest_same_direction_run": longest_dir_run,
            "flagged_dir_runs": dir_runs,
        },
        "order_style": {
            "limit_pct": round(limit_pct, 1),
            "longest_limit_run": longest_limit_run,
        },
        "sizes": {"buckets": buckets, **size_stats},
        "probability_targeting": prob_stats,
        "timing": {
            "top_3_utc_hours": top_hours,
            "active_hour_span": active_hour_span,
        },
        "markets": {"distinct_count": distinct_markets},
    }


# ─── Rendering ───────────────────────────────────────────────────────────

def inject_profile_streak(analysis: dict, profile: dict) -> dict:
    """Patch analysis.activity with profile-level streak (more reliable
    than sample-distinct-days when bet limit is small)."""
    if "activity" in analysis and profile:
        analysis["activity"]["profile_streak_days"] = profile.get(
            "currentBettingStreak", 0)
    return analysis


def render_dossier(platform: str, handle: str, profile: dict,
                   analysis: dict, titles: dict[str, str]) -> str:
    """Return a markdown-formatted dossier."""
    buf: list[str] = []
    P = buf.append

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    P(f"# Resolve · {platform}:{handle}")
    P(f"")
    P(f"**Snapshot**: {now}")
    P(f"**Profile URL**: https://manifold.markets/{handle}" if platform == "manifold" else "")
    P(f"")

    # ── Profile block
    if platform == "manifold":
        bal = profile.get("balance", 0)
        dep = profile.get("totalDeposits", 0)
        streak = profile.get("currentBettingStreak", 0)
        created = profile.get("createdTime", 0)
        last_bet = profile.get("lastBetTime", 0)
        bio = (profile.get("bio") or "").strip()[:200]
        P("## Profile")
        P("")
        P(f"| Field | Value |")
        P(f"|---|---|")
        P(f"| Balance | M${bal:,.0f} |")
        P(f"| Total deposits | M${dep:,.0f} {'(NET WITHDRAWN)' if dep < 0 else ''} |")
        P(f"| Current betting streak | {streak} days |")
        P(f"| Account age | since {_ts(created).date().isoformat() if created else '?'} |")
        P(f"| Last bet | {_ts(last_bet).strftime('%Y-%m-%d %H:%M UTC') if last_bet else '?'} |")
        if bio:
            P(f"| Bio | {bio!r} |")
        P("")

    if analysis.get("empty"):
        P("_No bets returned from API — cannot fingerprint strategy._")
        return "\n".join(buf)

    # ── 1 Activity streak
    a = analysis["activity"]
    P("## 1 · Activity streak")
    P("")
    profile_streak = a.get("profile_streak_days", 0)
    if profile_streak:
        P(f"- **Profile-level current streak: {profile_streak} days** (platform-reported, authoritative)")
    P(f"- Distinct days in sample: {a['distinct_days']}")
    P(f"- Longest streak in sample: {a['longest_daily_streak']} days")
    P(f"- Current streak through today (sample-bounded): {a['current_streak']} days")
    P(f"- Sample range: {a['first_bet']} → {a['last_bet']}")
    P("")

    # ── 2 Direction bias
    d = analysis["direction"]
    P("## 2 · Direction bias")
    P("")
    P(f"- YES: **{d['yes_pct']}%**  · NO: **{d['no_pct']}%**")
    longest_side, longest_n = d["longest_same_direction_run"]
    P(f"- Longest same-direction run: {longest_n} consecutive **{longest_side}** bets")
    if d["flagged_dir_runs"]:
        P(f"- ⚠️ Streak signatures (runs ≥ 20): {d['flagged_dir_runs']}")
    P("")

    # ── 3 Order style
    o = analysis["order_style"]
    P("## 3 · Order style")
    P("")
    tag = "Pure limit-order market maker" if o["limit_pct"] >= 90 \
        else "Mixed" if o["limit_pct"] >= 40 \
        else "Pure market taker"
    P(f"- Limit orders: **{o['limit_pct']}%** → *{tag}*")
    P(f"- Longest consecutive-limit run: {o['longest_limit_run']} bets")
    P("")

    # ── 4 Size
    s = analysis["sizes"]
    P("## 4 · Size distribution")
    P("")
    P(f"- Median: M${s['median']:,.0f} · Mean: M${s['mean']:,.0f} · Max: M${s['max']:,.0f}")
    for label, count in s["buckets"].items():
        pct = count / analysis["n_bets"] * 100
        P(f"  - {label}: {count} ({pct:.1f}%)")
    P("")

    # ── 5 Probability targeting
    p = analysis["probability_targeting"]
    P("## 5 · Probability targeting")
    P("")
    P(f"- Tail-low (p ≤ 5%): **{p['tail_low_pct']:.1f}%**")
    P(f"- Tail-high (p ≥ 95%): **{p['tail_high_pct']:.1f}%**")
    P(f"- Middle (p 30-70%): **{p['middle_pct']:.1f}%**")
    P(f"- Total tail-skim: **{p['tail_total_pct']:.1f}%**")
    tag = "Tail-probability sniper" if p["tail_total_pct"] >= 40 \
        else "Middle-contestation" if p["middle_pct"] >= 50 \
        else "Mixed"
    P(f"- Style: *{tag}*")
    P("")

    # ── 6 Timing
    t = analysis["timing"]
    P("## 6 · Session timing")
    P("")
    hours_txt = ", ".join(f"{h:02d}:00 UTC ({n})" for h, n in t["top_3_utc_hours"])
    P(f"- Top 3 active hours: {hours_txt}")
    P(f"- Active hour span: {t['active_hour_span']} hours")
    P("")

    # ── 7 Markets
    m = analysis["markets"]
    P("## 7 · Market breadth")
    P("")
    P(f"- Distinct markets in sample: **{m['distinct_count']}**")
    bet_per_market = analysis["n_bets"] / max(m["distinct_count"], 1)
    P(f"- Avg bets per market: {bet_per_market:.1f}")
    P("")

    # ── 8 Streak signatures headline
    P("## 8 · Streak signatures (the reproducible habits)")
    P("")
    sig: list[str] = []
    if o["limit_pct"] >= 95:
        sig.append(f"✓ Near-total limit-order discipline ({o['limit_pct']}%) — liquidity provider, never pays taker spread")
    if d["no_pct"] >= 65 or d["yes_pct"] >= 65:
        dom = "NO" if d["no_pct"] >= 65 else "YES"
        sig.append(f"✓ Strong {dom} bias ({max(d['no_pct'], d['yes_pct']):.0f}%) — systematic direction preference")
    if p["tail_total_pct"] >= 40:
        sig.append(f"✓ Tail-skim heavy ({p['tail_total_pct']:.0f}% at p≤5% or p≥95%) — picks up systematic mispricing at extremes")
    profile_streak = a.get("profile_streak_days", 0) or a["longest_daily_streak"]
    if profile_streak >= 100:
        sig.append(f"✓ Iron-discipline daily cadence ({profile_streak} days consecutive) — compounds through time not size")
    if s["median"] < 100:
        sig.append(f"✓ Micro-size discipline (median M${s['median']:.0f}) — volume over conviction")

    if sig:
        for s_line in sig:
            P(f"- {s_line}")
    else:
        P("- No dominant signatures — trader is **generalist / uncommitted**. Less to reverse-engineer.")
    P("")

    return "\n".join(buf)


def render_advice(platform: str, handle: str, analysis: dict,
                  nolan_balance: float = 892) -> str:
    """The copy-what / don't-copy section, scaled to Nolan's current balance."""
    if analysis.get("empty"):
        return ""
    buf: list[str] = []
    P = buf.append
    o = analysis["order_style"]
    d = analysis["direction"]
    p = analysis["probability_targeting"]
    s = analysis["sizes"]
    a = analysis["activity"]

    P("## Copy / Don't copy  (scaled to Nolan's balance ~M$892)")
    P("")
    P("### ✅ Copy")
    if o["limit_pct"] >= 90:
        P("- **100% LIMIT orders**. Never market-take, never pay spread.")
    if d["no_pct"] >= 60:
        P(f"- **NO-bias default** on ambiguous markets. Their {d['no_pct']:.0f}% NO rate is not coincidence — predictions mostly fail.")
    if p["tail_total_pct"] >= 30:
        P("- **Tail-skim micro bets** at p ≤ 5% or p ≥ 95%, M$1-5 per bet. Mispricing of 0.5pp-1pp is normal; stack frequency.")
    if (a.get("profile_streak_days", 0) >= 50) or (a["longest_daily_streak"] >= 50):
        streak_val = a.get("profile_streak_days") or a["longest_daily_streak"]
        P(f"- **Daily streak commitment** (theirs: {streak_val} days). Bet at least M$1 every day, even if just tail-dust. Compounding via habit, not swings.")
    P("")

    P("### ❌ Don't copy (yet)")
    if s["max"] >= 5000:
        P(f"- Their max bet M${s['max']:,.0f} requires portfolio breadth you don't have. Cap your single bets at M${min(nolan_balance * 0.05, 50):.0f} (≤ 5% equity).")
    if analysis["markets"]["distinct_count"] > 100:
        P(f"- They spread across {analysis['markets']['distinct_count']} markets. You have M$892 — **pick 5-10 max** or you starve liquidity for each.")
    if p.get("middle_pct", 0) > 30:
        P("- Their middle-zone bets (p 30-70%) require stronger market-reading edge. Stay in tail zones until N ≥ 30 measured wins.")
    P("")
    return "\n".join(buf)


# ─── Orchestrator ────────────────────────────────────────────────────────

ADAPTERS = {
    "manifold": {
        "profile": manifold_fetch_profile,
        "bets": manifold_fetch_bets,
        "titles": manifold_fetch_market_titles,
    },
    # "polymarket": {...},  # TODO
    # "kalshi": {...},       # TODO
}


def main() -> int:
    p = argparse.ArgumentParser(prog="resolve",
                                description="Reverse-engineer a prediction-market trader.")
    p.add_argument("target", help="URL, platform:handle, or bare handle")
    p.add_argument("--bets", type=int, default=200, help="How many recent bets to pull")
    p.add_argument("--save", action="store_true", help="Also save memo to EverMem folder")
    args = p.parse_args()

    platform, handle = parse_target(args.target)
    adapters = ADAPTERS.get(platform)
    if not adapters:
        print(f"ERROR: platform '{platform}' adapter not implemented yet", file=sys.stderr)
        return 2

    try:
        profile = adapters["profile"](handle)
    except urllib.error.HTTPError as e:
        print(f"ERROR: could not fetch profile ({e.code}). Handle may be wrong.", file=sys.stderr)
        return 1

    user_id = profile.get("id")
    if not user_id:
        print(f"ERROR: profile returned no id field", file=sys.stderr)
        return 1

    print(f"… fetching up to {args.bets} bets for {handle} ({user_id[:10]}…)", file=sys.stderr)
    bets = adapters["bets"](user_id, limit=args.bets)
    print(f"… got {len(bets)} bets. Fingerprinting.", file=sys.stderr)

    analysis = analyze_bets(bets)
    analysis = inject_profile_streak(analysis, profile)
    titles = {}  # Skip title enrichment by default — adds 30s latency for marginal value

    dossier = render_dossier(platform, handle, profile, analysis, titles)
    advice = render_advice(platform, handle, analysis)

    out = dossier + "\n" + advice
    print(out)

    if args.save:
        os.makedirs(MEMORY_DIR, exist_ok=True)
        path = os.path.join(MEMORY_DIR, f"{handle.lower()}-reverse-engineering-auto.md")
        with open(path, "w") as f:
            f.write(f"---\nname: {handle} reverse-engineering (auto via /resolve)\n"
                    f"description: Auto-generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')} "
                    f"— {platform}:{handle} — see dossier for stats\n"
                    f"type: project\n---\n\n")
            f.write(out)
        print(f"\n✓ saved → {path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
