# The Leap Crypto VM MoE / ATA Room

Read-only local accountability room for the TradingView **The Leap Crypto — May 2026** contest.

It answers the daily propfirm questions without pretending to trade:

- who/what followed discipline today?
- which setup/symbol/regime made the most realized PnL today?
- why did the best/worst rows win or fail?
- are we still qualified against The Leap activity rules?

The current journal schema has no `trader_id`, quantity, notional, or TradingView account marker, so the room ranks **setups/symbols/regimes**, not literal people, and it uses `pnl_pct` / R multiple rather than leaderboard dollars.

## Launch

From repo root:

```bash
cd /Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/data_algo
python3 -m propfirm_engine.leap_moe_room --date today
```

Compact version:

```bash
python3 -m propfirm_engine.leap_moe_room --date today --quiet
```

Rank by a different bucket:

```bash
python3 -m propfirm_engine.leap_moe_room --date today --group-by symbol
python3 -m propfirm_engine.leap_moe_room --date today --group-by regime
python3 -m propfirm_engine.leap_moe_room --date today --group-by session_hour
```

Watch mode, useful while the contest is open:

```bash
python3 -m propfirm_engine.leap_moe_room --date today --quiet --watch-seconds 900
```

Machine-readable output:

```bash
python3 -m propfirm_engine.leap_moe_room --date today --json
```

## The fixed contest assumptions

The tool hard-codes the May 2026 The Leap Crypto rules that matter to the journal audit:

- competition window: 2026-05-01 12:00 UTC → 2026-05-15 23:59:59 UTC
- starting balance: $100,000 virtual USD
- crypto leverage: 10:1
- commission: 0.01%
- score basis: realized PnL on closed positions
- prize qualification: activity on at least 3 UTC days
- TradingView can restrict Paper Trading after 60+ transactions with orders/positions per minute
- official instruments only:
  - `COINBASE:BTCUSDC.P`
  - `COINBASE:ETHUSDC.P`
  - `COINBASE:SOLUSDC.P`
  - `COINBASE:XRPUSDC.P`
  - `COINBASE:DOGEUSDC.P`

## Safety contract

This is deliberately boring in the right places:

- opens SQLite with `mode=ro`
- sets `PRAGMA query_only=ON`
- never initializes or mutates the DB
- no broker/API/network imports
- no dynamic code execution
- no trade entry suggestions
- every output starts with a read-only / no-trades-executed header

It is an ATA room, not an execution agent.

## Verification

```bash
cd /Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/data_algo
python3 -m py_compile propfirm_engine/leap_moe_room.py propfirm_engine/tests/test_leap_moe_room.py
pytest propfirm_engine/tests -q
python3 -m propfirm_engine.leap_moe_room --date today --quiet
```
