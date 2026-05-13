# data_algo Stability Baseline (2026-05-12)

Snapshot of green checks at the close of the v4/p2-atr-trail stability
sweep. If any of the three checks below regresses, that is a real signal —
the sweep left them at a clean known-good state, so a delta is meaningful.

Reproduce all four checks locally:

```bash
cd fincept-qt/scripts/data_algo

# Install deps (idempotent if already on host)
python3 -m pip install -r requirements-dev.txt

# Check 1 — every .py at this directory parses and imports
python3 _smoke_imports.py
# expect: 24/24 OK, exit 0

# Check 2 — propfirm_engine/vendor/ copies haven't silently drifted
python3 _check_vendor_sync.py
# expect:
#   ✓ regime_dual_engine.py: byte-identical
#   ✓ strategy_bake_off.py:  byte-identical
#   ✓ regime_v3_volume.py:   drift confined to expected lines [18]
#   exit 0

# Check 3 — propfirm_engine test suite
python3 -m pytest propfirm_engine/tests/ -v --tb=line
# expect: 48 passed, ~0.1s, exit 0

# Check 4 — fincept-qt C++ build env probe (cmake configure, no build)
./_check_build_env.sh
# expect: exit 0 IF Qt 6.8.x is installed, otherwise exit 1 with
# a clear "find_package Qt6 ... not compatible" diagnosis

# Check 5 — ECC tools manifest drift probe
python3 _check_ecc_manifest.py
# expect: exit 1 — 3/17 declared files exist; see "Known-drift" below
```

## Current green state

| Check | Count | Notes |
|---|---|---|
| `_smoke_imports.py` | 24/24 modules import | Includes `regime_dual_engine.py` after iter-4/5 restored it from `_attic/` |
| `_check_vendor_sync.py` | 2 byte-identical + 1 expected-drift | `propfirm_engine/vendor/` mirrors are in sync |
| `pytest propfirm_engine/tests/` | 48 passed, 0 failed | Covers atr_utils, fusion_panel, leap_moe_room, session_filter |
| `_check_build_env.sh` | **BLOCKED** (exit 1) | See "Known-blocked: C++" below — not a regression, a captured state |
| `_check_ecc_manifest.py` | **DRIFT** (exit 1, 3/17 exist) | See "Known-drift: ECC manifest" below |
| `_git_hooks/pre-commit` (installed) | active (`core.hooksPath` set) | Self-test passes; blocks UUID/sk-*/ghp_/etc. on commit. Bypass: `git commit --no-verify` |

## Known-blocked: C++ cmake configure on this branch

Tracked here so future contributors don't re-discover it as a "new" problem.

`fincept-qt/CMakeLists.txt:284` on this branch (`v4/p2-atr-trail`) pins Qt6
with `EXACT` against `FINCEPT_QT_VERSION = 6.8.3`. On macOS with Homebrew
Qt 6.11.0 installed, cmake configure fails:

```
CMake Error at CMakeLists.txt:284 (find_package):
  Could not find a configuration file for package "Qt6" that exactly
  matches requested version "6.8.3".
  ...considered: /opt/homebrew/lib/cmake/Qt6/Qt6Config.cmake, version: 6.11.0
  The version found is not compatible with the version requested.
```

**Upstream already fixed this** (`upstream/main` is 29 commits ahead of this
branch; the v4.0.3 update commits introduced `FINCEPT_QT_PIN_MODE` with three
modes: `EXACT` / `MINOR` / `ANY`, default `MINOR` so 6.8.x patch drift is
allowed). To unblock locally either:

1. Sync upstream into this branch: `git merge upstream/main` (brings in the
   PIN_MODE machinery) — but that's a 29-commit merge and out of scope for a
   single stability iter
2. Install Qt 6.8.3 exact and point CMake at it: `aqt install-qt mac desktop
   6.8.3 clang_64` then re-run `cmake --preset macos-debug` with
   `-DCMAKE_PREFIX_PATH=...`
3. Set `-DFINCEPT_ALLOW_QT_DRIFT=ON` — this branch already honours that
   escape hatch (CMakeLists.txt:280-282)

The build status here is **captured, not blocking new work** — Python
data_algo / propfirm_engine work doesn't require the C++ build to pass.

## Known-drift: ECC tools manifest

`.claude/ecc-tools.json` declares 17 `managedFiles` that the ECC bundle
was configured to maintain on this repo. At time of capture, only 3 of
those 17 exist:

```
existing: .claude/skills/everything-claude-code/SKILL.md
          .agents/skills/everything-claude-code/SKILL.md
          .claude/identity.json
missing:  .codex/{config.toml, AGENTS.md, agents/{explorer,reviewer,docs-researcher}.toml}
          .claude/{homunculus/..., rules/..., research/..., team/..., enterprise/...}
          .claude/commands/{database-migration, feature-development, add-language-rules}.md
          .agents/skills/everything-claude-code/agents/openai.yaml
```

The manifest is declaring **intent** ("ECC packages these would manage if
generated"), not **state**. Not deleted from the manifest because the
declarations are still meaningful — they record what packages we opted
into when ECC was installed (workflow-pack, agentshield-pack,
research-pack, team-config-sync, enterprise-controls).

To converge:
- Run the ECC tools CLI to regenerate the missing files, OR
- Prune the manifest to only the packages whose outputs we actually want,
  OR
- Live with the drift; the probe will keep flagging it on each sweep.

A related lie was fixed alongside this drift capture: `.claude/identity.json`
previously declared `"domains": ["javascript"]` despite the repo having
1521 .py files, a C++/Qt6 product, TypeScript desktop shell, and zero
first-party JavaScript. Now reads
`["python", "cpp", "qt", "typescript", "cmake"]` with a `correctionNote`.

## What this baseline does NOT cover

- **Actual C++ build artifact** — `_check_build_env.sh` runs cmake configure
  only, never `cmake --build`. Once configure passes, add an end-to-end build
  step here.
- **Live network calls** in scanners (`wolf_hour_scanner`, `polymarket_scanner`,
  `weather_arb_scanner` all hit external APIs; iter-4/5 of this sweep is
  scheduled to add live dry-run checks for those)
- **Strategy correctness** under live bars (separate review track)

## Regression playbook

If one of the three checks fails after a change:

1. Run `git log --oneline -10` to see the most recent suspect
2. Bisect with `git bisect` against the failing check command
3. The check tools have machine-checkable exit codes — they're CI-friendly
4. Update this baseline file in the same commit that intentionally changes
   the expected counts; never bump the numbers without a referenced
   intentional change in the same commit
