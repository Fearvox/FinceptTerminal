# _attic — retrospective notes on falsified strategies

This is **not** a code attic. It's a place for written retrospectives
about approaches that were tested and falsified. The actual code
modules these documents reference stay in their production locations
(e.g. `data_algo/regime_dual_engine.py`) because **a falsified strategy
does not mean the underlying library is broken** — the `adx` /
`bb_width` / `fetch_any` indicators inside `regime_dual_engine.py` are
still in active use by `regime_v3_volume.py` and `propfirm_engine`.

## Current contents

(retrospective documents to be added)

## The dual-engine retrospective (2026-04 → 2026-05)

The `regime_dual_engine.py` module was originally written to drive a
"dual-engine" trading strategy: switch between a trend-follower and a
range-trader based on ADX + BB-width regime classification. 8-scenario
cross-market testing showed only 2/8 wins versus S6 alone winning 4/8
on the same scenarios. The **strategy** was falsified — keeping two
engines in parallel adds turnover without edge versus picking one and
sticking with it.

What is kept and what is dropped:

- DROPPED: the "switch between engines based on regime" decision logic
  (`run_dual_engine`). Future strategies should not call this — it's
  preserved only so `regime_v3_volume.py` and `propfirm_engine` can
  reuse the indicator primitives.
- KEPT: the indicator functions (`adx`, `bb_width`, `classify_regime`,
  `fetch_any`). These are arithmetic; the falsification was about
  combining them into a trading rule, not about the math.

The earlier short-lived move of the whole file into this `_attic/`
directory was an over-correction; production code (`regime_v3_volume`)
ended up importing from `_attic`, which is a smell. Restored to
`data_algo/regime_dual_engine.py` in iter-4/5 of the v4/p2-atr-trail
stability sweep on 2026-05-12.

See EverMem: `regime-dual-engine-findings.md`,
`strategy-bakeoff-results.md`.
