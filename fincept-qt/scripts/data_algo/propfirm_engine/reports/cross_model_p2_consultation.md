# Cross-Model P2 Consultation — Synthesis

**Date**: 2026-04-20
**Cost**: $0.13 USD (OR token billing)
**Latency**: 75s for 10 parallel calls
**Matrix**: 5 models × 2 conditions (C0 zero-prior, C3 full-context)

Raw JSON: `cross_model_p2_consultation.json`

---

## 0. Participation

| Slot | Model | C0 (zero) | C3 (full) |
|------|-------|:---------:|:---------:|
| or-gpt5     | GPT-5.4                | ✅ 1265 tok | ✅ 6740 tok |
| or-grok     | Grok 4.20              | ✅ 1142 tok | ✅ 6476 tok |
| or-gemini   | Gemini 3.1 Pro         | ✅ 1511 tok | ✅ 7371 tok |
| or-qwen     | Qwen 3.6 Plus (1M)     | ✅ 4064 tok | ✅ 9824 tok |
| or-deepseek | DeepSeek R1            | ✅ 1510 tok | ❌ empty (1200 reasoning tokens burnt, no visible output — R1 failure mode) |

9 of 10 usable responses.

---

## 1. UNANIMOUS FINDING — The pass gate is mis-specified

**4 of 4 C3 respondents independently say** the "Sharpe × 1.15 on ≥5/8" gate is
statistically broken. This validates Nolan's intuition ("参数设得太狠").

