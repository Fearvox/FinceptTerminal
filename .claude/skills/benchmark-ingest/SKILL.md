---
name: benchmark-ingest
description: Post-benchmark workflow — ingest results, update dashboard, sync research, deploy. Trigger when a benchmark run completes and results are available.
---

# Benchmark Ingest

Automates the full post-benchmark pipeline: data extraction, registry update, dashboard sync, research integration, i18n sync, commit, and deploy.

## When to Use

Trigger this skill when:
- User shares benchmark final screenshots or results
- User says "benchmark done", "收尾", "ingest results", "录入"
- A benchmark run (R00X) has completed and needs recording

## Prerequisites

- `benchmarks/evensong/registry.jsonl` exists with prior runs
- `benchmarks/evensong/compare.ts` exists
- `benchmarks/index.html` is the live dashboard
- `benchmarks/research.html` is the research page
- `benchmarks/zh/` contains Chinese i18n versions
- Vercel project configured at `benchmarks/.vercel/project.json`

## Workflow

### Phase 1: Data Extraction

Extract these fields from user-provided screenshots or text:

```
run:         R00X (next sequential number)
codename:    string (e.g., "evensong-iii")
date:        YYYY-MM-DD
model:       string (e.g., "Opus-4.6")
mode:        string (e.g., "Self-Evolution-III")
services:    number
tests:       number
failures:    number
assertions:  number
time_min:    number (wall clock in minutes)
criteria:    "N/N" (e.g., "28/28")
docs:        { adr: number, runbooks: number, soc2: number }
grade:       string (S+/A/B/C) or null
notes:       string (one-line summary)
```

Also extract:
- Per-service test counts
- New emergent behaviors observed
- Root cause analysis (if wall clock exceeded target)
- New test dimensions introduced (fuzz, integration, etc.)

### Phase 2: Registry Update

1. Read current `benchmarks/evensong/registry.jsonl`
2. Append new run as single-line JSON
3. Run compare: `bun benchmarks/evensong/compare.ts RPREV RNEW`
4. Show compare output to user

### Phase 3: Dashboard Update (index.html)

Update these sections in `benchmarks/index.html`:

**Hero section:**
- Update run count in title ("Eight runs" -> "Nine runs")
- Update hero-meta tags (date range, orchestration modes, emergent behaviors count)

**Stats section:**
- `data-count` for Benchmark Runs
- `data-count` for Tests Passed (cumulative total across ALL runs)
- Evolution Behaviors count

**Comparison table:**
- Add new `<tr>` row after the last run
- Use `class="highlight-row"` if grade is S+ or A
- Include model badge, mode, services, tests, pass rate, time

**Evolution timeline:**
- Remove `style="background:transparent;"` from previous last entry's connector
- Add new timeline entries for each emergent behavior observed
- Last entry gets transparent connector background

**Insights section:**
- Update Scale card if services/criteria changed
- Update Evolution card with new behavior count
- Update Speed card if new speed records

**Footer:**
- Update date range

**Subtitle:**
- Update "All N runs" count

### Phase 4: Research Integration (research.html)

If new findings were observed:

1. Add new entries to Section 5 (Emergent Behaviors) timeline
2. Update Section 1 (The Evidence) data table with new run
3. If new academic connections found, add to Section 2 cards
4. Update the callout stat if new records set

### Phase 5: i18n Sync

For each change made to English pages, apply the equivalent translation to:
- `benchmarks/zh/index.html`
- `benchmarks/zh/research.html`

Translation rules:
- Product names stay English: CCB, DASH SHATTER, Evensong
- Model names stay English: Opus 4.6, MiniMax M2.7
- Mode names stay English: Self-Evolution, PUA, CHECKPOINT
- Technical terms stay English: property-based, fuzz testing
- All data, numbers, code blocks unchanged

### Phase 6: DASH SHATTER Website Sync

The main product website at `dash-shatter.vercel.app` (repo: `~/dash-shatter/`) also needs updating.

**CRITICAL: Hero.tsx has hardcoded values that MUST be updated manually.**

1. **benchmarkData.ts** — Add new run to `BENCHMARK_RUNS` array in `/Users/0xvox/dash-shatter/src/lib/benchmarkData.ts`
   - `TOTAL_TESTS` and `TOTAL_RUNS` are auto-computed from array — no manual update needed
   - R005 is excluded from array by convention (invalid run)
2. **Hero.tsx** — Update hardcoded `target: N` for behaviors count in `/Users/0xvox/dash-shatter/src/components/sections/Hero.tsx` line ~54
   - Search for `heroStatBehaviors` — the number before it is hardcoded
   - This is NOT auto-computed. You MUST update it.
3. **EvolutionTimeline.tsx** — Add new emergent behaviors to EVENTS array in `/Users/0xvox/dash-shatter/src/components/sections/EvolutionTimeline.tsx`
4. **i18n translations** — Update both EN and ZH translations in `/Users/0xvox/dash-shatter/src/lib/i18n.ts`
   - Add translation keys for new emergent behaviors (evo* pattern)
