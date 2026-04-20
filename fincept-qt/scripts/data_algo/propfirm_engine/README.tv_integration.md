# TradingView → Propfirm Engine webhook setup

End-to-end recipe for auto-logging WillyAlgoTrader paper trades into
`trade_journal.sqlite` via TradingView alerts. Five steps, about 15
minutes total.

## 0. Prerequisites

- TradingView **Premium** or higher (webhook alerts require Premium+).
- WillyAlgoTrader open-source Pine script (confirmed 2026-04-20).
- `cloudflared` installed locally (`brew install cloudflared`) or an
  equivalent tunnel tool (ngrok, tailscale funnel).
- FinceptTerminal repo cloned, working directory at repo root.

## 1. Generate a webhook secret

```bash
export TV_WEBHOOK_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "TV_WEBHOOK_SECRET=$TV_WEBHOOK_SECRET" >> ~/.zshrc   # persist
```

Keep this value — you'll paste it into both the Pine script and the
local server. Do NOT commit it.

## 2. Patch WillyAlgoTrader Pine

Open your WillyAlgoTrader script in TV's Pine editor. Paste the block
from `pine_willy_alert_template.pine` near the bottom, then:

1. Replace `CHANGE_ME_SECRET` with the value from step 1 (both long
   and short blocks).
2. Replace the RHS of `longEntrySignal = false`, `shortEntrySignal = false`,
   `plannedEntryPrice = close`, `plannedStopLoss = 0.0`,
   `plannedTakeProfit = 0.0`, `regimeLabel`, `tqiValue`, `qStrengthValue`
   with the actual variable names your Willy script uses.
3. Click **Save** → **Add to chart**.

If your Willy script already emits long/short state via `strategy.entry()`,
gate the alerts off those conditions instead of recomputing them.

## 3. Launch the local webhook receiver

In one terminal:

```bash
cd fincept-qt/scripts/data_algo
python3 -m propfirm_engine.tv_webhook --port 5555
```

You should see:
```
▶ tv_webhook listening on http://127.0.0.1:5555  (POST /tv-signal, GET /health)
```

Quick sanity check from another terminal:
```bash
curl http://127.0.0.1:5555/health
# → {"ok": true, "ts": "2026-04-20T..."}
```

## 4. Open a tunnel

TradingView's alert servers can't reach `localhost`; expose port 5555
via cloudflared:

```bash
cloudflared tunnel --url http://localhost:5555
```

Cloudflared prints a URL like `https://<random>.trycloudflare.com`.
This is your webhook URL for the next step. The URL is ephemeral
(rotates per run) — for a stable URL, create a named tunnel (see
cloudflared docs).

## 5. Create the TradingView alert

1. In TV chart with WillyAlgoTrader loaded: click the **Alert (⏰)** icon.
2. **Condition**: "WillyAlgoTrader" → **Any alert() function call**.
   (This catches both long and short payloads from the Pine template.)
3. **Options**: set "Once Per Bar Close".
4. **Notifications**:
   - Tick **Webhook URL**
   - Paste `https://<cloudflared-url>/tv-signal`
   - (Leave "Message" empty — the Pine `alert()` call already carries
     the full JSON payload.)
5. Click **Create**.

## 6. Test the round trip

Wait for the next real signal OR trigger a manual test with curl:

```bash
# Replace the secret below with the real $TV_WEBHOOK_SECRET
curl -X POST http://127.0.0.1:5555/tv-signal \
  -H 'Content-Type: application/json' \
  -d '{
    "secret": "PASTE_YOUR_SECRET_HERE",
    "symbol": "USOIL",
    "side": "long",
    "entry": 87.32,
    "sl": 86.90,
    "tp": 88.49,
    "setup": "willy_long_a",
    "regime": "mixed_norm_vol",
    "tqi": 0.37,
    "timeframe": "1h"
  }'
```

Expected response: `{"id": N, "symbol": "USOIL", ...}` — trade row
written to `trade_journal.sqlite`. Verify:

```bash
python3 -m propfirm_engine.log_tv_trade list-open
```

## 7. Closing trades

When WillyAlgoTrader's TP/SL hits on the TradingView paper account,
close the row manually for now:

```bash
python3 -m propfirm_engine.log_tv_trade close --id N --exit <PRICE> --reason tp
```

(A future iteration can auto-log closes via a second `alert()` in Pine
that fires on exit. For now the 1-hop manual close is fine and less
error-prone than auto-closing rows that may not have actually filled
on the TV paper account.)

## 8. Nightly MFE follow-up

Wire this into `launchd` / `cron` / just run manually each evening:

```bash
python3 -m propfirm_engine.mfe_tracker
```

Populates `mfe_10 / mfe_50 / mfe_100` and `mae_10 / mae_50 / mae_100`
columns for any closed trade with ≥ 10 post-exit bars available. Prints
aggregate MFE leakage over last 30 trades against the 0.5R target.

## Troubleshooting

**Alert fires on TV but nothing arrives at the server** —
Check `cloudflared` logs for POST requests. If you see 401 in the
tv_webhook terminal, the secret in Pine doesn't match `$TV_WEBHOOK_SECRET`.

**"bad or missing secret" 401 on every call** —
Pine `{"secret":"..."}` string escaping: make sure the template did not
wrap the secret in extra quotes. The JSON delivered to the webhook
should look like `{"secret":"abc123...","symbol":...}` — copy the raw
message from TradingView's alert log to verify.

**Wrong symbol routing in mfe_tracker** —
TV tickers like `USOIL`, `XAUUSD` get mapped to Yahoo/Binance tickers
(`CL=F`, `GC=F`, ...) inside `mfe_tracker.py::_TV_TO_SOURCE_SYMBOL`.
Add new pairs there as you onboard new symbols.

**Cloudflared URL rotates each run** —
Use a named tunnel (one-time DNS setup) for stability; see
`cloudflared tunnel create` docs.

**I want to cancel a row the webhook created before actually executing
on TV** —
```bash
python3 -m propfirm_engine.log_tv_trade cancel --id N
```
Refuses to delete already-closed rows for audit-trail integrity.
