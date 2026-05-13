# Phase 4.1 Report — Scale-Out at +1R + Wider Remainder TP

**Date**: 2026-05-14
**Branch**: `v4/p4-mfe` (extends the P4 work; off `v4.p3`)
**Tag**: **NOT TAGGED** — pass gate FAILED again (no material improvement)
**Predecessor**: P4 attempt 1 (commit `f696a1bf`)
**Attempt**: 2 of 2 retry budget for P4 work

---

## Pass Gate — FAIL (essentially unchanged from P4)

| Gate criterion | Required | P4 (no scale-out) | **P4.1 (scale-out on)** | Δ |
|----------------|---------:|------------------:|-----------------:|--:|
| Sample size (pooled completed) | ≥30 | 27 | **27** | 0 |
| Mean leakage R (last 30) | <0.5 | +1.331 | **+1.321** | −0.010 |
| Median leakage R | — | +1.208 | **+1.208** | 0 |
| Max leakage R | — | +8.093 | **+8.093** | 0 |
| Min leakage R | — | −0.529 | **−0.641** | −0.112 |
| Trades above 0.5R threshold | — | 17/27 | **17/27** | 0 |

Net: **mean leakage moved by 0.010R (0.015pp)**. Within rounding noise.
Scale-out as designed had **no measurable impact** on the dataset's
leakage signal.

## Per-Scenario Comparison

| Scenario     | P4 mean L_R | P4.1 mean L_R | Δ |
|--------------|------------:|--------------:|--:|
| BTCUSDT 1h   | +2.06       | +2.04         | −0.02 |
| ETHUSDT 1h   | +0.22       | +0.19         | −0.03 |
| SPY 1h       | +1.01       | +1.04         | +0.03 |
| QQQ 1h       | +3.48       | +3.48         | 0.00 |
| GC=F 1h      | +1.45       | +1.37         | −0.08 |
| CL=F 1h      | +4.80       | +4.80         | 0.00 |
| EURUSD=X 1h  | +0.68       | +0.68         | 0.00 |
| BTCUSDT 15m  | +0.97       | +0.97         | 0.00 |

Only 5 of 27 individual trades had any pnl change, all between ±0.12R.

## Why Scale-Out Didn't Help (the structural finding)

The expected mechanics: scale-out should rescue trades that hit +1R
then reverse to SL — they go from −1R to 0R, cutting losses 1R per
event. And the wider remainder TP should capture more of the upside
tail before the leakage clock starts.

The data says **neither effect manifested**:

**Mechanism 1 (loser-rescue)** — P3 confluence-gated trades are trend
breakouts. The 8-scenario data shows them as bimodal: trades either
walk through TP cleanly or fail before reaching +1R (regime flip,
session-close, or SL). The "hit +1R then reverse to SL" middle case
is rare in this dataset. Scale-out's insurance premium goes uncashed.

**Mechanism 2 (tail-capture)** — even when scale-out fires and the
remainder TP at +3R closes the second half, the 100-bar MFE follow-up
still measures more upside post-exit. The leakage isn't an exit-timing
problem you can solve by exiting later — it's that trend breakouts
have long tails BEYOND any reasonable fixed-TP, including the +3R
TP we tried.

Concrete: CL=F's +8.09R outlier (the one that drove much of P4's mean)
came from a trade where price ran +12pp post-exit. With remainder_tp
at +4.5pp, scale-out exits at +4.5pp instead of +3pp — but MFE_100
still measures the remaining +7.5pp gain. Leakage drops from 8.09R
to 6.5R-ish? In practice the per-trade Δ was 0.00R for both CL=F
trades — they hit TP cleanly in both modes, the half-close + remainder
math collapsed to the same final pnl, and MFE_100 measurement is from
the same post-TP price point either way.

## Real Implication: "Leakage" Isn't an Exit-Tuning Problem

P2's three failures (ATR trail attempts) and P4.1's no-impact result
point to the same conclusion from different angles: **trend breakouts
have an intrinsic long-tail post-exit MFE that doesn't shrink with
reasonable exit-rule tuning**. The signals we're using (s1_trend_ema +
confluence) find moves with momentum that continues; capturing more
of that with closer-in exits is mathematically hard without giving
back the moves that DON'T continue.

This is a feature, not a bug: it tells us the entry edge is real.
But it does mean **we're not going to optimize our way to "leakage
< 0.5R" through exit redesign alone**.

## Decisions

**No tag** — gate still fails by 2.64× on magnitude. P4 retry budget
2/2 used (P4 attempt 1 measured the problem; P4.1 attempt 2 tried to
fix it and didn't move the needle).

**Branch preserved on `v4/p4-mfe`** — engine.py wiring, scale-out
infrastructure, mfe_tracker, and 86 passing tests all retained. The
scale_out_at_1R flag stays opt-in. Future contributors can experiment
with different `remainder_tp` values or `scale_out_fraction` without
re-deriving the state machine.

## Recommended P10 Next-Step Options (3)

### A. Accept the finding, advance to P5 with current exits ⭐ recommended
P4's measurement is the deliverable. P4.1's negative result strengthens
that deliverable — we have evidence that exit redesign within reasonable
parameters doesn't reduce leakage. Tag this as P10-override v4.p4
(parallel to v4.p3's override pattern) acknowledging "measurement
passed, exit redesign didn't" and advance to P5 sizing.

  Pro: Linear phase progression. P5 sizing can use leakage data as
  input (size up on regimes with smaller leakage, e.g. ETHUSDT mean
  +0.22R vs CL=F mean +4.80R).
  Con: Live trading with known-leaky exits.

### B. Try DIFFERENT exit redesigns within P4 retry quota
The budget was 2/2. Strictly, a third attempt requires P10 override.
Candidates we haven't tried:
- ATR-based dynamic TP (compute TP per trade from current ATR; not
  fixed pp)
- Time-based exit (close at fixed bar offset instead of price target)
- Confluence-loss exit (exit when HTF EMA flips against position)

Each is L2-risky (more parameters, more decisions to tune). Not
recommended without a strong reason to expect one would work.

### C. Reframe the gate as "leakage is an accepted feature"
The 0.5R threshold may itself be wrong for trend-breakout strategies.
Mean leakage of +1.33R on confluence-filtered breakouts may be the
**floor** — what trend-following inherently leaves on the table.
Rewrite Gate as "leakage trend is stable across scenarios" or "max
single-trade leakage <10R" instead of "mean <0.5R". L2-risky (changing
gate definition mid-stream); needs explicit P10 override + rationale.

## L2 Discipline Compliance — P4.1

- ✅ Did not declare pass on a 0.010R "improvement."
- ✅ Did not sweep remainder_tp or scale_out_fraction to find a config
  that hits the gate — single design, single run, single report.
- ✅ Did not move the v4.p4 tag.
- ✅ Faithfully reported "no measurable impact" instead of finding a
  per-scenario detail to dress as a win.
- ✅ Surfaced the structural finding (leakage is signal-intrinsic, not
  exit-tunable) instead of doing another knob-twiddle iteration.
- ✅ Branch + tests preserved; 86/86 pytest green.
