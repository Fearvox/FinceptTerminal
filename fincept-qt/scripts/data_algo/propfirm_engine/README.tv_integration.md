# TradingView → Propfirm Engine webhook setup

End-to-end recipe for auto-logging WillyAlgoTrader paper trades into
`trade_journal.sqlite`. Zero Pine edits required — both scripts
(SATS Self-Aware Trend System v1.9.0 + Precision Sniper v1.2.2) already
ship with native `alert(... webhookJson ...)` blocks. You only enable a
checkbox in the indicator inputs and configure one TradingView alert.

About 10 minutes end-to-end.

## 0. Prerequisites

- TradingView **Premium** or higher (webhook alerts require Premium+).
- WillyAlgoTrader open-source Pine scripts already loaded on chart.
- `cloudflared` (`brew install cloudflared`) or equivalent tunnel tool
  (ngrok, tailscale funnel). Cloudflared is free; no account required
  for the quick `--url` mode.
- FinceptTerminal repo cloned, at repo root.

## 1. Generate + persist a webhook secret

```bash
export TV_WEBHOOK_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "export TV_WEBHOOK_SECRET=$TV_WEBHOOK_SECRET" >> ~/.zshrc
```

Keep this value handy — it goes into the webhook URL in step 5.
Do not commit.

## 2. Enable the JSON output in each Willy script

Open each script's input dialog in TradingView (cog icon on the
indicator):

**SATS (Self-Aware Trend System)** — under group **📡 Alerts**:
- Tick **Enable Alert Signals** (usually on by default)
- Tick **Webhook JSON Format** ← important

**Precision Sniper** — under group **📡 Alerts**:
- Tick **Webhook JSON Format** ← important

Click OK. That's the only Pine-side change required.

## 3. Launch the local webhook receiver

```bash
cd fincept-qt/scripts/data_algo
python3 -m propfirm_engine.tv_webhook --port 5555
```

You should see:
```
▶ tv_webhook listening on http://127.0.0.1:5555
  POST /tv-signal?secret=<secret>  (entry + exit; detected by `action`/`event`)
  GET  /health
```

Sanity check from another terminal:
```bash
curl http://127.0.0.1:5555/health
# → {"ok": true, "ts": "2026-04-20T..."}
```

## 4. Open a tunnel

TradingView's alert servers can't reach `localhost`:

```bash
cloudflared tunnel --url http://localhost:5555
```

Note the printed URL (like `https://happy-badger-123.trycloudflare.com`).
This rotates per run unless you set up a named tunnel (see cloudflared
docs for stability).

## 5. Create the TradingView alert

On the chart with Willy loaded:

1. Click the **Alert (⏰)** icon.
2. **Condition**: select *WillyAlgoTrader (SATS)* or *Precision Sniper*,
   then **Any alert() function call** in the second dropdown. This
   catches BOTH entries (`action=buy|sell`) AND exits (`event=tp*_hit`
   / `sl_hit`) emitted by the script.
3. **Options**: "Once Per Bar Close".
4. **Notifications**:
   - Tick **Webhook URL**
   - Paste:
     ```
     https://<your-cloudflared-url>/tv-signal?secret=<your-secret>
     ```
     Replace both placeholders. The `?secret=...` query param is how
     the server authenticates — TV webhooks cannot send custom headers,
     and the Willy alert payload cannot include the secret without
     Pine edits, so URL-query is the minimum-friction path.
   - Leave "Message" at the default (it is already the JSON payload
     composed by `alert()` in Pine).
5. Click **Create**. Repeat for each symbol/chart you want covered.

## 6. Test the round trip

**Option a — wait for a real signal** (cleanest).

**Option b — fire a fake entry** with curl:

