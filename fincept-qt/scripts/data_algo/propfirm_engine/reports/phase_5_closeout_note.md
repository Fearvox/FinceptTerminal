# Phase 5a Closeout — P10 decision: NO TAG, advance to P5b live

**Date**: 2026-05-14
**Operator (P10)**: Nolan (fearvox1015)
**Decision**: **C — Do NOT create v4.p5a tag; advance to P5b live track**

## Backtest result summary

8-scenario matrix run with session_filter + confluence + pn_sizing all on
(see `phase_5_report.md` for full breakdown):

| | observed | spec target | verdict |
|---|---|---|---|
| Pooled N | 27 | ≥ 30 | **FAIL** (binding) |
| Win rate | 74.1% | ≥ 65% | PASS |
| R:R | 1.32 | ≥ 1.8 | FAIL |
| Overall (N AND (WR OR RR)) | | | **FAIL** |

`pn_sizing` fired **94 blocks** in total (90 on BTCUSDT 15m alone),
demonstrating that the sizing layer activates on short-timeframe crypto
where consecutive-loss patterns occur intraday. On 1h scenarios the
P3-confluence selectivity already prevents most consecutive losses on
the same UTC day, so sizing rarely fires there.

## Why no tag

Operator chose option C over A (override-tag) and B (more bars).

Reasoning recorded for audit: **the P5a backtest gate has done its job
of preparation. The MEANINGFUL gate is P5b live trading milestones**
(per spec §4.3):

- Paper TV: ≥ 10 trades logged with engine signals
- Manifold M$50 → M$1000
- Polymarket $100 → $300 (Binance USDC dependent)
- N ≥ 30 paper trades real-money or paper-side combined
- WR ≥ 65% OR R:R ≥ 1.8 on live N

A tag at this point would say "v4.p5a passed" when the backtest gate
literally failed; the WR-only spirit-of-the-gate read is exactly the
pattern v4.p3 and v4.p4 used, and the operator chose NOT to repeat
that pattern here. The reasoning: P5 is the LIVE phase, not another
backtest phase. Live data is the only valid gate.

## State of v4 tag chain

```
v4.p0  scaffolding              ✅ tagged
v4.p1  session filter           ✅ tagged
v4.p2  ATR trail                ❌ never tagged (3 attempts all failed)
v4.p3  confluence gate          ✅ tagged (P10 override on Gate-B near miss)
v4.p4  MFE measurement          ✅ tagged (P10 override; redesign falsified)
v4.p5  P.N. sizing (P5a)        ⏸  NO TAG (P10 chose C — P5b is the gate)
```

## Infrastructure status (all preserved)

- `propfirm_engine/pn_sizing.py` (146 lines, 20 unit tests)
- `propfirm_engine/engine.py` integration via `cfg.pn_sizing_on`
- `propfirm_engine/run_phase5_backtest.py` (parametrized for 3000-bar
  rerun later if needed)
- pytest baseline: 110/110 green
- Branch: `origin/v4/p5-sizing` pushed (head ff665979)

## P5b — separate track, future session

When operator is ready to start P5b:

1. Branch `v4/p5b-live` off `v4/p5-sizing` (NOT off v4.p4 — keeps the
   sizing infrastructure).
2. Wire TV paper webhook → trade_journal.sqlite ingestion.
3. Configure Manifold API + Polymarket Gamma API (keychain entries
   already exist from prior sessions: `manifold-api`, `evermem-api`).
4. Run paper for 30+ trades with mandate's 4 rails enforced
   (single-ticket M$10/$10 cap, no live $ before N≥10 paper wins,
   hard stop on −5R cumulative, no BE / no I.q. / no 4+ confluence).
5. If P5b gates met → tag `v4.p5b` + v4.0 production release.
6. If gates miss → honest report + P10 decision on redesign path.

**P5b is NOT in scope for the current goalv3-cc invocation.**

## Audit references

- goalv3-cc dispatch log (local, gitignored): `.goal/p5-sizing/dispatch-log.jsonl`
- P5 backtest full report: `phase_5_report.md` (this directory)
- Operator notes (local): `.goal/p5-sizing/operator-notes.md`
- v4 spec: `docs/superpowers/specs/2026-04-20-propfirm-v4-engine-design.md`
- Prior tag override patterns: v4.p3 annotated tag, v4.p4 annotated tag
