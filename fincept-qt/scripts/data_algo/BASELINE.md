# data_algo Stability Baseline (2026-05-12)

Snapshot of green checks at the close of the v4/p2-atr-trail stability
sweep. If any of the three checks below regresses, that is a real signal —
the sweep left them at a clean known-good state, so a delta is meaningful.

Reproduce all three locally:

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
```

## Current green state

| Check | Count | Notes |
|---|---|---|
| `_smoke_imports.py` | 24/24 modules import | Includes `regime_dual_engine.py` after iter-4/5 restored it from `_attic/` |
| `_check_vendor_sync.py` | 2 byte-identical + 1 expected-drift | `propfirm_engine/vendor/` mirrors are in sync |
| `pytest propfirm_engine/tests/` | 48 passed, 0 failed | Covers atr_utils, fusion_panel, leap_moe_room, session_filter |

## What this baseline does NOT cover

- **C++ build** of fincept-qt itself (CMakeLists.txt unchanged this sweep,
  but no `cmake --build` was run end-to-end. Wire that in next sweep.)
- **Live network calls** in scanners (`wolf_hour_scanner`, `polymarket_scanner`,
  `weather_arb_scanner` all hit external APIs; smoke test only confirms they
  parse + import, not that the endpoints still return what's expected)
- **Strategy correctness** under live bars (that's a separate review track,
  not a stability check)

## Regression playbook

If one of the three checks fails after a change:

1. Run `git log --oneline -10` to see the most recent suspect
2. Bisect with `git bisect` against the failing check command
3. The check tools have machine-checkable exit codes — they're CI-friendly
4. Update this baseline file in the same commit that intentionally changes
   the expected counts; never bump the numbers without a referenced
   intentional change in the same commit
