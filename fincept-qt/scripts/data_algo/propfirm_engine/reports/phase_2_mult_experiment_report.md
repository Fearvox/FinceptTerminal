# Phase 2 Mult Experiment — Per-Asset ATR Multiplier Table

**Date**: 2026-05-13
**Branch**: `v4/p2-mult-experiment` (off `v4/p2-atr-trail` tip)
**Tag**: **NOT TAGGED** — pass gate still FAILED
**Predecessor**: attempt 2 of `v4/p2-atr-trail` (regime-conditional trail)
**Quota note**: P2's 2 of 2 retry budget had been spent on attempts 1+2.
This experiment is on a **separate side branch**, treated as a one-shot
"is the multiplier the actual problem" probe rather than a fresh retry.

---

## Pass Gate — FAIL (same shape as attempt 2)

| Gate | Criterion | Result | Verdict |
|------|-----------|--------|---------|
| A | P2 Sharpe ≥ P1 Sharpe × 1.15 on ≥ 5/8 | 3/8 | **FAIL** |
| B | No scenario P2 Sharpe < P1 Sharpe × 0.90 | 5 violations | **FAIL** |
| C | Strict improvement (P2 > P1) | 3/8 | — |
| Overall | A ∧ B | | **FAIL** |

## Comparison vs Attempt 2 (regime-conditional, uniform 1.5×)

| Scenario       | Attempt 2 P2 Sh | Mult-exp P2 Sh | Δ      | Mult used | Notes |
|----------------|----------------:|---------------:|-------:|----------:|-------|
| BTCUSDT 1h     | −5.15           | −5.15          |  0.00  | 1.5× crypto | regime fallback didn't trigger trail |
| ETHUSDT 1h     | +2.93           | +2.93          |  0.00  | 1.5× crypto | same |
| SPY 1h         | −6.15           | −6.15          |  0.00  | 1.5× equity | unchanged |
| QQQ 1h         | +3.19           | +3.19          |  0.00  | 1.5× equity | unchanged |
| GC=F 1h        | +1.87           | **+2.75**      | **+0.88** | 2.0× gold | mild improvement |
| CL=F 1h        | +18.95          | +18.95         |  0.00  | 1.5× oil | trail didn't trigger |
| EURUSD=X 1h    | +15.28          | **+9.31**      | **−5.97** | 2.5× fx | **regression** |
| BTCUSDT 15m    | −4.59           | −4.59          |  0.00  | 2.0× crypto-15m | already widened by Risk B |

Five scenarios unchanged because attempt-2's regime-conditional trail
already routes range entries to the fixed-SL branch — the multiplier
just doesn't enter the picture for those bars. The two that DID see the
new multiplier produced opposite signs of change.

## Why EURUSD got worse (the actually-interesting finding)

The hypothesis going in was "1.5× is too tight on FX → widening to 2.5×
should let winners run". The data says the opposite for EURUSD: the 1.5×
trail was firing in a way that **happened to be timed well** with the
specific trade exits in this dataset's 4 FX trades. Widening to 2.5×
gives the trades more rope but in this sample the additional rope is
spent on adverse price action — the trade gives back gains before
finally exiting on the (now wider) trail.

This is a small-N artefact. Across ~4 trades, the 1.5× vs 2.5× choice
is closer to a coin-flip than a real edge difference. But the gate
metric (Sharpe with N=4) is itself coin-flippy. The honest read is:

> The multiplier isn't the bottleneck.

That's the key bit. Attempts 1+2 both blamed "wrong multiplier". Per-
asset tuning of the multiplier — even with L2-style one-shot governance
— produced **net zero gate improvement**. The structural problem must
be elsewhere.

## Decisions

- **No tag** on v4.p2. The experiment didn't move the gate.
- **No further multiplier sweeps.** This was the one-shot per-asset
  experiment; we have its result. Further mult fiddling would violate
  L2 explicitly.
- **Recommendation strengthened: skip P2 entirely (Option C from the
  attempt-2 report's three options).** The trail mechanic and the
  `regime_v3_volume` entry model don't compose. Going to P3 with the
  fixed SL/TP of P1 as the exit rule is the cleanest path forward.
  P4's MFE leakage measurement can substitute for trail-driven exit
  optimization later, with its own dedicated experiment.

- **Per-asset multiplier table itself is preserved** in
  `atr_utils.multiplier_for(asset_class=...)` for two reasons:
  1. The plumbing is correct and tested (5 parametrized cases).
  2. If a future phase adds back trailing in a different shape, the
     per-asset values are documented for re-use without re-deriving
     them.

  Pointer to keep this from rotting: only callers that pass
  `asset_class` see the table. The default 1.5× legacy behaviour
  is unchanged, so no existing code accidentally adopts the table.

## L2 Discipline Compliance

- ✅ Did not declare pass despite GC=F nudge.
- ✅ Did not sweep more multiplier configurations after first run.
- ✅ Did not move the v4.p2 tag.
- ✅ Did not silently treat the side-branch as a "third retry".
- ✅ Reported the counter-evidence (EURUSD got WORSE) honestly.
- ✅ atr_utils unit tests stay green (56/56 propfirm pytest).

## Branch state

`v4/p2-mult-experiment` keeps the per-asset table and its tests. It
does NOT carry the gate-passing implication — the tag remains absent.
v4.p1 stays the safe rollback target; production code can still
choose `cfg.atr_trail_on=False` to get pure P1 behaviour.
