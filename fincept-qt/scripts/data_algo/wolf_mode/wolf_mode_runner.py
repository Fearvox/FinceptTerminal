"""Wolf Mode Runner — Manifold League Season 37 sniper orchestrator.

One cycle = scan → score → filter → (dry-run | live execute) → ledger.

See SPEC.md for full design rationale.

Env:
    AUTOEXEC_WOLF_LIVE=1     enable real POST /v0/bet (otherwise dry-run)
    WOLF_MANIFOLD_KEY        Manifold API key (else uses bundled fallback)
    WOLF_BANKROLL_CAP        override total exposure cap (default 300)
    WOLF_DAILY_BET_CAP       override daily new-bet cap (default 60)
    WOLF_SINGLE_BET_MAX      override single bet cap (default 30)
    WOLF_SCORE_MIN_BET       min composite score to bet (default 0.30)
    WOLF_PROBE_HOURS         hours of forced dry-run on first run (default 24)

Usage:
    python -m wolf_mode.wolf_mode_runner cycle             # one cycle
    python -m wolf_mode.wolf_mode_runner cycle --verbose
    python -m wolf_mode.wolf_mode_runner brief             # daily rollup print
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running as module OR script
try:
    from .wolf_ledger import (
        append_decision, cycle_id_now, init_audit_schema,
        killswitch_active, record_killswitch, stats_summary, utc_iso,
        DEFAULT_DIR, LEDGER_FILE,
    )
    from .manifold_market_widener import scan as widener_scan
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from wolf_mode.wolf_ledger import (  # noqa
        append_decision, cycle_id_now, init_audit_schema,
        killswitch_active, record_killswitch, stats_summary, utc_iso,
        DEFAULT_DIR, LEDGER_FILE,
    )
    from wolf_mode.manifold_market_widener import scan as widener_scan  # noqa


MANIFOLD = "https://api.manifold.markets/v0"
DEFAULT_KEY = "43659d2b-b80c-43dc-82b5-4633d0e4950b"  # operator's published key in repo permissions

# Caps (env-overridable)
BANKROLL_CAP = float(os.environ.get("WOLF_BANKROLL_CAP", "300"))
DAILY_BET_CAP = float(os.environ.get("WOLF_DAILY_BET_CAP", "60"))
SINGLE_BET_MAX = float(os.environ.get("WOLF_SINGLE_BET_MAX", "30"))
SINGLE_MARKET_MAX = float(os.environ.get("WOLF_SINGLE_MARKET_MAX", "50"))
DRAWDOWN_KILL_MANA = float(os.environ.get("WOLF_DRAWDOWN_KILL", "80"))
SCORE_MIN_BET = float(os.environ.get("WOLF_SCORE_MIN_BET", "0.30"))
PROBE_HOURS = float(os.environ.get("WOLF_PROBE_HOURS", "24"))


def now() -> datetime:
    return datetime.now(timezone.utc)


def _api_key() -> str:
    return os.environ.get("WOLF_MANIFOLD_KEY", DEFAULT_KEY)


def _http_json(url: str, method: str = "GET", body: dict | None = None,
               timeout: int = 12, with_auth: bool = False) -> dict | list:
    headers = {"User-Agent": "wolf-mode-runner/1.0"}
    if with_auth:
        headers["Authorization"] = f"Key {_api_key()}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_balance() -> float:
    me = _http_json(f"{MANIFOLD}/me", with_auth=True)
    return float(me.get("balance", 0))


# ─── scan ───────────────────────────────────────────────────────────────────


def scan_phase(verbose: bool = False) -> list[dict]:
    """Pull candidates from widener (and later: existing scanners via subprocess)."""
    candidates = widener_scan(["newest", "active", "soon_to_close"], limit_per_source=80)
    if verbose:
        print(f"[scan] {len(candidates)} unique unresolved candidates", file=sys.stderr)
    return candidates


# ─── position lock (dedup) ─────────────────────────────────────────────────


def load_existing_positions(window_days: int = 14) -> dict[str, dict]:
    """Read ledger and tally cumulative live BET size per contract_id in the
    last `window_days`. Returns {contract_id: {size_mana: int, count: int}}.

    Used by decide_phase to enforce SINGLE_MARKET_MAX cumulative and to skip
    contracts already filled to capacity.
    """
    if not LEDGER_FILE.exists():
        return {}
    cutoff = now() - timedelta(days=window_days)
    pos: dict[str, dict] = {}
    try:
        for line in LEDGER_FILE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("phase") != "live":
                continue
            if r.get("decision") != "BET":
                continue
            res = r.get("result") or {}
            if res.get("error") or not res.get("bet_id"):
                continue  # only count successfully placed bets
            try:
                ts = datetime.fromisoformat((r.get("ts") or "").replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
            except Exception:
                continue
            cid = r["candidate"].get("contract_id")
            if not cid:
                continue
            slot = pos.setdefault(cid, {"size_mana": 0, "count": 0, "first_ts": r["ts"]})
            slot["size_mana"] += r.get("size_mana") or 0
            slot["count"] += 1
    except OSError:
        return {}
    return pos


# ─── auditor overrides ─────────────────────────────────────────────────────


AUDITOR_OVERRIDES_FILE = DEFAULT_DIR / "auditor_overrides.json"


def load_auditor_overrides() -> dict[str, dict]:
    """Read {contract_id: {my_prob, side, confidence, rationale, top_risk, ts}}.

    Overrides are produced by:
      - Manual Claude session running auditor subagents (v2)
      - Future automated auditor pipeline (v2.x)

    Stale overrides (>72h old) are skipped — re-audit before trusting.
    """
    if not AUDITOR_OVERRIDES_FILE.exists():
        return {}
    try:
        raw = json.loads(AUDITOR_OVERRIDES_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    overrides = raw.get("overrides", {})
    if not isinstance(overrides, dict):
        return {}
    # Filter stale
    cutoff = now() - timedelta(hours=72)
    fresh = {}
    for cid, ov in overrides.items():
        try:
            ts = datetime.fromisoformat((ov.get("ts") or "").replace("Z", "+00:00"))
            if ts < cutoff:
                continue
        except Exception:
            continue
        if not isinstance(ov.get("my_prob"), (int, float)):
            continue
        if ov.get("side") not in ("YES", "NO", "SKIP"):
            continue
        fresh[cid] = ov
    return fresh


# ─── score ──────────────────────────────────────────────────────────────────


def _kelly(prob_mine: float, prob_market: float) -> float:
    """Half-Kelly fraction. Returns 0 if no edge or odds degenerate."""
    if prob_market <= 0 or prob_market >= 1:
        return 0.0
    # YES side: payout = 1/prob_market when YES wins, else 0
    payout = 1.0 / prob_market - 1.0
    if payout <= 0:
        return 0.0
    full_k = (prob_mine * (payout + 1) - 1) / payout
    return max(0.0, min(full_k * 0.5, 0.25))


def score_one(c: dict, my_prob: float | None = None) -> dict:
    """Compute composite score for a binary candidate.

    Composite is normalized to roughly [0, 1] where ≥0.30 = bet-worthy:
        composite = (raw_edge / 0.10) * conf * liq_score * decay
                    -- kelly affects SIZE, not score
    Decay is hyperbolic: 1/(1 + days/30) so 30d→0.5, 60d→0.33, 210d→0.125.
    """
    out = dict(c)
    out["score"] = {"composite": 0.0, "reason": "no_my_prob"}
    if c.get("outcome_type") != "BINARY":
        out["score"]["reason"] = "non_binary"
        return out
    pm = c.get("probability")
    if pm is None or my_prob is None:
        return out
    raw_edge = abs(my_prob - pm)
    if raw_edge < 0.05:
        out["score"]["reason"] = "edge_too_small"
        out["score"]["raw_edge"] = raw_edge
        return out
    # Pick side
    side = "YES" if my_prob > pm else "NO"
    prob_for_kelly = my_prob if side == "YES" else 1 - my_prob
    prob_market_for_kelly = pm if side == "YES" else 1 - pm
    k = _kelly(prob_for_kelly, prob_market_for_kelly)
    liq = c.get("total_liquidity") or 0
    liq_score = min(1.0, liq / 500.0)
    close_ms = c.get("close_time_ms") or 0
    if close_ms:
        days_left = (close_ms / 1000 - time.time()) / 86400
    else:
        days_left = 60  # unknown close = assume 60 days
    if days_left < 0:
        out["score"]["reason"] = "already_closed"
        return out
    decay = 1.0 / (1.0 + days_left / 30.0)  # hyperbolic, never zero
    conf = 0.5  # default; auditor overrides
    edge_norm = raw_edge / 0.10  # 10pp edge = 1.0 baseline
    composite = edge_norm * conf * liq_score * decay
    out["score"] = {
        "composite": round(composite, 4),
        "raw_edge": round(raw_edge, 4),
        "edge_norm": round(edge_norm, 4),
        "kelly": round(k, 4),
        "liq_score": round(liq_score, 4),
        "decay": round(decay, 4),
        "conf": conf,
        "side": side,
        "days_left": round(days_left, 1),
    }
    return out


def score_phase(candidates: list[dict], verbose: bool = False) -> list[dict]:
    """Score candidates. Uses auditor_overrides.json for my_prob where available."""
    overrides = load_auditor_overrides()
    scored = []
    n_with_prob = 0
    for c in candidates:
        ov = overrides.get(c.get("contract_id"))
        if ov and ov.get("side") in ("YES", "NO"):
            # Auditor said BET — score with their my_prob
            s = score_one(c, my_prob=ov.get("my_prob"))
            s["auditor"] = {
                "my_prob": ov.get("my_prob"),
                "side": ov.get("side"),
                "confidence": ov.get("confidence", 0.5),
                "rationale": (ov.get("rationale") or "")[:300],
                "top_risk": (ov.get("top_risk") or "")[:200],
            }
            # Auditor confidence boosts score's `conf` dimension
            if "composite" in s["score"] and s["score"]["composite"] > 0:
                s["score"]["conf"] = ov.get("confidence", 0.5)
                # rescale composite using new conf (matches score_one formula)
                s["score"]["composite"] = round(
                    s["score"]["edge_norm"] * s["score"]["conf"]
                    * s["score"]["liq_score"] * s["score"]["decay"],
                    4,
                )
            n_with_prob += 1
        elif ov and ov.get("side") == "SKIP":
            # Auditor said skip — mark explicitly
            s = score_one(c)
            s["score"]["composite"] = 0
            s["score"]["reason"] = "auditor_skip"
            s["auditor"] = {"side": "SKIP", "rationale": (ov.get("rationale") or "")[:200]}
            n_with_prob += 1
        else:
            s = score_one(c)
        scored.append(s)
    if verbose:
        print(f"[score] {len(scored)} scored, {n_with_prob} with auditor my_prob", file=sys.stderr)
    return scored


# ─── filter + decide ────────────────────────────────────────────────────────


def decide_phase(scored: list[dict], dry_run: bool, verbose: bool = False) -> list[dict]:
    """Pick decisions: BET / SKIP / WATCH.

    Priority order:
      1. Candidates with auditor my_prob: BET if score >= SCORE_MIN_BET, SKIP if auditor said SKIP
      2. Other candidates: WATCH if liquidity > 100, otherwise drop

    Position-lock: contracts with existing live BET in past 14d → DEDUP_SKIP.
    SINGLE_MARKET_MAX enforced cumulatively: if size + existing >= cap → trim or skip.
    """
    decisions = []
    existing = load_existing_positions(window_days=14)

    # Sort scored: auditor-graded first (by composite desc), then by volume_24h
    audited = sorted(
        [c for c in scored if c.get("auditor") is not None],
        key=lambda c: c.get("score", {}).get("composite", 0),
        reverse=True,
    )
    unaudited = [c for c in scored if c.get("auditor") is None
                 and not c.get("is_resolved")
                 and (c.get("total_liquidity") or 0) > 100]
    unaudited.sort(key=lambda c: c.get("volume_24h", 0), reverse=True)

    # Daily cap tracking — naive: sum of today's live BET in ledger
    today_str = utc_iso()[:10]
    today_bet_total = 0.0
    if LEDGER_FILE.exists():
        for line in LEDGER_FILE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if (r.get("phase") == "live" and r.get("decision") == "BET"
                        and (r.get("ts") or "").startswith(today_str)
                        and (r.get("result") or {}).get("bet_id")):
                    today_bet_total += r.get("size_mana") or 0
            except Exception:
                continue

    # All audited get a decision
    for c in audited:
        composite = c.get("score", {}).get("composite", 0)
        auditor_skip = c.get("auditor", {}).get("side") == "SKIP"
        cid = c["contract_id"]
        existing_size = (existing.get(cid) or {}).get("size_mana", 0)

        if auditor_skip:
            decision = "SKIP"
            size = 0
            reason_extra = "auditor_skip"
        elif existing_size > 0:
            decision = "DEDUP_SKIP"
            size = 0
            reason_extra = f"existing_position_M${existing_size:.0f}"
        elif today_bet_total >= DAILY_BET_CAP:
            decision = "DAILY_CAP_SKIP"
            size = 0
            reason_extra = f"daily_cap_M${today_bet_total:.0f}_of_{DAILY_BET_CAP:.0f}"
        elif composite >= SCORE_MIN_BET:
            decision = "BET"
            kelly = c.get("score", {}).get("kelly", 0)
            conf = c.get("score", {}).get("conf", 0.5)
            raw_size = BANKROLL_CAP * kelly * conf
            size = int(max(5, min(SINGLE_BET_MAX, raw_size)))
            # Enforce SINGLE_MARKET_MAX cumulative (defensive even though existing=0 here)
            max_room = SINGLE_MARKET_MAX - existing_size
            size = int(min(size, max_room))
            # Enforce daily remaining
            daily_room = DAILY_BET_CAP - today_bet_total
            size = int(min(size, daily_room))
            if size < 5:
                decision = "DAILY_CAP_SKIP"
                size = 0
                reason_extra = "insufficient_room"
            else:
                today_bet_total += size
                reason_extra = "ok"
        else:
            decision = "WATCH"
            size = 0
            reason_extra = "score_below_threshold"
        decisions.append({
            "candidate": {
                "contract_id": c["contract_id"],
                "slug": c["slug"],
                "question": c.get("question", ""),
                "url": c["url"],
                "probability": c.get("probability"),
                "volume_24h": c.get("volume_24h"),
                "total_liquidity": c.get("total_liquidity"),
            },
            "score": c.get("score", {}),
            "decision": decision,
            "size_mana": size,
            "outcome": c.get("auditor", {}).get("side") if decision == "BET" else None,
            "reason": f"{reason_extra} | {(c.get('auditor', {}).get('rationale') or '')[:140]}",
            "auditor_used": True,
        })

    # Up to 50 unaudited as WATCH
    for c in unaudited[:max(0, 50 - len(decisions))]:
        decisions.append({
            "candidate": {
                "contract_id": c["contract_id"],
                "slug": c["slug"],
                "question": c.get("question", ""),
                "url": c["url"],
                "probability": c.get("probability"),
                "volume_24h": c.get("volume_24h"),
                "total_liquidity": c.get("total_liquidity"),
            },
            "score": c.get("score", {}),
            "decision": "WATCH",
            "size_mana": 0,
            "outcome": None,
            "reason": c.get("score", {}).get("reason", "no_my_prob"),
            "auditor_used": False,
        })

    if verbose:
        n_bet = sum(1 for d in decisions if d["decision"] == "BET")
        n_skip = sum(1 for d in decisions if d["decision"] == "SKIP")
        n_dedup = sum(1 for d in decisions if d["decision"] == "DEDUP_SKIP")
        n_daily = sum(1 for d in decisions if d["decision"] == "DAILY_CAP_SKIP")
        n_watch = sum(1 for d in decisions if d["decision"] == "WATCH")
        print(f"[decide] {n_bet} BET / {n_skip} SKIP / {n_dedup} DEDUP / {n_daily} DAILY_CAP / {n_watch} WATCH",
              file=sys.stderr)
    return decisions


# ─── execute ────────────────────────────────────────────────────────────────


def execute_phase(decisions: list[dict], dry_run: bool, cycle_id: str,
                  verbose: bool = False) -> dict:
    """Log every decision to ledger. If live (dry_run=False), POST bets."""
    summary = {"bet": 0, "skip": 0, "watch": 0, "dedup_skip": 0, "daily_cap_skip": 0,
               "errored": 0, "total_size": 0}
    for d in decisions:
        row = {
            "ts": utc_iso(),
            "cycle_id": cycle_id,
            "phase": "dry_run" if dry_run else "live",
            "candidate": d["candidate"],
            "score": d["score"],
            "decision": d["decision"],
            "size_mana": d["size_mana"],
            "outcome": d["outcome"],
            "reason": d["reason"],
            "auditor_used": d["auditor_used"],
            "result": None,
        }
        if d["decision"] == "BET" and not dry_run:
            try:
                pre_bal = get_balance()
                resp = _http_json(
                    f"{MANIFOLD}/bet",
                    method="POST",
                    body={
                        "contractId": d["candidate"]["contract_id"],
                        "amount": d["size_mana"],
                        "outcome": d["outcome"],
                    },
                    with_auth=True,
                )
                post_bal = get_balance()
                row["result"] = {
                    "bet_id": resp.get("betId") or resp.get("id"),
                    "error": None,
                    "pre_balance": pre_bal,
                    "post_balance": post_bal,
                }
                summary["bet"] += 1
                summary["total_size"] += d["size_mana"]
            except Exception as e:
                row["result"] = {"bet_id": None, "error": str(e)[:200],
                                 "pre_balance": None, "post_balance": None}
                summary["errored"] += 1
        elif d["decision"] == "BET" and dry_run:
            summary["bet"] += 1
            summary["total_size"] += d["size_mana"]
        elif d["decision"] == "SKIP":
            summary["skip"] += 1
        elif d["decision"] == "DEDUP_SKIP":
            summary["dedup_skip"] += 1
        elif d["decision"] == "DAILY_CAP_SKIP":
            summary["daily_cap_skip"] += 1
        else:
            summary["watch"] += 1
        append_decision(row)
        # rate-limit between live POST
        if not dry_run and d["decision"] == "BET":
            time.sleep(1)
    if verbose:
        print(f"[exec] summary={summary}", file=sys.stderr)
    return summary


# ─── audit (drawdown, calibration) ──────────────────────────────────────────


def audit_phase(verbose: bool = False) -> dict:
    """Check drawdown, set killswitch if needed."""
    init_audit_schema()
    try:
        bal = get_balance()
    except Exception as e:
        if verbose:
            print(f"[audit] balance fetch failed: {e}", file=sys.stderr)
        return {"balance": None, "drawdown_ok": True}

    peak_file = DEFAULT_DIR / "peak_balance.txt"
    peak = float(peak_file.read_text()) if peak_file.exists() else bal
    if bal > peak:
        peak = bal
        peak_file.write_text(str(peak))
    drawdown = peak - bal
    if drawdown >= DRAWDOWN_KILL_MANA:
        record_killswitch(f"drawdown_{drawdown:.0f}_from_peak_{peak:.0f}", drawdown)
        if verbose:
            print(f"[audit] KILLSWITCH triggered drawdown={drawdown:.0f}", file=sys.stderr)
        return {"balance": bal, "peak": peak, "drawdown": drawdown, "killswitch": True}
    return {"balance": bal, "peak": peak, "drawdown": drawdown, "killswitch": False}


# ─── cycle ──────────────────────────────────────────────────────────────────


def _probe_active() -> bool:
    """First WOLF_PROBE_HOURS after install force dry-run."""
    install_marker = DEFAULT_DIR / "install_ts.txt"
    if not install_marker.exists():
        install_marker.parent.mkdir(parents=True, exist_ok=True)
        install_marker.write_text(utc_iso())
        return True
    try:
        install_ts = datetime.fromisoformat(install_marker.read_text().strip().replace("Z", "+00:00"))
    except Exception:
        return True
    return (now() - install_ts) < timedelta(hours=PROBE_HOURS)


def cycle(verbose: bool = False) -> dict:
    cid = cycle_id_now()
    if killswitch_active():
        if verbose:
            print(f"[cycle {cid}] killswitch active, skipping", file=sys.stderr)
        append_decision({"cycle_id": cid, "phase": "halted",
                         "decision": "KILLSWITCH", "reason": "killswitch_active",
                         "candidate": {}, "score": {}, "size_mana": 0, "outcome": None,
                         "auditor_used": False, "result": None})
        return {"halted": True, "reason": "killswitch"}

    env_live = os.environ.get("AUTOEXEC_WOLF_LIVE") == "1"
    probe = _probe_active()
    dry_run = (not env_live) or probe

    candidates = scan_phase(verbose=verbose)
    scored = score_phase(candidates, verbose=verbose)
    decisions = decide_phase(scored, dry_run=dry_run, verbose=verbose)
    exec_summary = execute_phase(decisions, dry_run=dry_run, cycle_id=cid, verbose=verbose)
    audit = audit_phase(verbose=verbose)

    return {
        "cycle_id": cid,
        "dry_run": dry_run,
        "probe_phase": probe,
        "env_live": env_live,
        "candidates_in": len(candidates),
        "decisions_out": len(decisions),
        "exec": exec_summary,
        "audit": audit,
    }


def brief() -> str:
    """Daily rollup. Print to stdout."""
    s = stats_summary()
    bal = "?"
    try:
        bal = f"M${get_balance():.1f}"
    except Exception:
        pass
    lines = [
        f"=== Wolf Mode Brief {utc_iso()} ===",
        f"balance     : {bal}",
        f"ledger_rows : {s['ledger_rows']}",
        f"BET / SKIP / WATCH decisions: {s['decisions_bet']} / {s['decisions_skip']} / {s['decisions_watch']}",
        f"closed positions: {s['closed_n']} (pnl M${s['closed_pnl_mana']:.1f}, avg M${s['closed_avg_mana']:.2f})",
        f"killswitch  : {s['killswitch_active']}",
    ]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(prog="wolf_mode_runner")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cycle")
    c.add_argument("--verbose", action="store_true")
    sub.add_parser("brief")
    args = p.parse_args()

    if args.cmd == "cycle":
        result = cycle(verbose=args.verbose)
        print(json.dumps(result, indent=2, default=str))
    elif args.cmd == "brief":
        print(brief())


if __name__ == "__main__":
    main()
