# Phase 2 Report — ATR Chandelier Trailing Stop

**Branch**: `v4/p2-atr-trail`
**Tag**: **NOT TAGGED** — both attempts failed pass gate
**Predecessor**: `v4.p1`
**Attempts**: 2 of 2 retry budget consumed

---

## Attempt 1 (2026-04-20) — unconditional trail

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

---

## Attempt 2 (2026-05-13) — regime-conditional trail (Option A)

**Branch state**: same `v4/p2-atr-trail` branch, building on attempt 1's preserved
work. Single-line change to `engine.py`: trail branch now requires
`active in TREND_REGIMES` (= `{strong_long, strong_short, weak_trend}`),
otherwise falls back to fixed SL/TP. Added `TREND_REGIMES = frozenset(...)`
constant near other module-level imports for clarity. Zero new parameters,
no multiplier sweep, no spec change.

### Pass Gate — FAIL

| Gate | Criterion | Result | Verdict |
|------|-----------|--------|---------|
| A | P2 Sharpe ≥ P1 Sharpe × 1.15 on ≥ 5/8 | 3/8 | **FAIL** |
| B | No scenario P2 Sharpe < P1 Sharpe × 0.90 | 5 violations | **FAIL** |
| Overall | A ∧ B | | **FAIL** |
| C (extra) | Baseline integrity (negative-Sharpe count) | 3/8 still negative | ok |

### Scenario Matrix

Note: P1 baselines below differ from attempt 1's because market data has
advanced ~22 days; same script, fresh bars. Comparisons within a row
(P1 → P2) are the honest gate signal — cross-attempt Sharpe values are not
directly comparable.

| Scenario    | P1 trades | P1 Sh  | P2 trades | P2 Sh  | Gate A | Gate B | Strict improved | P2 neg |
|-------------|----------:|-------:|----------:|-------:|:------:|:------:|:---------------:|:------:|
| BTCUSDT 1h  |  10       | −3.42  |  10       | −5.15  | ❌     | ❌     | ❌              | ⚠️     |
| ETHUSDT 1h  |  11       | +8.20  |  11       | +2.93  | ❌     | ❌     | ❌              | ok     |
| SPY 1h      |   5       | +2.19  |   5       | −6.15  | ❌     | ❌     | ❌              | ⚠️     |
| QQQ 1h      |   3       | −2.63  |   3       | +3.19  | ✅     | ✅     | ✅              | ok     |
| GC=F 1h     |   5       | +6.79  |   5       | +1.87  | ❌     | ❌     | ❌              | ok     |
| CL=F 1h     |   2       | +18.95 |   2       | +18.95 | ❌     | ✅     | —               | ok     |
| EURUSD=X 1h |   4       | +6.30  |   4       | +15.28 | ✅     | ✅     | ✅              | ok     |
| BTCUSDT 15m |  12       | −5.05  |  12       | −4.59  | ✅     | ❌     | ✅              | ⚠️     |

### Cumulative trade-PnL across all 8 scenarios

| Aggregate | P1 | P2 | Δ |
|---|---:|---:|---:|
| Total pnl% | +4.21% | −0.64% | −4.85% |

L2 hard-stop floor is "cumulative backtest loss > −5R" (≈ −7.5% with 1R≈1.5%
fixed SL). Δ −4.85% is below the trigger. **No automatic hard rollback
required.** P1 remains the safe rollback target if the user chooses.

### Root-Cause Update (Attempt 2)

**Positive evidence for the regime-conditional hypothesis (H1+H3):**
- EURUSD=X 1h: attempt-1 caught −2.87, attempt-2 has +15.28 → +18 Sharpe
  swing. Range-FX scenario fully recovered + improved beyond P1 baseline,
  exactly as H3 predicted (range falls back to fixed SL).
- BTCUSDT 15m: regression of attempt-1 cut from −1.00 → −4.59 narrowed.

**Counter-evidence — trend regimes still hurt:**
- SPY 1h (trend-y, entered in `strong_long`/`weak_trend`): P1 +2.19 → P2 −6.15.
  Trail was on by design. Yet still cut into noise.
- ETHUSDT 1h (mixed): P1 +8.20 → P2 +2.93. Trail fired in a way that
  truncated winners.

The simple "trail = trend tool" model is **partially right but
incomplete**. Trend regimes also have intrabar volatility; 1.5× ATR
chandelier is too tight even there for some symbols. Widening the
multiplier per symbol would tackle this — but that's Option B, which is
L2-blocked (multiplier sweep opens the door to over-tuning).

### Decisions (this attempt)

- **No tag** — gate not met. v4.p2 still does not exist.
- **Retry budget exhausted** — attempt 1 + attempt 2 = 2/2. No further
  auto-iteration per L2 §Autoagent Loop.
- **Branch preserved** — `v4/p2-atr-trail` keeps both attempts' diffs;
  if a future P10 override allows a third design, it can build on
  this engine.py state without re-deriving regime-conditional logic.
- **`atr_utils.py` untouched** — chandelier math unit-tests still 48/48.

### Recommended next steps (awaiting P10 / user decision)

1. **Skip P2 entirely (formerly Option C)** — accept ATR trail does not
   fit `regime_v3_volume`'s entry model. Branch P3 (confluence gate)
   directly off `v4.p1`. Drop trail; reconsider exit optimization at P4
   when MFE leakage data arrives.

2. **Per-symbol mult floor with stage gate** — formalize a per-symbol
   table (e.g. FX 2.5×, equity 1.5×, crypto 1.5×) tested once, locked,
   no online sweep. This is essentially Option B with stricter governance.

3. **Investigate trend SPY/ETHUSDT failure mode first** — is the
   chandelier high/low look-back too short for these symbols? That's a
   parameter outside `mult` so it doesn't violate the "no multiplier
   sweep" rule. But still parameter tuning — needs P10 sign-off.

### L2 Discipline Compliance — Attempt 2

- ✅ Did not declare pass despite 3/8 genuine improvements.
- ✅ Did not sweep multiplier values to find a config that passes.
- ✅ Did not lower gate thresholds.
- ✅ Did not move the v4.p2 tag.
- ✅ Did honestly report counter-evidence (SPY trend failure).
- ✅ Did escalate to P10 with three concrete next-step options.
