"""
Propfirm v4 — cross-model strategy consultation harness.

Small FINCEPT-local harness for asking 5 OpenRouter models the same
question under 2 memory-prior conditions, in parallel. Stdlib only
(urllib + concurrent.futures), ~$0.06 per full matrix run.

Design rationale in .planning/propfirm_v4_cross_model_consultation.md

Usage:
    export OPENROUTER_API_KEY=sk-or-v1-...
    python3 -m propfirm_engine.cross_model_harness

Writes results to reports/cross_model_p2_consultation.json.

L2 discipline: this is a one-shot consultation tool, not a framework.
Hardcoded models + 2 conditions for this specific P2 redesign. Copy,
paste, modify for future consultations — do not try to generalize.
"""
from __future__ import annotations

import concurrent.futures as _cf
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

# Models — slug → (OR model_id, display_name). Picked from CCR BENCHMARK_MODELS.
MODELS: dict[str, tuple[str, str]] = {
    "or-gpt5":     ("openai/gpt-5.4",                "GPT-5.4"),
    "or-grok":     ("x-ai/grok-4.20",                "Grok 4.20"),
    "or-gemini":   ("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
    "or-qwen":     ("qwen/qwen3.6-plus",             "Qwen 3.6 Plus (1M)"),
    "or-deepseek": ("deepseek/deepseek-r1-0528",     "DeepSeek R1"),
}

SYSTEM_PROMPT = (
    "You are an external principal-engineer reviewer for a trading engine "
    "design decision. Be rigorous, cite concrete mechanisms, and rank your "
    "proposals by confidence. No code — markdown text only, under 600 words."
)

# ── Context payloads ─────────────────────────────────────────────────────

C0_PROMPT = """\
A regime-switching trading engine (trend vs range classifier → S1 EMA or S6 MTF
entries on 1h / 15m bars across BTC, ETH, SPY, QQQ, GC, CL, EURUSD) currently
exits on a fixed 1.5% stop-loss + 3% take-profit (1:2 R:R).

I replaced the fixed SL with an ATR(14) chandelier trailing stop at 1.5× mult,
kept TP as a hard cap. 8-scenario backtest result:
  - 1 scenario improved  (QQQ 1h, Sharpe -2.0 → +3.4)
  - 6 of 7 positive/near-positive scenarios got WORSE
  - EURUSD 1h: +5.2 → -2.9 Sharpe  (worst regression)
  - CL 1h: trail did not fire (only 3 trades, all hit TP or regime-flip)

Propose THREE candidate exit mechanisms that might recover the regressions
while keeping QQQ's improvement. For each: 1 short paragraph rationale,
1 short paragraph risk, 1 line describing a concrete backtest that would
falsify it. Rank by your confidence.
"""

C3_INSTRUCTION = """\
Above are the spec, P1 (passed) report, and P2 (failed attempt 1) report for a
trading engine. Claude (internal agent) proposed three options at the end of
the P2 report:
  A. Regime-conditional trail (trail on for trend regimes, fixed SL for range)
  B. Widen multiplier to 2.5× in range regimes only
  C. Skip P2 entirely, go straight to P3 confluence gate

Three questions:

1. Principal-engineer critique: do Options A/B/C miss anything obvious?
2. Propose a 4th option if warranted, with rationale/risk/falsifier in the
   same shape as the P2 report's options.
3. Is the pass gate reasonable? (Sharpe ≥ baseline × 1.15 on ≥ 5/8 scenarios,
   when most baselines are Sharpe-negative.) The user is an independent
   trader, not a trained quant, and suspects the gate is mis-calibrated for
   early-innings development. Validate or push back with concrete statistical
   reasoning (e.g., DSR / sample size / R:R arithmetic on negative Sharpe).

Return markdown. 600 words max.
"""


def _read(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


def build_c3_payload() -> str:
    spec = _read(os.path.join(REPO_ROOT, "docs", "superpowers", "specs",
                              "2026-04-20-propfirm-v4-engine-design.md"))
    p1 = _read(os.path.join(os.path.dirname(__file__),
                            "reports", "phase_1_report.md"))
    p2 = _read(os.path.join(os.path.dirname(__file__),
                            "reports", "phase_2_report.md"))
    return (
        "## SPEC\n\n" + spec +
        "\n\n## PHASE 1 REPORT (passed)\n\n" + p1 +
        "\n\n## PHASE 2 REPORT (failed attempt 1)\n\n" + p2 +
        "\n\n---\n\n" + C3_INSTRUCTION
    )


# ── OR HTTP call ─────────────────────────────────────────────────────────

@dataclass
class CallResult:
    slot: str
    model_id: str
    condition: str
    ok: bool
    content: str
    error: str | None
    latency_ms: int
    usage: dict[str, Any]


def call_openrouter(slot: str, model_id: str, condition: str,
                    user_content: str, api_key: str,
                    max_tokens: int = 1200, timeout: int = 120) -> CallResult:
    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }
    data = json.dumps(body).encode()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/0xvox/FinceptTerminal",
        "X-Title": "propfirm-v4 cross-model consultation",
    }
    req = urllib.request.Request(OR_URL, data=data, headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
        dt = int((time.time() - t0) * 1000)
        payload = json.loads(raw)
        if "choices" not in payload or not payload["choices"]:
            return CallResult(slot, model_id, condition, False, "",
                              f"no choices in response: {raw[:400]}", dt, {})
        msg = payload["choices"][0].get("message", {})
        content = msg.get("content") or ""
        usage = payload.get("usage", {})
        return CallResult(slot, model_id, condition, True, content, None, dt, usage)
    except urllib.error.HTTPError as e:
        dt = int((time.time() - t0) * 1000)
        err_body = e.read().decode() if hasattr(e, "read") else str(e)
        return CallResult(slot, model_id, condition, False, "",
                          f"HTTPError {e.code}: {err_body[:400]}", dt, {})
    except Exception as e:
        dt = int((time.time() - t0) * 1000)
        return CallResult(slot, model_id, condition, False, "",
                          f"{type(e).__name__}: {e}", dt, {})


# ── Orchestration ────────────────────────────────────────────────────────

def run_matrix(api_key: str) -> dict[str, Any]:
    c3_body = build_c3_payload()
    jobs: list[tuple[str, str, str, str]] = []  # slot, model_id, condition, user_content
    for slot, (model_id, _display) in MODELS.items():
        jobs.append((slot, model_id, "C0", C0_PROMPT))
        jobs.append((slot, model_id, "C3", c3_body))

    results: list[CallResult] = []
    with _cf.ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = {
            ex.submit(call_openrouter, slot, mid, cond, body, api_key): (slot, mid, cond)
            for (slot, mid, cond, body) in jobs
        }
        for fut in _cf.as_completed(futs):
            r = fut.result()
            results.append(r)
            tag = "✓" if r.ok else "✗"
            head = (r.content[:120].replace("\n", " ") if r.ok
                    else (r.error or "")[:120])
            print(f"  {tag} {r.slot:<14} {r.condition}  {r.latency_ms:>6}ms  {head}",
                  flush=True)

    # Sort deterministically
    results.sort(key=lambda x: (x.slot, x.condition))
    return {
        "generated_at": int(time.time()),
        "models": {slot: display for slot, (_, display) in MODELS.items()},
        "conditions": {
            "C0": "zero prior — abstract problem statement only",
            "C3": "full context — spec + P1 report + P2 report + ask",
        },
        "results": [
            {
                "slot": r.slot, "model_id": r.model_id, "condition": r.condition,
                "ok": r.ok, "content": r.content, "error": r.error,
                "latency_ms": r.latency_ms, "usage": r.usage,
            }
            for r in results
        ],
    }


def main() -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY env var is not set.", file=sys.stderr)
        return 2
    print(f"Running cross-model consultation: {len(MODELS)} models × 2 conditions "
          f"= {len(MODELS) * 2} parallel calls\n")
    out = run_matrix(api_key)
    reports_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    out_path = os.path.join(reports_dir, "cross_model_p2_consultation.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)

    ok = sum(1 for r in out["results"] if r["ok"])
    total = len(out["results"])
    print(f"\n{ok}/{total} successful. Raw → {out_path}")
    return 0 if ok >= total // 2 else 1


if __name__ == "__main__":
    sys.exit(main())
