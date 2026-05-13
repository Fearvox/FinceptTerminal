"""
Vendor snapshot of data_algo indicator + regime modules.

Frozen at propfirm v4 P1 start (2026-04-20) from:
  fincept-qt/scripts/data_algo/{strategy_bake_off,regime_dual_engine,regime_v3_volume}.py

Rationale: those three files are untracked in the parent repo (alongside
local DB artifacts that should stay untracked), but the propfirm engine
needs stable cross-branch imports of their indicator / regime functions.
Vendoring here gives the propfirm_engine package a self-contained import
graph — do not edit the vendor files; if upstream changes, re-snapshot.

The three originals use flat imports (from strategy_bake_off import ...)
so we insert this directory into sys.path on package load below.
"""
import os
import sys

# Make the three vendored modules importable as top-level names so their
# existing `from strategy_bake_off import ...` / `from regime_dual_engine
# import ...` statements still work inside the package.
_VENDOR_DIR = os.path.dirname(__file__)
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)
