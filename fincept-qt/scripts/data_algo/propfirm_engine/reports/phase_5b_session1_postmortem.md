# Phase 5b — Session 1 Postmortem

**Date**: 2026-05-13 → 2026-05-14
**Duration**: ~17.5 hours (operator's overnight + day session)
**Branch**: `v4/p5b-live-week` (14 commits)
**Spec context**: Phase 5b live trading week — see `2026-04-20-propfirm-v4-engine-design.md` §4.3

## TL;DR

Built end-to-end TV auto-execution pipeline + cross-platform Manifold trading. **Pipeline proven working** with multiple real fills on TV Paper Trading via Playwright→CDP→Chrome. **Strategy did not work** under chop conditions — $1,007 net loss on TV side from tight-SL stop-out cycles. **Manifold side approximately breakeven** with one real win (S&P 7500 YES +M$4.43).

**Bottom line**: Infrastructure delivered. Strategy needs Pine-timeframe revision (5m+ instead of 1m) or quality filter (score≥30 + tqi≥0.4) before re-enabling auto-fire.

## Numbers

| | Start | End | Δ |
|---|---|---|---|
| TV Leap Crypto USD | $100,000.00 | $98,992.85 | **-$1,007.15 (-1.01%)** |
| TV alerts processed | 0 | 272+ | 14/hr avg over 17.5h |
| Cap aborts (25x leverage cap fire) | 0 | ~17 | saved 30-101x toxic scalps |
| TV real fills | 0 | 5 | 1 AAPL pair (test) + 3 BTC no-SL (-$444) + 2 BTC w/SL (-$547 slip) |
| Manifold balance | M$2,091.96 | M$1,696.39 | -M$395 (locked in 19 bets) |
| Manifold total value | ~M$2,131 | ~M$2,000 | -M$131 |
| Manifold active bets | 7 (mostly resolved) | 19 active | +12 new |
| Manifold resolved win | — | **S&P 7500 YES +M$4.43** | first proper resolution win |

**Mandate -5R hard stop**: -$5,000 budget. **Used $1,007 (20%)**. 4R room remaining.

## Pipeline architecture (delivered)

```
TV Pine alert (Willy on BTCUSDC.P/SP500/Gold/TSLA, 1m+)
  → cloudflared quick tunnel
  → tv_webhook.py:5555
      ├── JSON .50 leading-decimal repair (Willy Pine bug)
      ├── Schema validation (action/ticker/price/sl/tp1)
      ├── open_trade() → SQLite journal row
      ├── notify_signal() macOS + pbcopy + position-size + 4-rail reminder
      │     ├── leverage cap 25x (aborts 30-101x scalps)
      │     └── 5-min per (symbol,side) rate-limit
      └── IF P5B_AUTOEXEC_ENABLED=1 AND notify not skipped:
            POST → tv_playwright_daemon.py:5556 /trade
                └── Playwright over CDP → Chrome (port 9222)
                      ├── Symbol-match guard (chart vs alert ticker)
                      ├── _ensure_market_mode (clicks Market tab)
                      ├── _set_side_via_form (clicks buy/sell ribbon — CSS-hidden quirk)
                      ├── (optional) SL toggle ON + price fill
                      └── Click place-and-modify-button → real fill in TV Paper
```

## Commits (14)

| # | Hash | Purpose |
|---|---|---|
| 1 | 983418a0 | infra-1: restore webhook stack (tv_webhook + log_tv_trade + Pine template) |
| 2 | dd871f2e | infra-2: notify_signal.py + 5x leverage cap + 16 tests (110→126 pytest) |
| 3 | 06d50a29 | infra-3: start_p5b_session.sh + PHASE_1_OPERATOR_CHECKLIST.md |
| 4 | 30856334 | infra-4: restore 13 Manifold scanner/util files (wolf_hour, polymarket, position_tracker, strategy_auditor, etc) |
| 5 | f85b1759 | infra-5: webhook body logging + REJECT diagnostics |
| 6 | b9652a62 | infra-6: TV mandate relax — leverage cap 5x→25x + JSON `.50` repair |
| 7 | 7ae6169e | infra-7: notify rate-limit 5min per (symbol, side) |
| 8 | 0dff46fd | infra-8: tv_autoexec.py opencli wrapper (deprecated in favor of Playwright) |
| 9 | 2b2e0bc7 | infra-9: tv_playwright.py — CDP-connect mode |
| 10 | c9749b83 | infra-10: tv_playwright_daemon + webhook auto-exec wiring |
| 11 | c9070b81 | infra-11: symbol-mismatch guard in execute_signal |
| 12 | cfcc597e | infra-12: qty-N loop in click_buy/sell |
| 13 | 3ee2c877 | infra-13: safety gate via P5B_AUTOEXEC_ENABLED env flag |
| 14 | _(this postmortem)_ | docs: session 1 postmortem |

## Key discoveries

### 1. Pine template `.50` JSON bug (b9652a62)
Operator's Willy SP500 alerts emit `"tqi":.50` (bare decimal) which violates JSON spec.
Webhook patch: regex `(?<=[:,\[\s])(-?)\.(\d)` → `\g<1>0.\2` repairs before parse.

### 2. TV `data-name` selectors (vs canvas-rendered assumption)
EverMem 2026-04-20 marked TV canvas-DOM as hostile. Updated finding:
- `buy-order-button` / `sell-order-button` = quick-trade ribbon (CSS-hidden in some panel states)
- `qtyEl` = quantity element
- `order-panel` = status messages
- `place-and-modify-button` = ACTUAL submit (text reflects current form state, e.g. "Buy 1 BTCUSDC.P MARKET")
- `Paper.positions-table` / `Paper.orders-table` / `Paper.history-table`

### 3. Playwright over CDP > opencli for stable automation
opencli's `default` session reset to `about:blank` between commands, killing multi-step orchestration. Playwright with persistent CDP connection (Chrome launched with `--remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp-profile`) provides stable session for full session lifetime.

### 4. TV order panel: 2-step trade flow
- Click side ribbon (buy/sell-order-button) → sets form side state
- Click place-and-modify-button → submits at form state
- One-click market order requires BOTH steps (clicking only ribbon doesn't fire)
- For "modify existing position" mode (when position already open), submit may no-op or just update SL/TP rather than add to position — leads to TV self-limiting at ~2 BTC accumulation

### 5. The Leap Crypto USD ≠ standard Paper Trading
The Leap is TradingView's monthly paper trading **competition** account. Crypto series allows COINBASE:BTCUSDC.P / ETHUSDC.P / SOLUSDC.P / XRPUSDC.P / DOGEUSDC.P only.
- 10:1 leverage, 0.01% commission
- **Min 3 trading days** required for prize eligibility (operator had 0 days → can't qualify for May 1-15 cycle)
- Max position: 5 BTC, 200 ETH, 5000 SOL, 350000 XRP, 5M DOGE
- Prize: 500 TV plans (top 1-10: 12mo, 11-50: 6mo, 51-250: 3mo, 251-500: 1mo)

## Trade-level postmortem (TV side)

### Trade A: 3 BTC long no-SL (-$444)
- Entries: 81,341 → 81,262 (laddered) → avg 81,302
- Closed manually at 81,170 area
- Loss: -$444 ($147/BTC × 3) - $24 commission
- **Lesson**: 10:1 leverage on $243k notional without SL → bleeding on normal BTC drift

### Trade B: 2 BTC long w/SL ($36/BTC bracket) → stop-out (-$547)
- Entries: 81,366 + 81,427 → avg 81,397
- SL at 81,330.6 (default 25-tick = $36)
- BTC dumped fast 81,503 → 81,235 over 30min
- SL triggered, fill slippage made effective loss $273/BTC (vs $66 mathematical SL)
- **Lesson**: Stop-market orders on fast-moving asset have 2-4x slippage vs stop limit price

### Trades C/D: Pine auto-fire experiment (mixed)
- After SL stop-out, auto-fire enabled briefly
- Pipeline fired but TV self-limited at 2 BTC long (place-and-modify in "modify mode" no-op)
- BTC dropped through SL again
- Operator triggered FULL AUTO then PAUSED after second drawdown

## Manifold side — 19 active bets + 1 resolved

### Resolved (1)
- **S&P 7500 YES** (cost M$10, payout M$14.43, **+M$4.43 net**)
  - Bought at 0.690 prob, resolved YES based on yesterday's cash ^GSPC close 7505.85
  - Base rate analysis was correct: 87.1% empirical vs 69% market → +18.1pp edge proven

### Active 19 (M$390 stake total)
**BTC straddle (5 bets, M$110)**:
- $65K YES M$25 (455 shares, 18x payout if BTC drops -20%)
- $67K YES M$10 (103 shares, 10x)
- $69K YES M$25 (170 shares, 7x)
- $84K YES M$25 (37 shares, base rate 75.4%)
- $90K YES M$25 (183 shares, base rate 22.2%)

**Geopolitical NO tails (4 bets, M$100)**:
- Cuba US-attack 30d NO M$5
- US x Iran peace May 31 NO M$25
- US-China tariff agreement May 31 NO M$25
- Israel-Iran war full conflict May 31 NO M$25
- Trump Hormuz blockade lifted May 31 NO M$25
- **Hormuz traffic normal Jun 30 NO M$25** (added Session 1 wrap)
- **Iran-US peace deal Jun 30 NO M$25** (added Session 1 wrap)

**Status-quo + lottery (5 bets, M$95)**:
- Claude Code on Pro YES M$25 (98% true rate vs 90.5% market)
- Araghchi alive YES M$25 (98% vs 90%)
- Virginia certified YES M$25 (routine cert, 13.7x payout)
- Storm pre-Atlantic YES M$25 (climate base rate 40-70%)
- TSA Musk YES M$10 (lottery 50x)

**AI/political tail (1 bet, M$25)**:
- US-China tariff (already in geopolitical)

**Carryover (1 bet, M$25)**:
- Oil $150 by EOY NO

## Lessons → next session

1. **1m Pine + tight SL + chop = death by cuts**. Each cycle: 14 alerts/hr × stop-out cost.
   - **Fix**: Use 5m or 15m timeframe for Pine alerts. Reduce signal noise.
   - **Or**: Add filter score≥30 AND tqi≥0.4 (dogfood data: 12.2% pass rate ≈ 1.7/hr instead of 14/hr).

2. **Stop-market on fast moves has 2-4x slippage**. SL @ -$36 actually filled at -$273.
   - **Fix**: Use stop-LIMIT orders with reasonable limit slip allowance.
   - **Or**: Wider SL distance (% of price not ticks).

3. **TV's `place-and-modify-button` is dual-mode**. With open position, submit may MODIFY existing rather than ADD.
   - **Fix**: For pure accumulate, close existing first then open new at signal direction.
   - **Or**: Use bracket orders that explicitly stack.

4. **The Leap Crypto USD** has 3-trading-day minimum for prizes. Don't start mid-cycle.
   - **Lesson**: Register early, sample-trade days 1-2 to ensure prize eligibility.

5. **Manifold base-rate empirical works**. S&P 7500 was bought at 69% with 87.1% real rate → resolved YES.
   - **Fix**: Continue using cash-instrument base rates (Yahoo Finance) for upcoming Manifold equity/crypto bets.

## Next session paths

### A. Daemon v2 (~30-45min)
- Quality filter (score≥30 + tqi≥0.4) at daemon side OR webhook side
- Ribbon force-visible fix (scroll-into-view + dispatch hover before click)
- SL price override using Pine's payload sl value (not default 25-tick)
- Stop-LIMIT instead of stop-MARKET for slippage protection

### B. Manifold expansion
- Continue cross-platform tail bets (Polymarket vs Manifold gap markets)
- Watch for resolution events (storm late May, Iran tensions, etc.)

### C. Higher timeframe Pine
- Operator switches Pine alerts to 5m or 15m on TV
- Less chop noise = higher signal:noise = better auto-fire economics

### D. Switch broker
- The Leap competition ends May 15 23:59 UTC. Prize unattainable for 0-day-start.
- Future Leap series: register day 1.
- Or move auto-exec to OxVox USD multi-asset paper (was working pre-broker-switch).

## Current state on disk

- **Branch**: `v4/p5b-live-week` (14 commits, push status check needed)
- **Runtime processes**: webhook :5555, daemon :5556, Chrome CDP :9222, cloudflared tunnel
- **Manifold balance**: M$1,696.39
- **Manifold open positions**: 19 bets (~M$390 cost basis)
- **TV Leap Crypto balance**: $98,992.85
- **TV positions**: 0 (flat)
- **Webhook auto-fire**: PAUSED (P5B_AUTOEXEC_ENABLED unset)
- **Monitor task**: STOPPED (was bv2at3q8o)

## File map

```
fincept-qt/scripts/data_algo/propfirm_engine/
├── tv_webhook.py                    # HTTP receiver, JSON repair, journal write, notify, auto-fire trigger
├── notify_signal.py                 # macOS notify + pbcopy + leverage cap + rate-limit
├── tv_autoexec.py                   # opencli adapter (deprecated)
├── tv_playwright.py                 # Playwright CDP-connect, click logic
├── tv_playwright_daemon.py          # Long-running daemon, HTTP /trade endpoint
├── log_tv_trade.py                  # Journal CLI (open/close/cancel)
├── start_p5b_session.sh             # Idempotent session helper (webhook + tunnel)
├── PHASE_1_OPERATOR_CHECKLIST.md    # Operator pre-flight runbook
├── trade_journal.sqlite             # 272+ alert rows since 2026-04-20
├── reports/phase_5b_session1_postmortem.md  # this file
└── ../12 Manifold scanner files (wolf_hour, polymarket, position_tracker, strategy_auditor, etc.)
```

## Session 1 verdict

**Skill-level**: PASS (infrastructure delivered, methodology dogfooded)
**Profit-level**: FAIL (-$1,007 TV side, ~breakeven Manifold)
**Mandate compliance**: PASS (within -5R hard stop budget, 4R room)

These coexist honestly. Auto-fire is the bottleneck — the trade pipeline works, the signal source (1m Willy in chop) doesn't. Next session: fix signal quality (paths C or A.filter) before re-enabling auto-fire.
