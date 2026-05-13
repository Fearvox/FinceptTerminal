# Propfirm v4 — Pre-Trade Checklist Dashboard

Single-file HTML dashboard that forces the 7 propfirm discipline checks
before every trade. Uses DASH brand tokens (Deep Green / Neon Gold /
Warm Sand / Geist fonts) from `/Users/0xvox/Desktop/design-infra`.

## Launch

**Option 1 — open locally (simplest)**:
```bash
open /Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/data_algo/propfirm_engine/dashboard/pretrade.html
```
Brave / default browser will render it directly from `file://`. No server needed.

**Option 2 — local HTTP server (for automation / screenshots)**:
```bash
cd /Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/data_algo/propfirm_engine/dashboard
python3 -m http.server 7788
# then visit http://127.0.0.1:7788/pretrade.html
```

## The 7 pillars

| # | Check | Hard? | What fires the fail |
|---|-------|:-----:|---------------------|
| ① | SL set + direction correct | 🔴 hard | SL blank, OR long with SL ≥ entry, OR short with SL ≤ entry |
| ② | TP set + direction correct | 🟡 soft | TP blank or wrong side |
| ③ | R:R ≥ 1 : 1.5 | 🟡 soft | reward/risk < 1.5 |
| ④ | Position risk ≤ 1 % of $100 k equity | 🟡 soft | |entry − sl| × qty > $1000 |
| ⑤ | Willy system alignment | 🟡 soft | score < 6.5 OR grade ∉ {A, A+} OR status = "No Trade" |
| ⑥ | No binary event (earnings / FOMC / NFP / CPI) in 48 h | 🔴 hard | checkbox checked |
| ⑦ | Symbol + qty filled (journal-ready) | 🟡 soft | blank fields |

**Verdict logic**:
- **GO** — all 7 pass. Dashboard emits the `log_tv_trade open` CLI line you paste into terminal.
- **SKIP** — any 🔴 hard fail (no SL, or binary event in window).
- **SKIP** — 3+ soft fails.
- **WAIT** — 1-2 soft fails (observe, or re-enter params).
- **PENDING** — form incomplete.

## Typical workflow

1. Willy TV alert fires → note score / grade / status + SL / TP from Willy panel
2. Open this dashboard in your main Brave → fill the 7 fields → press `↓ 检查这一笔`
3. If **GO** → dashboard prints the exact `log_tv_trade open` command → paste in terminal
4. Then execute on TV Paper Trading with same SL + TP (via Willy's bracket order)
5. Close cycle as before (`log_tv_trade close` when TP/SL triggers)

## Editing thresholds

Top of `<script>` block in `pretrade.html`:

```js
const EQUITY          = 100000;   // OxVox paper balance
const MAX_RISK_PCT    = 1.0;      // 1 % per trade
const MIN_RR          = 1.5;      // pillar 3 threshold
const MIN_WILLY_SCORE = 6.5;      // pillar 5 threshold
const VALID_GRADES    = ['A+','A']; // pillar 5 whitelist
```

Change and save — no rebuild, just reload browser.

## Design system

- Colors: `#10291f` / `#f0ee9b` / `#ddd6c7` / `#7ed29a` / `oklch(0.62 0.20 25)` (danger)
- Typography: Geist 400/500/600/800 + Geist Mono for numerical fields and CLI output
- 8-pixel baseline rhythm per DASH spec
- Single file (no build step, no bundler, no external JS deps)

## Not yet built

- Integration with `trade_journal.sqlite` (to auto-mark open rows as "executed" when the GO CLI is actually run)
- Live sync with Willy values (currently you transcribe from TV panel — auto-pull would need CDP or webhook state)
- Position-size calculator (given SL + risk %, suggest qty — right now you type qty yourself)
- History view (show last 10 decisions and their GO/SKIP/WAIT outcomes)

These are obvious next-iteration improvements once discipline habit is installed.