```bash
curl -X POST "http://127.0.0.1:5555/tv-signal?secret=$TV_WEBHOOK_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"action":"buy","ticker":"USOIL","tf":"1h",
       "price":87.32,"sl":86.90,"tp1":87.82,"tp2":88.32,"tp3":88.82,
       "score":85,"tqi":0.50,"tp_mode":"fixed","tp_scale":1.00}'
```

Expected response: `{"kind":"entry_opened","id":N,...}`.
Verify journal row: `python3 -m propfirm_engine.log_tv_trade list-open`.

To simulate the SL exit:
```bash
curl -X POST "http://127.0.0.1:5555/tv-signal?secret=$TV_WEBHOOK_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"event":"sl_hit","ticker":"USOIL","price":86.90}'
# → {"kind":"exit_closed","id":N,"exit_reason":"sl",...}
```

## 7. What the server does with each event

| Alert type | Payload key | Server action |
|------------|-------------|---------------|
| Entry long | `action: "buy"` | opens `side=long` row, `tp = tp1` (the nearest realistic paper fill target); stores `score/tqi/grade/preset/tp_mode/tp_scale` into `setup_reason` column for grep |
| Entry short | `action: "sell"` | same, `side=short` |
| TP1/TP2/TP3 milestone | `event: "tpN_hit"` | logs milestone in memory; **no** journal update (Willy keeps the position open and trails SL) |
| Trailing SL or raw SL | `event: "sl_hit"` | closes the latest open row for that ticker. If any TP milestone was seen before this sl, `exit_reason="atr_trail"`; otherwise `exit_reason="sl"`. |
| Unmatched exit | any `event` with no matching open row | returns 200 "ignored" so TV doesn't retry |

