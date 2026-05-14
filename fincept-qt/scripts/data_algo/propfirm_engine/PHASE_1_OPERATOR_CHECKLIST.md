# P5b Phase 1 — Operator Pre-flight Checklist

**Goal**: Get from "infra ready" to "first live Willy alert routes through webhook + I see notification".

**Owner**: Nolan (operator). I (the assistant) cannot touch the TV UI; everything below is yours.

**Estimated time**: 15-25 min if TV layout matches Apr-20 state, +10 min if alert needs recreation.

---

## Pre-flight (one-time per session)

### 1. Start the webhook + quick tunnel
```bash
cd /Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/data_algo
bash propfirm_engine/start_p5b_session.sh
```

Expected output:
- ✓ webhook started PID=N port=5555
- ✓ tunnel URL: https://<random-words>.trycloudflare.com
- Full URL (with `?secret=...`) gets **pbcopy'd to clipboard**

### 2. Confirm TV paper account state
In TV's Paper Trading panel:
- Balance: should be **$100,000.00** (operator's chosen starting size)
  - If different, click Settings → Reset Paper Account → confirm $100k
- Open positions: should be **0**
  - If there's a stale BTC/GOLD short from 2026-04-21 (journal has row id=8 still open),
    close it manually on TV first, then mark in journal:
    ```bash
    python3 -m propfirm_engine.log_tv_trade list-open
    python3 -m propfirm_engine.log_tv_trade close --id 8 --reason manual_cleanup --exit-price <whatever>
    ```

### 3. Update TV alert webhook URL
1. Open the Willy alert in TV's alert manager (bell icon → Manage)
2. Click the alert → edit
3. Notification tab → Webhook URL field → **cmd-V** (the start script just put the new URL there)
4. Message tab → confirm it's the Willy alert template payload (see `pine_willy_alert_template.pine`)
5. Trigger setting: **Once Per Bar Close** (NOT once per bar — avoid mid-bar fakeouts)
6. Save

### 4. Smoke test alert
1. In TV, manually fire the alert once via "test webhook" button if available
2. Or: wait for next bar close on a chart you're sure will fire (e.g. BTCUSD 15m during active session)
3. Confirm:
   - You see a macOS notification "Willy BUY/SELL ..."
   - Clipboard contains the trade plan (cmd-V into any text editor to verify)
   - Webhook log shows `[notify_signal] ENTRY:` block
   - Journal has a new row: `python3 -m propfirm_engine.log_tv_trade list-open`

If smoke test passes → ready to start **attempt-1** of the trading week.

---

## During the trading week — per-alert workflow

### When you hear a notification:
1. **Look at clipboard** (cmd-V into anywhere): full trade plan with size + R:R + rails
2. **Decide**: enter, skip, or wait? Use the 4 rails:
   - no BE stop (don't move stop to entry mid-trade)
   - no I.q. 4-of-4 imitation (don't enter just because indicators align — Willy is the trigger)
   - no Wolf-Hour momentum (no entries during 23:00-02:00 UTC just because of "volatility window")
   - no 4+ confluence (more isn't better; Willy + session is enough)
3. **If entering**: switch to TV, click Buy/Sell, type SL/TP from the clipboard plan
4. **If skipping**: log a one-liner via `log_tv_trade cancel --id <row_id>` so the row doesn't get
   counted as a phantom open

### When SL/TP hits:
Willy's Pine template fires an exit alert → webhook auto-closes the journal row.
You'll hear an EXIT notification with `+X.XX%` pnl.
Verify TV paper actually closed the position (sometimes paper fills lag).

### End of each trading day:
```bash
python3 -m propfirm_engine.log_tv_trade list-open    # any phantom opens?
python3 -m propfirm_engine.mfe_tracker                # back-fill MFE/MAE
sqlite3 propfirm_engine/trade_journal.sqlite "select id, symbol, side, exit_reason, pnl_pct, mfe_50 from trades where date(entry_ts) = date('now') order by id"
```

### Cumulative -5R check:
If pooled pnl across today's trades < -5% of $100k = -$5000 → **hard stop**.
- Close any open positions
- Reset TV paper to $100k
- Log attempt-N as blowup in `.goal/p5b-live-week/attempt-log.md` with reason
- New attempt starts fresh tomorrow

---

## What I (assistant) will do while you're trading

- **Watch webhook.log**: spot any 400/500s or schema mismatches
- **Daily reconcile**: at end of UTC day, summarize journal stats (N, WR, RR per symbol)
- **Failure-mode notes**: when a trade closes, write what setup looked like + why it
  worked or didn't to `phase_5b_observations.md`
- **Build opencli adapter (background)**: if manual-click becomes the bottleneck and you
  want me to swap in auto-exec, I can build the opencli TV adapter as Packet v3

---

## When something breaks

| Symptom | Likely cause | Fix |
|---|---|---|
| TV alert fires but no notification | quick tunnel URL rotated since last start | re-run `start_p5b_session.sh`, update TV alert URL |
| `?secret=...` 401 | shell didn't load zshrc → wrong secret env | `source ~/.zshrc` then restart webhook |
| `kind:ignored` on close events | webhook never saw the open (e.g. tunnel was down) | manually `log_tv_trade close --id N` |
| pbcopy empty | osascript / pbcopy missing on shell PATH | check `command -v pbcopy` — should be `/usr/bin/pbcopy` |
| Notification not visible but stderr shows it | macOS Do-Not-Disturb / Focus mode on | check Control Center → turn off DND |

---

## Tagging policy (week-end)

- If account_multiple ≥ 2.0 AND post-mortem identifies ≥ 2 actionable findings → **tag v4.p5b**
- If account blows multiple times AND no actionable methodology emerges → no tag, document why
- If methodology delivers but account doesn't 2x (e.g. ended +30%) → judgment call, P10 decision
- **Never silent-tag if the gate-as-written fails** — same discipline as v4.p3/p4/p5a
