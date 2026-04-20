# Phase 2 Report — ATR Chandelier Trailing Stop

**Date**: 2026-04-20
**Branch**: `v4/p2-atr-trail`
**Tag**: **NOT TAGGED** — pass gate FAILED
**Predecessor**: `v4.p1`

---

## Pass Gate — FAIL

| Gate | Criterion | Result | Verdict |
|------|-----------|--------|---------|
| A | P2 Sharpe ≥ P1 Sharpe × 1.15 on ≥ 5/8 | 2/8 | **FAIL** |
| B | No scenario P2 Sharpe < P1 Sharpe × 0.90 | 6 violations | **FAIL** |
| Overall | A ∧ B | | **FAIL** |
| C (extra) | Baseline integrity (negative-Sharpe count) | 5/8 still negative | ⚠️ **FLAG P10** |

Raw JSON: `reports/phase_2_raw.json`

## Scenario Matrix

| Scenario    | P1 trades | P1 Sh | P2 trades | P2 Sh | Gate A | Gate B | Strict improved | P2 neg |
|-------------|----------:|------:|----------:|------:|:------:|:------:|:---------------:|:------:|
| BTCUSDT 1h  |  9 | −4.85 |  9 | −5.45 | ✅* | ❌ | ❌ | ⚠️ |
| ETHUSDT 1h  |  8 | −1.50 |  8 | −4.28 | ❌ | ❌ | ❌ | ⚠️ |
| SPY 1h      |  5 | +2.19 |  5 | +0.72 | ❌ | ❌ | ❌ | ok |
| QQQ 1h      |  4 | −2.00 |  4 | +3.41 | ✅ | ✅ | ✅ | ok |
| GC=F 1h     |  5 | +0.85 |  5 | −2.65 | ❌ | ❌ | ❌ | ⚠️ |
| CL=F 1h     |  3 | +19.94 |  3 | +19.94 | ❌ | ✅ | — | ok |
| EURUSD=X 1h |  5 | +5.22 |  7 | −2.87 | ❌ | ❌ | ❌ | ⚠️ |
| BTCUSDT 15m |  7 | −0.18 |  7 | −1.00 | ❌ | ❌ | ❌ | ⚠️ |

\* Gate A for BTCUSDT 1h passes vacuously because `P1 Sh × 1.15` is more negative
than `P1 Sh` when P1 is negative — a mathematical artefact of the spec phrasing.
The strict-improvement column is the honest signal here.

## Root-Cause Analysis

**Only 1 of 8 scenarios (QQQ 1h) genuinely improved.** ATR trailing hurt 6 of 7
positive or near-positive scenarios.

Three hypotheses for what went wrong:

### H1 — 1.5× ATR is too tight on low-ADX range regimes
EURUSD=X 1h dropped from +5.22 → −2.87 while keeping the same *entry count*
(actually went 5→7, so more entries). The trail fires inside normal range
noise, cutting winners before they hit TP. FX and gold on the 1h are
range-y; their normal intrabar swing approaches the 1.5× ATR band.

### H2 — Trail breaks the 1:2 R:R that fixed SL/TP embedded
P1 used sl=1.5% / tp=3.0% — a 1:2 R:R. ATR trail makes the realized risk
*dynamic* while TP stays 3.0%. Result: trade is cut at small loss or
small profit more often, destroying the edge that P1 had on scenarios
where the fixed 1:2 was actually working (FX, SPY, GC=F).

### H3 — Works for trend, hurts range
QQQ 1h is the only "trend-y" winner in the matrix after P1 filtering.
ATR trail is a trend-following tool. Forcing it on range regimes is
fighting the strategy.

**Strongest candidate: combined H1+H3.** Trail should be regime-conditional:
enabled on `strong_long` / `strong_short` / `weak_trend`, disabled (fall
back to fixed SL) on `mm_range` / `range_divergent`.

## Decisions

**No tag** — spec §6 says tags are immutable rollback points. v4.p2 would
imply pass gate met; it is not.

**Work preserved on branch** — `v4/p2-atr-trail` keeps the atr_utils and
engine.py changes so a redesigned P2.1 can cherry-pick the clean ATR
primitives. The chandelier functions themselves are correct; the
application site is the problem.

**Retry counter** — this is attempt 1 of allowed 2 (per P10 strategy
§Autoagent Loop). A redesigned P2.1 is allowed before escalation.

**trade_state_machine.py deleted** — still correct per spec §4.1
irrespective of pass-gate outcome. BE logic remains excluded.

## Recommended P2.1 (awaiting P10 human decision)

Option A (narrow fix, L2-compatible):
- Make ATR trail regime-conditional per H3. Add one-line check
  `if regime in ("strong_long","strong_short","weak_trend")` before
  entering the trail branch; range regimes keep fixed SL.
- No new parameters. No multiplier sweep.
- Expected: preserves EURUSD=X / FX gains, captures QQQ improvement.

Option B (2.5× widen for range only):
- Keep trail always on, but widen mult to 2.5× in range regimes.
- Adds parameter. L2-risky — opens door to tuning.

Option C (rollback fully to P1, skip P2 entirely):
- Accept that ATR trail doesn't fit regime_v3_volume's entry model.
- Go straight to P3 confluence gate (smaller scope per spec).
- Tradeoff: drops MFE leakage measurement that P4 needs. May need
  to reintroduce some form of exit optimization at P4.

## L2 Discipline Compliance

- ❌→✅ Did not declare pass despite 1/8 genuine improvements.
- ✅ Did not sweep multiplier values looking for a config that passes.
- ✅ Did not move the v4.p2 tag onto a failing commit.
- ✅ Escalated honestly to P10 (human) for redesign decision.
- ✅ Preserved atr_utils.py correctness separately from application bug.
