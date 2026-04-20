# Phase 1 Report — Session Filter

**Date**: 2026-04-20
**Branch**: `v4/p1-session-filter`
**Tag**: `v4.p1` (on pass)
**Predecessor**: `v4.p0` (scaffolding)

---

## Pass Gate

| Gate | Criterion | Result | Verdict |
|------|-----------|--------|---------|
| A | Filtered Sharpe ≥ baseline Sharpe on ≥ 5/8 scenarios | 7/8 non-regressing | **PASS** |
| B | Filtered trade count ≤ 60% of baseline (↓ ≥ 40%) | mean ratio 0.53 | **PASS** |
| Overall | A ∧ B | | **PASS** |

Raw JSON: `reports/phase_1_raw.json`

## Scenario Matrix

| Scenario    | Base trades | Base Sharpe | Base PnL % | Filt trades | Filt Sharpe | Filt PnL % | Trade ratio | Sharpe non-regress |
|-------------|------------:|------------:|-----------:|------------:|------------:|-----------:|------------:|:------------------:|
| BTCUSDT 1h  | 10 | −4.34 | −2.9 |  9 | −4.85 | −3.0 | 0.90 | ❌ |
| ETHUSDT 1h  |  9 | −3.71 | −1.2 |  8 | −1.50 | −0.4 | 0.89 | ✅ |
| SPY 1h      | 15 | −0.86 | −0.7 |  5 | +2.19 | +0.8 | 0.33 | ✅ |
| QQQ 1h      | 10 | −5.60 | −3.1 |  4 | −2.00 | −0.7 | 0.40 | ✅ |
| GC=F 1h     | 16 | −3.13 | −2.4 |  5 | +0.85 | +0.3 | 0.31 | ✅ |
| CL=F 1h     | 20 | +0.42 | +0.9 |  3 | +19.94 | +1.9 | 0.15 | ✅ |
| EURUSD=X 1h | 21 | +3.68 | +1.1 |  5 | +5.22 | +0.3 | 0.24 | ✅ |
| BTCUSDT 15m |  7 | −0.18 | −0.0 |  7 | −0.18 | −0.0 | 1.00 | ✅ |

## Observations

1. **Filter effect varies by asset class**. Equities, commodities, and FX
   see 60-85% trade reduction because the v3 engine was emitting signals
   during Asia-only hours when liquidity is thin. Crypto 15m is nearly
   untouched (Wolf Hour is only 1.5 hours out of 24 and at that
   granularity rarely hits).

2. **Baseline is structurally weak** — 6/8 scenarios had negative or
   zero Sharpe before the filter. Session filtering moves several into
   positive territory (SPY, GC=F, QQQ toward zero) but we are not
   working from a strong base. Phase 2 ATR trailing targets this: a
   +15% Sharpe improvement over a negative baseline is still negative.
   Flag for P10 review before accepting P2 pass gate.

3. **CL=F 1h** went from 0.42 → 19.94 Sharpe with only 3 trades. High
   Sharpe on very low sample size is not statistical evidence — DSR
   will heavily discount this. Treat as a data point, not a win.

4. **Single regressing scenario** (BTCUSDT 1h) regressed within noise:
   -4.34 → -4.85 Sharpe, both in a loss regime. Not worth tuning;
   L2 discipline says don't touch session masks post-gate.

## Changes

- `propfirm_engine/session_filter.py` — hour tables for 5 asset classes,
  news calendar parser, `is_tradeable()` combinator.
- `propfirm_engine/engine.py` — `PropfirmEngine` class forked from
  vendor `run_v3_engine` with `session_filter_on` flag wired at entry
  logic (not post-hoc, to preserve state-machine semantics).
- `propfirm_engine/vendor/` — frozen snapshot of
  `strategy_bake_off.py`, `regime_dual_engine.py`, `regime_v3_volume.py`
  for stable cross-branch imports.
- `propfirm_engine/tests/test_session_filter.py` — 26 tests, all pass.
- `propfirm_engine/run_phase1_backtest.py` — pass-gate harness.

## Non-Goals Respected (L2 discipline)

- Did NOT tune session ranges after seeing results.
- Did NOT add a 4th asset class mask to "fix" BTCUSDT regression.
- Did NOT expand news_calendar beyond 2026-04-20 fetch (TODO deferred).
- Did NOT rerun with different bar counts to cherry-pick a better matrix.

## P10 Handoff to P2

**Decision for P10 at P2 pass gate**: if P2 ATR trail delivers +15% on
Sharpe that is still negative on 4+ scenarios, P10 should consider
redesigning rather than advancing — the engine foundation may be wrong
in a way that trailing stops cannot rescue. P2 runner will flag this
condition explicitly.
