# Phase 3 Report — 3-of-3 Confluence Gate

**Date**: 2026-05-13
**Branch**: `v4/p3-confluence` (off `v4.p1`)
**Tag**: **NOT TAGGED** — pass gate FAILED
**Predecessor**: `v4.p1`
**Attempt**: 1 of 2 retry budget

---

## Pass Gate — FAIL

| Gate | Criterion | Result | Verdict |
|------|-----------|--------|---------|
| A | P3 Sharpe non-regressing vs P1 on ≥ 5/8 | 4/8 | **FAIL** |
| B | P3 trade count ↓ ≥ 50% vs P1 (mean ratio ≤ 0.50) | mean 0.17 (83% reduction) | **PASS** (overshot) |
| C | Aggregate P3 WR ≥ 65% | 50.0% over 10 P3 trades | **FAIL** |
| Overall | A ∧ B ∧ C | | **FAIL** |

Raw JSON: `reports/phase_3_raw.json` (gitignored runtime artefact).

## Scenario Matrix

| Scenario     | P1 trades | P1 Sh   | P1 WR  | P3 trades | P3 Sh   | P3 WR  | Sharpe non-reg | filtered_confluence |
|--------------|----------:|--------:|-------:|----------:|--------:|-------:|:--------------:|--------------------:|
| BTCUSDT 1h   | 10        | −3.42   | 30.0%  |  0        | 0.00    | —      | ✅ (vacuous)*  | 12                  |
| ETHUSDT 1h   | 11        | +8.20   | 63.6%  |  **3**    | **+13.08** | **66.7%** | ✅          | 8                   |
| SPY 1h       |  5        | +2.19   | 60.0%  |  4        | +1.50   | 50.0%  | ❌             | 1                   |
| QQQ 1h       |  3        | −2.63   | 33.3%  |  0        | 0.00    | —      | ✅ (vacuous)*  | 3                   |
| GC=F 1h      |  5        | +6.79   | 40.0%  |  0        | 0.00    | —      | ❌             | 5                   |
| CL=F 1h      |  2        | +18.95  | 100.0% |  0        | 0.00    | —      | ❌             | 2                   |
| EURUSD=X 1h  |  4        | +6.30   | 75.0%  |  0        | 0.00    | —      | ❌             | 5                   |
| BTCUSDT 15m  | 12        | −5.05   | 41.7%  |  **3**    | **+7.83** | 33.3%  | ✅            | 10                  |

\* "Vacuous non-regression": 0-trade Sharpe of 0.00 is technically ≥ a negative P1 Sharpe.
The strict test of the gate's intent is whether **positive-baseline** scenarios stayed positive.

## What Worked

Two scenarios showed exactly the spec's promised shape — **fewer trades,
higher quality**:

- **ETHUSDT 1h**: 11 trades → 3 trades, Sharpe +8.20 → **+13.08**, WR 64% → 67%.
  Trade count down 73%, win rate up, Sharpe up by ~5. Textbook
  confluence behaviour.

- **BTCUSDT 15m**: 12 trades → 3, Sharpe −5.05 → **+7.83**. The
  scenario flipped from net-losing to net-winning. WR fell to 33%
  but with only 3 samples on a previously losing baseline, the
  Sharpe-positive flip is the relevant signal.

These two prove the confluence-gate code is doing something coherent
when there's enough trade flow to evaluate.

## What Broke

**Five of eight scenarios produced ZERO P3 trades** (BTCUSDT 1h, QQQ 1h,
GC=F 1h, CL=F 1h, EURUSD=X 1h). The confluence gate killed every
candidate entry. This is the dominant failure mode.

Looking at the `filtered_confluence` counter (entries blocked by P3
but otherwise valid):

- EURUSD=X had 5 entries blocked by confluence, 0 trades survived.
  P1 had 4 trades at WR 75% — these were good trades. The 2-of-2
  FX path requires both HTF + level. With only 4 P1 trades over
  1500 bars, the 2 required signals essentially never coincided
  in the same direction at the same bar.

- GC=F similarly: 5 entries blocked, P1 had 5 trades at +6.79 Sharpe.

## Root Cause: structural conflict between two of the three signals

`level_proximity` requires close to be within 0.5 × ATR of either
prev-session VWAP, prev-session high, or prev-session low. `s1_trend_ema`
(the strategy that drives `strong_long` / `weak_trend` / `strong_short`
entries) fires when **price is breaking out** from those very levels
— that's what makes the bar trend-y. The two signals are anti-correlated
by construction:

  *Trend entries push away from levels; confluence requires being near them.*

`s6_mtf_combo` (the `mm_range` strategy) is the only fit for the
current level_proximity definition, but mm_range regimes are themselves
rare in our 1500-bar fetched datasets.

CVD slope (signal 3) is a separate concern — for FX where it's skipped,
the gate becomes purely HTF + level, so the level conflict bites
directly without redemption.

## Why this is NOT a "trail-style" tuning failure

In P2 attempts 1+2 the failure was about over-tight ATR multipliers
cutting winners. The fix space was clearly continuous (widen the mult).
Here the failure is **categorical** — the chosen level definition
fights the entry generator. No multiplier-style sweep recovers it.

This is the difference between "wrong knob setting" (P2) and "wrong
shape of the gate" (P3 attempt 1).

## Decisions

**No tag** — gate not met. v4.p3 does not exist.

**Retry budget** — this is attempt 1 of allowed 2. A redesigned
P3.1 is allowed before escalation.

**Work preserved on branch `v4/p3-confluence`** — engine.py wiring,
`confluence_scorer.py` helpers, `atr_utils.py`, and 56 passing tests
all stay. A P3.1 design only needs to revise the level signal.

## Recommended P3.1 (awaiting P10 / user decision)

Three options:

**A. Redefine "level proximity" for trend regimes**
Instead of "close near prev H/L/VWAP" (which conflicts with trend
push-through), use "close is breaking BEYOND the prev H/L by ≥ 0.25 × ATR".
That's still a level signal but aligns with the entry's intent. Range
strategies (`s6_mtf_combo`) keep the old definition.

**B. Replace level signal with second-momentum signal**
Drop level_proximity, use a second momentum check (e.g. RSI > 50 for
long, < 50 for short, on a different period). Confluence becomes
HTF + momentum + CVD instead of HTF + level + CVD. Closer to the
spirit of "three independent confirmations".

**C. Loosen FX to 1-of-2 (HTF only)**
The FX scenarios were the worst losers (EURUSD=X +6.30 → 0). FX has
already given up CVD per spec Risk C; degrading further to just HTF
might be the right call given the structural data limit (Yahoo FX
volume is unreliable).

## L2 Discipline Compliance

- ✅ Did not declare pass on 2/8 genuine wins.
- ✅ Did not sweep threshold parameters looking for a config that passes.
- ✅ Did not move the v4.p3 tag.
- ✅ Reported the structural conflict (vs trying to fit a tuning
  narrative onto a categorical failure).
- ✅ atr_utils.py and confluence_scorer.py both keep their unit tests
  green independent of phase outcome.
