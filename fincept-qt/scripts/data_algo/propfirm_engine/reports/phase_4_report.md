# Phase 4 Report — MFE Counterfactual Log

**Date**: 2026-05-14
**Branch**: `v4/p4-mfe` (off `v4.p3`)
**Tag**: **NOT TAGGED** — pass gate FAILED on both sample size AND magnitude
**Predecessor**: `v4.p3`
**Attempt**: 1 of 2 retry budget

---

## Pass Gate — FAIL (both criteria breached)

Spec §4.3 gate: `mean(MFE_100 − realized_pnl) < 0.5R over last 30 trades`,
with 1R = 1.5pp (engine's default fixed SL).

| Gate criterion | Required | Observed | Verdict |
|----------------|---------:|---------:|:-------:|
| Sample size (pooled completed trades) | ≥ 30 | **27** | **FAIL** |
| Mean leakage (R units, last 30) | < 0.5 | **+1.331** | **FAIL** (2.66× over) |
| Overall | both | | **FAIL** |

Raw JSON: `reports/phase_4_raw.json` (gitignored runtime artefact).

## Aggregate Distribution

Across all 27 fully-completed P4-tracked trades (sample window = full pool
because we have < 30):

```
mean    +1.331 R   (+1.996 pp)
median  +1.208 R
max     +8.093 R   (CL=F 1h trade #2 — price ran +12pp post-exit)
min     −0.529 R   (one ETH trade where post-exit went adverse)
trades_above_0.5R = 17 / 27  (63%)
```

**63% of trades leaked ≥ 0.5R**. Mean is 1.331R = roughly **2pp per trade
that we left on the table** by exiting at fixed TP (3%) instead of riding
the move.

## Per-Scenario Breakdown

| Scenario     | Trades | Completed | Mean L_R | Max L_R | Min L_R |
|--------------|-------:|----------:|---------:|--------:|--------:|
| BTCUSDT 1h   |  2     | 2         | **+2.060** | +2.606 | +1.514 |
| ETHUSDT 1h   |  5     | 4         | +0.222  | +1.367 | −0.529 |
| SPY 1h       |  4     | 4         | +1.010  | +1.643 | +0.057 |
| QQQ 1h       |  1     | 1         | +3.482  | +3.482 | +3.482 |
| GC=F 1h      |  4     | 3         | +1.446  | +3.824 | +0.207 |
| CL=F 1h      |  2     | 2         | **+4.800** | +8.093 | +1.508 |
| EURUSD=X 1h  |  4     | 4         | +0.675  | +1.344 | +0.015 |
| BTCUSDT 15m  |  7     | 7         | +0.966  | +1.293 | +0.393 |

**Only one scenario (ETHUSDT 1h, +0.222R) is below the 0.5R threshold.**
Every other scenario averages above — most by a wide margin. CL=F at
+4.8R mean (across only 2 trades) is dominated by the +8.09R outlier.

### Outliers (single-trade leakages ≥ 3R)

```
QQQ 1h    trade #1:  +3.48R   (1 of 1 — entire scenario)
GC=F 1h   trade #2:  +3.82R
CL=F 1h   trade #2:  +8.09R   (price ran +12pp after our TP at +3pp)
```

The CL=F outlier on its own would push any reasonable mean well above
0.5R. It's not a one-off — it's symptomatic of fixed TP=3% being far
short of how far trend moves actually carry.

## What This Result Says (the real signal)

**P4's job is measurement, and the measurement is decisive**: with our
current exit rules (fixed SL=1.5%, fixed TP=3%), we are systematically
leaving money on the table. Mean +1.331R post-exit upside means a typical
trade closes at +3% when it could have made +5% by holding 100 bars more.

This is **categorically different** from a noisy-data fail. The signal
is strong (mean 2.66× the threshold), broad (7 of 8 scenarios average
above), and consistent (median 1.2R, not just outlier-driven).

The gate doing its job means we now have **falsifiable evidence that
the fixed-TP exit policy is suboptimal**.

## Why P2 (ATR Trail) Looked So Bad Earlier, Through This Lens

P2's three failed attempts tried to fix this exact problem (trail-the-
winner instead of fixed TP). All failed because:
- attempt 1: trail too tight on range, cut winners
- attempt 2: regime-conditional trail fixed range but trend still cut
- attempt 3 (per-asset mult): swing-and-miss across symbols

P4 now says: **the problem P2 was trying to solve is REAL** (2pp avg
leakage). P2's failure was about **the specific trail mechanism**
(chandelier 1.5×ATR), not about whether trailing is the right idea.
The data here justifies a different P2-style mechanism (scale-out,
partial close at +1R + ride remainder, or trail only after +1R locked).

## Decisions

**No tag** — N<30 AND mean >> threshold. Per L2 spec, P4 attempt 1 of 2
used. P4.1 is allowed.

**Branch preserved on `v4/p4-mfe`** — engine.py integration, mfe_tracker
module, 14 unit tests, and run_phase4_backtest all retained. The
measurement infrastructure is correct independent of the gate outcome.

**Pytest state**: 74/74 green on this branch.

## Recommended P4.1 (awaiting P10 / user decision)

Three honest paths:

### A. Get more bars and re-run, no engine change
Pull 3000-bar fetches instead of 1500. Should approximately double
the trade count to ~54 pooled, satisfying N≥30. **But the magnitude
problem (1.3R mean) doesn't go away with more bars** — it might
shift slightly but the underlying "fixed TP too tight" signal stays.
This path FORMALLY passes the sample-size criterion but the gate
still fails on mean. Doesn't help.

### B. Redesign the exit rule — P4.1 is the trail-style fix
Use the P4 measurement to justify a real exit-rule change:

  1. **Scale-out at +1R**: close half the position at +1R = +1.5pp,
     hold the rest with trailing stop at breakeven on the remainder.
     Captures the +0.5-1R move (most trades) AND the long tail
     (CL=F-style runners).
  2. **TP at +2R instead of +1R**: simplest possible change. Doubles
     the upside cap from +3pp to +4.5pp. Doesn't capture +8R runners
     but does capture the +1.3R median.
  3. **Combine with regime check**: trend regimes get +2R or trail;
     range regimes keep +1R (where leakage is less — ETHUSDT's
     +0.2R mean came from mixed regime).

  Each is a strategy phase, NOT a pure P4 retry. Strictly per spec
  this should be a new phase tag (P4-exit-redesign or fold into P5).

### C. Accept the leakage finding, freeze exits, advance to P5
Tag this branch (despite gate fail) as **v4.p4-measurement** to mark
that P4 *infrastructure* shipped, leave fixed exits alone, and let P5
(sizing) work with current exits. Leakage data becomes input to P5
sizing decisions (e.g. larger size on trades with regime-conditional
high run-after probability).

  Pro: keeps spec phase progression linear; defers exit redesign.
  Con: signs off on a known suboptimal exit policy for live trading.

## L2 Discipline Compliance

- ✅ Did not declare pass despite gate-as-written failing.
- ✅ Did not silently widen the gate threshold or change the sample window.
- ✅ Did not auto-iterate P4.1 — escalated to P10 with three concrete options.
- ✅ Reported the magnitude faithfully (2.66× over threshold, not "close miss").
- ✅ Connected the dots to P2's failures honestly: the problem P2 was
  trying to fix is real and measured; the failure was about the
  specific mechanism, not the goal.
- ✅ Infrastructure (mfe_tracker, integration, backtest runner) preserved
  with full test coverage.
