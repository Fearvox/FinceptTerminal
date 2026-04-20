# Cross-Model Strategy Consultation — Propfirm v4 P2 Redesign

**Date**: 2026-04-20
**Trigger**: P2 attempt 1 failed gate (Gate A 2/8, Gate B 6 violations).
  Nolan (P10) halted single-agent hill-climb and requested cross-model
  pipeline to generate redesign hypotheses.
**Infra reuse**: Nolan's OpenRouter proxy + ATOMIC_MODELS registry from
  `~/claude-code-reimagine-for-learning` (CCR / Evensong).

---

## 0. Why this, why now

Claude (Opus 4.7, 1M ctx) ran a single hill-climb:
  P0 → P1 pass → P2 fail → proposed Options A/B/C.

The failure mode Claude is at risk of: tunnel vision. Its redesign
proposals are anchored on what it just wrote. A different model — with
a different reasoning chain, different training cutoff, different
inductive bias — will generate orthogonal hypotheses.

Nolan's further insight: **ablating memory priors** lets us see how
each model reasons *without* Claude's framing. A model with zero prior
might suggest abandoning ATR altogether; one with full context might
double down on regime-conditional trails. The spread is the signal.

Nolan explicit concern: "5% [Sharpe 15% / 5 of 8 scenarios] 对新入门
的你我来说都是不可能完成的挑战 … 参数设得太狠了." This says the
gate itself may be mis-calibrated. Cross-model consultation should
surface whether other models also find the gate unreasonable — that is
an orthogonal decision (relax gate vs fix engine) that Claude alone
cannot assess neutrally.

## 1. Experimental Design — 4 memory ablation conditions × K models

### Conditions (memory priors injected)

| Code | Prior | Content seen by model |
|------|-------|------------------------|
| C0 | **Zero** | Abstract task only: "You are designing an exit rule for a regime-switching trend/range trading engine on 1h-to-15m bars. Recent empirical test says the current approach (ATR chandelier trailing stop at 1.5× ATR) cut winners on 6/7 positive scenarios. Propose 3 alternative exit mechanisms." |
| C1 | **Spec only** | + spec §1-5 (problem statement + 5-phase outline + module layout). No pass gate thresholds. No backtest results. |
| C2 | **Spec + P1 results** | + phase_1_report.md (P1 passed, baseline is weak). Still no P2 attempt shown. |
| C3 | **Full context** | + phase_2_report.md (attempt 1 failure, three hypotheses, current Options A/B/C). Asked to validate, refute, or add a 4th option. |

### Model roster (K = 4–5, from Nolan's ATOMIC_MODELS)

| Slot | Candidate | Why |
|------|-----------|-----|
| M1 | **grok-4-1-fast-reasoning** | Nolan-flagged "乱杀" — strongest of his remote tier |
| M2 | **MiniMax-M2.7** | Alternate reasoning chain, plan-locked quality baseline |
| M3 | **Codex / GPT-5.4** | Via `codex:codex-rescue` skill — no OR proxy needed |
| M4 | **Gemma 4B local** | Offline control — does a tiny model notice the same issue? |
| M5 | (optional) **Claude Sonnet 4.6** via subagent | Self-consistency check |

Not all models × all conditions — that's 5×4 = 20 calls which is noise.
Prioritize:
- **M1 × C0, C3** (opening move + validation)
- **M2 × C0, C2** (independent reasoning + mid-context)
- **M3 × C3** (Codex as "principal engineer reviewer")
- **M4 × C0** (tiny model as sanity — if it finds the issue, problem is obvious)

Total: 7 calls, ~$0.05 cost ballpark.

## 2. Prompt templates

### Shared wrapper
```
ROLE: You are an external reviewer of a trading-engine design decision.
OBJECTIVE: Produce 3 candidate redesigns for an exit rule, ranked by
your confidence. For each: 1 paragraph rationale, 1 paragraph risk,
1 line on what test would falsify it.
FORMAT: Markdown. No code. 400 words max per candidate.
```

### C0 body
```
An engine enters on regime-classifier signals (trend vs range) and uses
ATR(14) chandelier trail (1.5× mult) to exit. Backtest on 8
markets × 1500 bars each shows: 1 market improved (QQQ 1h, Sharpe
-2.0 → +3.4), 6 markets got worse (including EURUSD 1h going from
+5.2 → -2.9 Sharpe). The entry logic is untouched between runs; the
only change is exit from fixed 1.5% SL + 3% TP to ATR chandelier.

What exit rule would you try next? List 3 candidates.
```

### C1 body → C0 body + (spec §1-5 pasted, 2k tokens)
### C2 body → C1 body + phase_1_report.md pasted
### C3 body → C2 body + phase_2_report.md pasted + "Validate or refute Options A/B/C from that report. Add a 4th if warranted."

## 3. Output schema (what we collect)

Per model response, extract into `reports/cross_model_p2_consultation.json`:
```json
{
  "model": "grok-4-1-fast-reasoning",
  "condition": "C0",
  "candidates": [
    {"name": "...", "rationale": "...", "risk": "...", "falsifier": "..."},
    ...
  ],
  "raw": "<full response>",
  "latency_ms": 1234,
  "cost_usd": 0.0034
}
```

## 4. Convergence analysis

After all 7 calls:
1. **Cluster candidates** by technique family: "regime-conditional",
   "widen multiplier", "replace with X (e.g., PSAR, Supertrend, VWAP
   reversion)", "time-based exit", "abandon exit optimization at P2".
2. **Count votes** per family across conditions. Weight C0/C1 higher
   than C3 (less-anchored models are more informative).
3. **Check whether any model challenges the gate itself** — that's the
   "5R/15%/5-of-8" sanity check Nolan flagged.
4. Claude reads the aggregated JSON, synthesizes a decision memo.
5. Human (Nolan) picks final P2.1 direction.

## 5. Open questions for Nolan before execution

1. **OR proxy endpoint** — what's the base URL / auth header I should
   use? Not in FinceptTerminal repo; expect it in CCR
   (`~/claude-code-reimagine-for-learning`).
2. **Harness entry point** — is there a CLI I should shell out to
   (e.g. `cross-model benchmark --models ... --conditions ...`) or do
   I hand-craft curl calls per ATOMIC_MODELS entry?
3. **Which 4–5 models** from the registry — your call. Proposal above
   is a guess; you know which are reliable right now.
4. **Budget/time cap** — run it now (few minutes, <$1) or schedule
   overnight with more conditions?
5. **Gate re-calibration stance** — if ≥ 2 models say "5R / 5-of-8 /
   1.15× thresholds are unreasonable", are you open to relaxing them,
   or is the gate immutable and we must design to it?

## 6. Non-goals (L2 discipline — don't let this become an infra project)

- NOT building a new harness. Reusing Nolan's existing.
- NOT training or fine-tuning. Cold inference only.
- NOT scoring model outputs against a ground truth. This is
  hypothesis generation, not validation.
- NOT making a final P2.1 decision inside this doc. That's a follow-up
  memo after data arrives.

## 7. If consultation yields nothing new

Fallback: proceed with Option A (regime-conditional trail) as Claude
originally recommended. The consultation cost is capped low enough
that "no new info" is an acceptable outcome — we paid a few minutes +
<$1 to gain confidence the original plan isn't tunnel-visioned.
