# Wall Street Ultra Wolf Mode — Manifold Leagues Harness

**Status:** approved by Nolan 2026-05-17 (delegated §2-§5 review to claude)
**Branch:** `worktree-p5b-tv-selector-fix` → `v4/p5b-live-week`
**Owner:** auto-driven by launchd, observable via wolf_ledger.jsonl

## Problem

- Nolan is in Manifold League Season 37 (Mega Leviathans / Bronze, #92, −M$2)
- 15d 4h 20m until season ends (2026-06-01-ish)
- Goal floor: +M$100 profit (Bronze promotion threshold)
- Goal stretch: top 2 in Mega Leviathans (≥+M$470) → promote to Gold + USD prize
- Capital allotted: **M$300** (52% of M$581 balance); M$281 untouched as safety
- Constraint: Nolan operates from phone, asynchronously; harness must self-drive

## Non-goals

- Not building a general-purpose trading bot — single-season, scoped to this league
- Not winning every market — winning *enough* over 720 cycles
- No new scanners — reuse existing (`wolf_hour_scanner`, `commodity_scanner`, `political_calendar_scanner`, `earnings_reaction_scanner`, `strategy_auditor`)

## Architecture — Approach C (Hybrid Python conductor + Claude auditor)

```
launchd com.fincept.wolf-mode (every 30min, :13)
  └── wolf_mode_runner.py
       ├─ scan_phase()    — parallel HTTP (existing scanners + Manifold v0)
       ├─ score_phase()   — strategy_auditor edge × kelly × liquidity × time
       ├─ filter()        — drop <0.3, mech-bet 0.3-0.7, escalate >0.7
       ├─ auditor_phase() — Claude subagent for ≤5 high-score candidates
       ├─ execute_phase() — POST /v0/bet (gated by AUTOEXEC_WOLF_LIVE env)
       └─ audit_phase()   — close-out P&L, calibrate score weights vs reality
```

## Lane economics

| Setting | Value | Reason |
|---------|-------|--------|
| Cycle cadence | 30 min | 720 cycles in 15d, Manifold rate-limit safe, low LLM cost |
| Active lanes | 20 | manageable position book |
| Watchlist lanes | 30 | conditions not met yet, upgrade on trigger |
| Avg per-lane | M$15 | meaningful P&L (Manifold slippage > M$5) |
| Single bet max | M$30 | concentration cap |
| Single market max | M$50 | one market never eats >16% of pool |
| Daily new bets cap | M$60 | M$300 / 5 days; pacing |
| Total exposure cap | M$300 | hard wall |
| Drawdown kill | −M$80 from peak | auto-pause 12h |

## 3-phase season cadence

- **Days 1-3 (probe)**: dry-run first 24h, then live with bet caps halved (M$15 daily). Calibrate score vs realised PnL.
- **Days 4-10 (scale)**: full caps. Sources that proved +EV in probe get 2x weight.
- **Days 11-15 (sprint)**: if leaderboard rank ≥ top 5, hold; else uncap single_bet_max → M$60 to chase.

## Data sources (scan_phase)

Parallel fetch, dedupe by `contractId`:

| Source | Reuse | What it gives |
|--------|-------|--------------|
| `wolf_hour_scanner.py` | existing | low-liquidity mispricing in Manifold |
| `political_calendar_scanner.py` | existing | election/treaty/legislation events |
| `commodity_scanner.py` | existing | oil/gold/wheat tied to live data |
| `earnings_reaction_scanner.py` | existing | earnings-window markets |
| `manifold_market_widener.py` | **NEW** | Manifold v0/markets `sort=newest` + `sort=score` + Mega Leviathans group |

`manifold_market_widener.py` is ~80 lines. Just paginates Manifold API + dedupes + normalizes to common candidate dict.

## Scoring (score_phase)

Uses existing `strategy_auditor.audit()` interface. Composite:

```
raw_edge       = abs(my_prob - market_prob)
kelly_fraction = (my_prob × payout − (1−my_prob)) / payout   # capped to 25% half-Kelly
liquidity      = min(1, total_pool / M$500)                  # avoid thin markets
time_decay     = max(0.2, 1 - days_to_close / 30)            # prefer faster resolution
confidence     = inputs from edge_source quality (manual tiers in auditor)
score          = raw_edge × kelly_fraction × liquidity × time_decay × confidence
```

## Auditor gate (auditor_phase)

For candidates with score > 0.7 (≤5 per cycle):

- Subagent prompt: "You are a Manifold sniper auditor. Market: {contract_full}. My score: {score_breakdown}. Last 20 comments: {comments}. Is this BET or SKIP? Reply JSON: {decision, size_mana, rationale_lt_200_words, top_risk}."
- Claude responses parsed; SKIP overrides mechanical decision.
- Subagent failure → mechanical decision proceeds with size halved.

## Execution (execute_phase)

```
for c in to_bet:
  if dry_run: log to ledger, continue
  pre_balance = GET /v0/me
  post = POST /v0/bet {contractId, amount, outcome, expiresMillisAfter:60_000}
  verify: new_bet_in /v0/bets?username=Fearvox&limit=1
  write: wolf_ledger.jsonl {ts, cycle_id, contract, score, decision, action, bet_id, pre_bal, post_bal}
  enforce: rate_limit_sleep(1s)
```

Live gate via `AUTOEXEC_WOLF_LIVE=1` env. First 24h **dry-run forced** regardless of env.

## Audit loop (audit_phase)

- Scan closed positions since last cycle
- For each: compare `score` at entry vs realized PnL sign
- Update `score_calibration.json` weights (gradient: amplify dimensions that correlated with wins)
- Drawdown check: if equity − peak_equity < −M$80, write `wolf_killswitch.flag` (runner refuses to bet next cycle)

## Files

```
fincept-qt/scripts/data_algo/wolf_mode/
  wolf_mode_runner.py            — main orchestrator (~250 LOC)
  manifold_market_widener.py     — new scanner (~80 LOC)
  wolf_ledger.py                 — jsonl + sqlite writer (~60 LOC)
  __init__.py                    — package marker
fincept-qt/scripts/cron/
  wolf_mode_cycle.sh             — launchd entrypoint (mirrors phantom_cleanup_hourly.sh)
~/Library/LaunchAgents/
  com.fincept.wolf-mode.plist    — every 30min :13
```

## Observability

- `wolf_ledger.jsonl` — every decision (dry or live)
- `wolf_audit.sqlite` — closed-position P&L for calibration
- `wolf_daily_brief.md` — daily 22:00Z rollup (rank, P&L, hit rate, top hits, top misses)
- Errors → stderr → `/tmp/wolf_mode.err`

## Safety / kill switches

1. **Dry-run forced first 24h** — no live POST regardless of env
2. **AUTOEXEC_WOLF_LIVE env** required for live mode
3. **Drawdown −M$80 kill** auto-creates `wolf_killswitch.flag`
4. **`wolf_killswitch.flag` exists** → runner skips bet phase
5. **Single bet caps** prevent runaway exposure
6. **Daily cap** prevents day-1 blowup
7. **Manifold API failure** → cycle aborts, retry next cycle
8. **Operator override**: `touch ~/wolf_killswitch.flag` to instantly halt

## Out of scope (this spec)

- Polymarket integration (separate vault per Polymarket paper-validation memory)
- Telegram/Discord notifications (rely on log tail + brief file)
- Auto-rebalance between Manifold and SOL TV positions
- Cross-market hedging