5. **Build verify** — `cd ~/dash-shatter && bun run build` (MUST pass before commit)
6. **Commit + push** — Separate commit in the dash-shatter repo

```bash
cd ~/dash-shatter
git add -A
git commit -m "benchmark(R00X): sync R00X data + i18n updates"
git push origin main
```

Architecture notes:
- Next.js 16 app with App Router
- i18n via React Context (`LocaleContext.tsx`) + translations in `i18n.ts`
- `LocaleToggle.tsx` in SiteHeader toggles locale context
- Section components read `useLocale()` and index into translations
- Fonts: Plus Jakarta Sans + Geist Mono (no Noto Sans SC needed, system CJK fallback)
- **Hero stats: TOTAL_RUNS and TOTAL_TESTS are auto-computed, but behaviors count is HARDCODED**

### Phase 7: CCB Benchmark Commit + Deploy

```bash
# Stage only benchmark files in the CCB repo
cd ~/claude-code-reimagine-for-learning
git add benchmarks/index.html benchmarks/research.html \
       benchmarks/zh/index.html benchmarks/zh/research.html \
       benchmarks/evensong/registry.jsonl

# Commit with conventional format
git commit -m "benchmark(R00X): [codename] — N tests, 0 failures, +delta vs previous"

# Push
git push origin main

# Verify Vercel deployment
cd benchmarks && npx vercel --prod
```

### Phase 8: Memory Update

Check if existing memory files need updating:
- `memory/benchmark_evolution_r00X_results.md` — create new or update
- `memory/project_evensong_benchmark.md` — update with latest run reference

### Phase 8: Post-Deploy Verification

After ALL commits and pushes, verify BOTH live sites show correct data:

1. **CCB Benchmarks site** — `benchmarks-zeta.vercel.app` (force deploy if needed: `cd benchmarks && npx vercel --prod`)
2. **DASH SHATTER site** — `dash-shatter.vercel.app` (auto-deploys from git push)

Check these specific values on EACH site:
- Run count matches registry entry count
- Total tests matches sum of all runs in registry
- Emergent behaviors count matches latest total
- Latest run appears in comparison table
- R011+ data visible (not cached old version)

If values don't match: check Vercel deployment status, force redeploy, or wait 60s for CDN propagation.

### Phase 8.5: Presentation Review (Steve Jobs Lens)

**REQUIRED** — invoke `benchmark-presentation-review` skill after registry update.

This evaluates whether the dashboard layout still serves the data at current scale.
Thresholds: ≤11 runs = likely KEEP, 12-24 = REVIEW for era grouping, 25+ = RESTRUCTURE.

Output: KEEP / RESTRUCTURE / REDESIGN decision with specific recommendations.

### Phase 9: Ad Copy (if notable results)

If the run achieved S+ grade or set new records:
- Update `docs/superpowers/ad-copy/your-agent-feels-pressure.md` numbers table
- Draft new tagline if appropriate

## Parallelization Strategy

Use subagents for independent work:
- Agent 1: Update index.html (English dashboard)
- Agent 2: Update research.html (if new findings)
- Agent 3: Update zh/index.html (Chinese dashboard)
- Agent 4: Update zh/research.html (Chinese research)
- Agent 5: Update dash-shatter repo (benchmarkData.ts + i18n + timeline)

Agents 3-4 depend on 1-2 completing first (need to know what changed).

## Quality Checklist

Before committing, verify:
- [ ] Registry JSONL is valid (each line parses as JSON)
- [ ] Compare tool runs without errors
- [ ] All 4 HTML files have matching language toggles
- [ ] Toggle CSS uses rgba() fallback (not oklch-only)
- [ ] Toggle z-index is 9999
- [ ] New table row data matches registry entry
- [ ] Timeline entries have correct transition-delay sequence
- [ ] Stats counters sum correctly (cumulative total)
- [ ] Footer date range includes new run date
- [ ] **passRate in benchmarkData.ts is criteria format (e.g., '8/8', '24/24', '28/28') — NEVER assertions count**
  - Sanity check: if either number in passRate > 100, it's WRONG (assertions leaked into criteria field)
  - passRate comes from registry.jsonl `criteria` field, NOT from `assertions`
- [ ] **Hero.tsx hardcoded behaviors count matches total emergent behaviors**
- [ ] **TOTAL_RUNS and TOTAL_TESTS include R005 offset (+1 run, +265 tests)**

## R009 Behavior Predictions (for comparison)

When ingesting R009, compare actual behaviors against predictions:

| # | Predicted | Observed? | Notes |
|---|-----------|-----------|-------|
| P1 | Circuit breaker gaming | | |
| P2 | Pressure meta-cognition | | |
| P3 | Two-wave fusion (ignore 5+5, go 10) | | |
| P4 | Proactive file splitting | | |
| P5 | Self-benchmarking against R007/R008 | | |
| P6 | Quality downgrade for speed | | |
| P7 | Cross-agent knowledge sharing | | |
| P8 | R010 autonomous planning | | |

Record hits, misses, and surprises in the research page.