Auto-close of the "true" exit is only done on `sl_hit` (which is what
actually fires when Willy's trail or original stop triggers). Full-TP
exits go via sl_hit too, because Willy's trail parks at TP2 after
tp3_hit and then gets taken out on the next adverse bar — that final
take-out is the `sl_hit` event the server catches.

## 8. Nightly MFE follow-up

Run manually or wire into cron/launchd:

```bash
python3 -m propfirm_engine.mfe_tracker
```

Populates `mfe_10 / mfe_50 / mfe_100` and `mae_10 / mae_50 / mae_100`
columns using Yahoo/Binance bars after `exit_ts`. Prints aggregate
MFE leakage over last 30 trades vs the 0.5R target.

## 9. Fusion Chat and Fincept native alert panels

If you want the propfirm lane inside Windburn Fusion Chat or FinceptTerminal's
Algo Trading screen, run the combined local stack instead of the bare webhook
receiver:

```bash
cd /Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/data_algo
python3 -m propfirm_engine.fusion_stack
```

This starts two local surfaces:

| Surface | Default URL | Purpose |
|---------|-------------|---------|
| TradingView webhook | `http://127.0.0.1:5555/tv-signal?secret=...` | Receives alerts, writes journal rows, appends a sanitized JSONL feed |
| Local alert panel | `http://127.0.0.1:5556/fusion-panel` | Read-only page that polls `/alerts` and displays accepted/rejected alert facts |
| Fincept native tab | Algo Trading → `PROPFIRM` | Qt-native Obsidian panel that polls the same local `/alerts` endpoint |

Only tunnel the webhook port to TradingView. Keep the panel on `127.0.0.1` and
let Fusion Chat iframe it or FinceptTerminal poll it locally. The alert feed is
display-only: it does not place trades, suggest entries, or load secrets.

Override the Fincept native panel endpoint if needed:

```bash
FINCEPT_PROPFIRM_PANEL_URL=http://127.0.0.1:5556
```

The standalone panel server is also available:

```bash
python3 -m propfirm_engine.fusion_panel_server --port 5556
```

## 10. The Leap VM MoE / ATA room

For the May 2026 TradingView The Leap Crypto contest, run the read-only
accountability room after trades are logged:

```bash
cd /Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/data_algo
python3 -m propfirm_engine.leap_moe_room --date today --quiet
```

It reads `trade_journal.sqlite` in SQLite read-only mode, checks contest
activity days / official crypto symbols / journal discipline, and produces a
local ATA-style role transcript. It does not place trades or generate entries.

Full notes: `propfirm_engine/README.leap_moe_room.md`.

## 11. Manual corrections

If a webhook row was opened but you chose not to execute on TV:
```bash
python3 -m propfirm_engine.log_tv_trade cancel --id N
```

If an auto-close got the wrong reason (e.g. Willy sl_hit fired but you
closed the TV paper position earlier at a different price):
```bash
# close command will refuse if already closed; first see the id:
python3 -m propfirm_engine.log_tv_trade list-open
# to fix, open the sqlite directly; we intentionally don't ship an
# update command to protect the audit trail in the common case.
```

## Troubleshooting

**Alert fires on TV but nothing arrives at the server** —
Check cloudflared terminal for inbound POST. If you see 401 in the
tv_webhook log, the `?secret=...` on the TV alert URL doesn't match
`$TV_WEBHOOK_SECRET`. Common cause: you restarted the shell without
sourcing ~/.zshrc before launching tv_webhook.

**400 "missing required fields"** —
Inspect the exact payload TV sent (cloudflared logs the body). If the
`Webhook JSON Format` input wasn't ticked, TV sends a plaintext
pipe-delimited message instead of JSON — tick the checkbox and update
the alert (TV caches alert settings; delete + recreate the alert if
needed).

**Exit events ignored with "no open trade"** —
Happens if the entry webhook failed for any reason (TV didn't fire,
server was down, secret wrong). The exit tells you there was a signal;
open the TV history tab, pick the entry, then:
```bash
python3 -m propfirm_engine.log_tv_trade open --symbol USOIL --side long \
  --entry 87.32 --sl 86.90 --tp 87.82 --setup "willy_buy_recovered" \
  --entry-ts "2026-04-20T13:00:00Z"
python3 -m propfirm_engine.log_tv_trade close --id <new_id> \
  --exit 86.90 --reason sl
```

**Wrong symbol routing in mfe_tracker** —
`mfe_tracker.py::_TV_TO_SOURCE_SYMBOL` maps TV tickers (USOIL, XAUUSD,
etc.) to Yahoo/Binance tickers (CL=F, GC=F). Add new pairs there as
you onboard new symbols.

**Cloudflared URL rotates each run** —
Use a named tunnel (one-time DNS setup) — see
`cloudflared tunnel create` docs. For the first week of paper trading,
rotating URL is fine; just restart cloudflared and update the TV
alert webhook URL when it happens.

## Reference: the JSON schemas Willy emits

**Entry** (both scripts; discriminated by `action`):
```json
// SATS
{"action":"buy","ticker":"USOIL","tf":"1h",
 "price":87.32,"sl":86.9,"tp1":87.82,"tp2":88.32,"tp3":88.82,
 "score":85,"tqi":0.50,"tp_mode":"dynamic","tp_scale":1.25}

// Precision Sniper
{"action":"sell","ticker":"EURUSD","price":1.0850,
 "sl":1.0890,"tp1":1.0810,"tp2":1.0770,"tp3":1.0730,
 "score":7.2,"grade":"A","preset":"Default","tf":"1h"}
```

**Exit events** (both scripts):
```json
{"event":"tp1_hit","ticker":"USOIL","price":87.82}
{"event":"tp2_hit","ticker":"USOIL","price":88.32}
{"event":"tp3_hit","ticker":"USOIL","price":88.82}
{"event":"sl_hit", "ticker":"USOIL","price":87.32}
```

The server supports both SATS and Sniper schemas transparently —
`open_trade()` uses `tp1` as the `tp` column (nearest target); `tp2/
tp3/score/tqi/grade/preset/tp_mode/tp_scale` are surfaced in the HTTP
response and encoded into `setup_reason` for later grep, but are not
stored as discrete columns.
