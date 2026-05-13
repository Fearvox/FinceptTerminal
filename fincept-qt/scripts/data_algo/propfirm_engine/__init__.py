"""
Propfirm v4 Engine — modular trading engine built in 5 phases.

Spec : docs/superpowers/specs/2026-04-20-propfirm-v4-engine-design.md
Plan : docs/superpowers/plans/2026-04-20-propfirm-v4-engine-plan.md

Phase activation flags (each phase adds exactly one mechanism):
  P1  session_filter_on   — UTC hour masks + news-window skip
  P2  atr_trail_on        — ATR chandelier trail, no breakeven
  P3  confluence_gate_on  — 3-of-3 entry gate (HTF / level / CVD)
  P4  mfe_log_on          — post-exit counterfactual MFE/MAE tracker
  P5  pn_sizing_on        — fixed 1% risk + skip-after-losses

No public API from this package until Phase 1 ships engine.py.
"""

__version__ = "0.0.0"  # bumped per phase: 0.1.0=P1, 0.2.0=P2, ...