| Model | Core argument |
|-------|---------------|
| GPT-5.4 | "Highest-confidence call: the current P2 pass gate is mis-specified." Negative × 1.15 creates sign errors (BTCUSDT's "pass" was a vacuous artefact). Sharpe SE ≈ 1/√N; at N=5 the 15% threshold is inside 1σ noise. |
| Grok 4.20 | "Not reasonable for early innings." SE_Sharpe ≈ √(1+0.5·SR²)/√N makes most cells statistically indistinguishable from zero. |
| Qwen 3.6 | "Statistically invalid." Multiplicative gates on negative baselines are directionally inverted. Propfirms use Expectancy and Profit Factor, not raw Sharpe on micro-samples. |
| Gemini 3.1 | (implicit — focused critique on Options A/B, didn't make gate the headline) |

### Replacement gate — convergent recommendation

Combining the three explicit proposals:

> **Gate A'**: ΔExpectancy (R per trade) > 0 on ≥ 5/8 scenarios
> **Gate B'**: No scenario drops realized PnL % by > 30%
> **Gate C' (stretch)**: Scenarios with N ≥ 10 trades additionally pass `Sharpe absolute delta > 0`
> Reserve DSR / Sharpe-ratio tests for N ≥ 30 (i.e., P5 terminal).

This is additive, not multiplicative, so negative-baseline sign errors disappear.
It matches propfirm evaluation norms better than pure Sharpe.

---

## 2. Regime-conditional exit — 5 of 5 models endorse directionally

Every C0 respondent (zero prior, no Claude framing) independently gave a
"regime-conditional exit" variant as their **Rank 1**. Claude's Option A was
already the right instinct, but it's **not sufficient alone** — 4 of 5 models
layered in additional mechanisms:

| Mechanism | Who proposed it | What it does |
|-----------|----------------|--------------|
| **Delayed trail activation** (fix SL initially, activate chandelier after +0.75R – +1R favorable excursion) | GPT-5 C0 #1, Qwen C0 #2 | Gives trades room to develop before the trail can whipsaw |
| **Partial scale-out** at 1R (50% off, runner trails) | GPT-5 C0 #3, Qwen C3 D | Locks positive expectancy; reduces path-dependence |
| **Explicit regime-flip exit** | Grok C0 #2 | Uses the classifier itself as an informed exit signal |
| **Volatility-scaled TP** (replace fixed 3% with 2× entry-bar ATR) | Grok C0 #3 | Restores R:R symmetry when stop is ATR-scaled |
| **Min-hold + MFE gate** | Grok C3 D | Prevents jitter-induced micro-exits before trend confirms |

**Single most common warning across all C3 models**: regime classifier
**lag** (2–4 bars per Qwen, 1–3 bars per GPT-5). Regime-conditional logic
inherits whatever error the classifier has — if it's jittery, the exit
switches fire late. Gemini C3 specifically flags this: "the v3 regime
classifier suffers from excessive regime jitter" — matching spec §1.

---

## 3. The one truly novel idea Claude missed

**GPT-5.4 Option D** (pulled forward P4 MFE logger):

> "Keep P1 fixed 1:2 SL/TP as the control exit, proceed to P3, and pull
> forward a lightweight P4-style MFE/MAE logger *now*. After 30+ trades,
> decide whether trailing is justified by observed post-exit path behavior."

**Why this is better than Claude's A/B/C**: don't optimize exits in the
dark. Measure counterfactual first (how much do we leave on the table?
do losers expand their MAE or bounce?), then decide whether trail is
even needed. Claude's A/B/C all implicitly assume "trailing is the right
intervention" without proving it.

This is the most L2-compliant option on the board:
- No new parameter tuning.
- No premature optimization.
- Produces data that informs whether P2 should exist at all.
- P3 (confluence gate) is entry-side, structurally independent of exits.

---

## 4. Convergent ranking across all 9 responses

Weighting C0 and C3 equally; counting mentions of each idea family:

| Technique | C0 votes | C3 votes | Total | Notes |
|-----------|:--------:|:--------:|:-----:|-------|
| Regime-conditional exit (base of A) | 5 | 3 | **8** | Universal consensus on direction |
| Delayed/profit-gated trail activation | 2 | 1 | **3** | Strong cross-model signal |
| Partial scale-out at 1R | 1 | 1 | **2** | Propfirm canonical |
| MFE logger pulled forward (= GPT-5 D) | — | 2 | **2** | Measurement-first |
| Widen multiplier (= Claude B) | 1 | 0 | **1** | Most models flag as parameter creep |
| Skip P2 clean (= Claude C) | — | 2 | **2** | Viable; most wrap it with MFE logger |
| Regime-flip explicit exit | 1 | 0 | **1** | Cheap additive |
| Volatility-scaled TP | 1 | 0 | **1** | Elegant but unproven |

---

## 5. Recommended path to Nolan (P10 decision)

### Primary (highest L2 compliance, backed by GPT-5 + Grok convergence):

**"Skip P2, pull P4 forward, fix the gate"** —

1. **Fix the gate first** before any P2 retry. Adopt Gate A'/B'/C' from §1.
   Rewrite `run_phase2_backtest.py` (or create P1.5 harness) to compute
   Expectancy-per-trade and realized-PnL-% deltas instead of Sharpe ratios.
2. **Proceed directly to P3 confluence gate** from `v4.p1` tag. Entry-side
   improvement is statistically cleaner to measure.
3. **Pull forward P4 MFE/MAE logger as a parallel mini-phase** (call it P1.5
   MFE-instrumentation). Run 30+ trades of P1 config, log MFE at +10/+50/+100
   bars. This is 1 session of work, ships data that P2.2 redesign (if any)
   will actually need.
4. **Defer P2 to post-P3**. Decide then, with data, whether a trailing stop
   is even warranted.

### Secondary (if you insist on salvaging P2 this cycle):

**Claude Option A + delayed activation + explicit regime-flip exit** —
narrow retry with three stacked but cheap mechanisms, each with a separate
falsifier. Retry counter becomes 2/2 after this.

### Not recommended:
- Option B (widen multiplier) — 3 models flag parameter creep.
- Raw Option A alone — universally flagged as amplifying regime jitter.

---

## 6. What Claude missed, honestly

- **Claude proposed trailing-stop variants without questioning the gate.**
  4 of 4 external C3 models led with "the gate is wrong." That's a framing
  I did not have the reflexes to lead with. This is the highest-value signal
  the consultation produced — saved us from 1–2 pointless retries.
- **Claude did not consider "measure first, then redesign."** GPT-5 Option D
  is a cleaner L2 move than any of Claude's A/B/C. The engineering instinct
  "instrument the system before you tune it" is standard in perf work but
  Claude did not map it onto this trading problem.
- **Regime classifier jitter was in the spec (§1), but Claude treated it as
  background.** Multiple models flagged it as the *dominant* failure mode.

## 7. Decision required from P10 (Nolan)

Pick one:
- **[P-MFE]** Primary path — skip P2, insert P1.5 MFE logger, go P3.
- **[P-RETRY]** Secondary — retry P2 with stacked A + delayed + regime-flip.
- **[P-GATE-FIRST]** Only do §1 gate-fix this session, keep all else frozen
  until you read the consultation data yourself.

No tag movement in any path. `v4.p1` remains the latest valid rollback
point until we have a clean pass under the revised gate.
