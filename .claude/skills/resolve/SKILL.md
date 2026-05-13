---
name: resolve
description: Reverse-engineer a prediction-market trader's strategy from their public profile. Feed a Manifold/Polymarket URL or username — get a structured dossier with strategy fingerprint, streak probes, and copy/don't-copy recommendations scaled to Nolan's account size. Triggers on /resolve, "resolve this trader", "逆向这个玩家".
license: MIT
---

# /resolve — Trader reverse-engineering pipeline

Takes a prediction-market trader URL or username, pulls public API data,
fingerprints their strategy across 8 dimensions (direction bias, order
style, size distribution, probability targeting, 8 streak types,
timing, category), and emits a Miko/Randles-style dossier.

## Invocation

```
/resolve <url-or-username>                    # auto-detect platform
/resolve manifold:<username>                  # explicit platform
/resolve polymarket:<wallet-or-handle>        # future: Polymarket
/resolve <url> --save                         # also save to EverMem folder
/resolve <url> --bets=1000                    # deep-dive with more history
```

## Execution

When invoked:

### Step 1: Parse args
Extract:
- target (URL or `platform:handle` or bare handle)
- flags (`--save`, `--bets=N`)

If bare handle provided without platform, default to **manifold**.

### Step 2: Run the python pipeline

```bash
python3 /Users/0xvox/Documents/GitHub/FinceptTerminal/.claude/skills/resolve/resolve.py \
  <target> [--bets=N] [--save]
```

The script hits public APIs, runs the analysis, and prints a markdown
dossier to stdout.

**Timeout**: 60s default (the bet-history call can be slow for deep
pulls). If `--bets ≥ 500`, allow up to 120s.

### Step 3: Display the dossier

Show the markdown output directly to the user. If `--save` was set, the
script writes to
`/Users/0xvox/.claude/projects/-Users-0xvox-Documents-GitHub-FinceptTerminal/memory/<handle>-reverse-engineering.md`
and also appends a line to `MEMORY.md`. Confirm both files written.

### Step 4: Trailing context

Always end the response with:
- **Copy-what / Don't-copy-what** section (scaled to Nolan's current balance/equity)
- **One concrete first action** Nolan can take in ≤ 5 minutes to try the strategy
- **One streak dimension to probe next** (e.g., "next run: check this trader's time-of-day streak with --bets=1000")

## Probe Dimensions (what the script reports)

For every target, report these 8 streak / pattern probes:

1. **Activity streak** — consecutive days with ≥1 bet
2. **Direction bias** — % YES vs % NO across last N bets
3. **Order style** — % LIMIT orders vs market taker
4. **Size distribution** — tiny / small / mid / big bucket histogram
5. **Probability targeting** — % of bets at `p ≤ 5%` / `p ≥ 95%` (tail-skim) vs `p ∈ [30%, 70%]` (middle contestation)
6. **Session timing** — bet timestamp distribution across 24 UTC hours
7. **Category focus** — top-3 market categories
8. **Consensus vs contrarian** — how often they bet against majority direction

If any dimension shows a **run of 20+ consecutive bets** in the same
bucket, flag it as a "streak signature" — that's a highly reproducible
habit worth studying.

## Adapter API

The script has platform adapters at
`/Users/0xvox/Documents/GitHub/FinceptTerminal/.claude/skills/resolve/adapters/`:

- `manifold.py` — uses `api.manifold.markets/v0/`  (fully working)
- `polymarket.py` — uses `data-api.polymarket.com/` (placeholder, fill in when a real Polymarket target comes)
- `kalshi.py` — (placeholder)

Adding a new platform: implement `fetch_profile()` + `fetch_bets()` +
`fetch_top_positions()` with the documented return shape, and register
it in `resolve.py::ADAPTERS`.

## Known traders already resolved

See EverMem indexes:
- `christopher-randles-reverse-engineering.md` — Manifold M$1M + $137k withdrawn, limit-order tail sniper, 73% NO, 1072-day streak
- `miko-reverse-engineering.md` — Polymarket bot wallet 0x6fdc, Pre-Wolf 00:00-02:59 UTC MM

The skill will detect if a target has a prior memo and offer to refresh
or diff against the old snapshot.
