"""
Per-source policy gates for the propfirm-v4 p5b auto-exec pipeline.

Operator workflow:
  - Edit DEFAULT_POLICIES in this file directly (no external config).
  - Phase 4 readiness: flip willy["enabled"] to True, adjust min_score / min_tqi.

CLI:
    python -m propfirm_engine.auto_exec.policies show
    python -m propfirm_engine.auto_exec.policies show --source fincept
    python -m propfirm_engine.auto_exec.policies show --format json
"""
from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import logging
import sys
from typing import Any

logger = logging.getLogger("propfirm.auto_exec.policies")

# ---------------------------------------------------------------------------
# Default policy table — operator edits this dict in source.
# Deep-copied on SourcePolicies.__init__ so live instances never mutate this.
# ---------------------------------------------------------------------------
DEFAULT_POLICIES: dict[str, dict[str, Any]] = {
    "fincept": {
        "enabled": True,
        "min_score": 60,
        "min_tqi": 0.5,
        "allowed_sides": ["buy", "sell"],
        "allowed_symbol_patterns": ["*"],   # glob; "*" = any
        "blocked_symbol_patterns": [],
    },
    "willy": {
        "enabled": False,                   # Phase 4 will flip True
        "min_score": 30,
        "min_tqi": 0.4,
        "allowed_sides": ["buy", "sell"],
        "allowed_symbol_patterns": ["*"],
        "blocked_symbol_patterns": [],
    },
    "smc": {
        "enabled": False,
        "min_score": 0,
        "min_tqi": 0.0,
        "allowed_sides": ["buy", "sell"],
        "allowed_symbol_patterns": ["*"],
        "blocked_symbol_patterns": [],
    },
}


def _matches_any(symbol: str, patterns: list[str]) -> bool:
    """Return True if *symbol* matches at least one glob pattern.

    Uses ``fnmatch.fnmatch`` so ``*`` matches everything.  Symbols may carry
    an exchange prefix like ``BATS:TSLA``; patterns are matched against the
    full string as received.

    Args:
        symbol: The instrument ticker, e.g. ``"SOLUSDC.P"`` or ``"BATS:TSLA"``.
        patterns: A list of glob patterns, e.g. ``["*"]`` or ``["BTC*", "ETH*"]``.

    Returns:
        True if at least one pattern matches, False otherwise.
    """
    for pat in patterns:
        if fnmatch.fnmatch(symbol, pat):
            return True
    return False


class SourcePolicies:
    """Gate per-source business rules before a dispatch is attempted.

    Decision order inside ``allows`` (returns on first failure):
      1. Unknown source.
      2. Source disabled.
      3. Score below threshold.
      4. TQI below threshold.
      5. Action (side) not in allowed_sides.
      6. Symbol matches a blocked_symbol_patterns entry.
      7. Symbol does not match any allowed_symbol_patterns entry.

    Args:
        config: Custom policy dict.  If ``None`` the module-level
            ``DEFAULT_POLICIES`` is deep-copied so callers cannot accidentally
            mutate the module default.

    Example:
        >>> sp = SourcePolicies()
        >>> ok, reason = sp.allows({"source": "fincept", "action": "buy",
        ...                          "ticker": "SOLUSDC.P", "score": 75,
        ...                          "tqi": 0.72})
        >>> ok
        True
    """

    def __init__(self, config: dict[str, dict[str, Any]] | None = None) -> None:
        self._config: dict[str, dict[str, Any]] = (
            copy.deepcopy(DEFAULT_POLICIES) if config is None else copy.deepcopy(config)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allows(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """Evaluate whether *payload* is allowed to proceed.

        Args:
            payload: Alert dict received from TradingView.  Must contain at
                minimum ``"source"``, ``"action"``, ``"ticker"``, ``"score"``,
                and ``"tqi"`` keys (absent keys default safely to 0/empty).

        Returns:
            ``(True, "policy:ok")`` when all gates pass.
            ``(False, "<reason_slug>")`` with a grep-friendly slug on the
            first failing gate.  The dispatcher wraps this in ``"policy:<why>"``.

        Reason slug format (used verbatim by dispatcher):
            ``policy:unknown_source:<src>``
            ``policy:source_disabled:<src>``
            ``policy:score_below_threshold:<actual>_<min>``
            ``policy:tqi_below_threshold:<actual>_<min>``
            ``policy:side_blocked:<action>``
            ``policy:symbol_blocked``
            ``policy:symbol_not_allowed``
            ``policy:ok``
        """
        src: str = str(payload.get("source") or "").lower().strip()

        # 1. Unknown source
        if src not in self._config:
            reason = f"policy:unknown_source:{src}"
            logger.debug("allows=False reason=%s payload_keys=%s", reason, list(payload.keys()))
            return False, reason

        pol = self._config[src]

        # 2. Source disabled
        if not pol.get("enabled", False):
            reason = f"policy:source_disabled:{src}"
            logger.debug("allows=False reason=%s", reason)
            return False, reason

        # 3. Score gate
        actual_score = float(payload.get("score") or 0)
        min_score = float(pol.get("min_score", 0))
        if actual_score < min_score:
            reason = f"policy:score_below_threshold:{actual_score}_{min_score}"
            logger.debug("allows=False reason=%s", reason)
            return False, reason

        # 4. TQI gate
        actual_tqi = float(payload.get("tqi") or 0)
        min_tqi = float(pol.get("min_tqi", 0.0))
        if actual_tqi < min_tqi:
            reason = f"policy:tqi_below_threshold:{actual_tqi}_{min_tqi}"
            logger.debug("allows=False reason=%s", reason)
            return False, reason

        # 5. Side allowlist
        action: str = str(payload.get("action") or "").lower().strip()
        allowed_sides: list[str] = [s.lower() for s in pol.get("allowed_sides", [])]
        if action not in allowed_sides:
            reason = f"policy:side_blocked:{action}"
            logger.debug("allows=False reason=%s", reason)
            return False, reason

        # 6. Blocked symbol patterns (checked before allowed so a block wins)
        symbol: str = str(payload.get("ticker") or "")
        blocked_pats: list[str] = pol.get("blocked_symbol_patterns", [])
        if blocked_pats and _matches_any(symbol, blocked_pats):
            reason = "policy:symbol_blocked"
            logger.debug("allows=False reason=%s symbol=%s", reason, symbol)
            return False, reason

        # 7. Allowed symbol patterns
        allowed_pats: list[str] = pol.get("allowed_symbol_patterns", ["*"])
        if not _matches_any(symbol, allowed_pats):
            reason = "policy:symbol_not_allowed"
            logger.debug("allows=False reason=%s symbol=%s", reason, symbol)
            return False, reason

        logger.debug("allows=True source=%s action=%s ticker=%s score=%s tqi=%s",
                     src, action, symbol, actual_score, actual_tqi)
        return True, "policy:ok"

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a deep copy of the current config for external inspection.

        Returns:
            Deep copy of the full policy dict, safe to mutate.
        """
        return copy.deepcopy(self._config)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_table(config: dict[str, dict[str, Any]], source_filter: str | None) -> str:
    """Format policy config as a simple aligned table.

    Args:
        config: Policy dict (full or single-source).
        source_filter: If set, only include that source row.

    Returns:
        Multi-line string ready for ``print()``.
    """
    sources = (
        [source_filter]
        if source_filter and source_filter in config
        else list(config.keys())
    )

    col_w = {
        "source": 10,
        "enabled": 8,
        "min_score": 10,
        "min_tqi": 8,
        "sides": 14,
        "allow_pats": 18,
        "block_pats": 18,
    }

    header = (
        f"{'source':<{col_w['source']}}"
        f"{'enabled':<{col_w['enabled']}}"
        f"{'min_score':<{col_w['min_score']}}"
        f"{'min_tqi':<{col_w['min_tqi']}}"
        f"{'sides':<{col_w['sides']}}"
        f"{'allow_pats':<{col_w['allow_pats']}}"
        f"{'block_pats':<{col_w['block_pats']}}"
    )
    sep = "-" * len(header)
    rows = [header, sep]

    for src in sources:
        pol = config.get(src, {})
        enabled = str(pol.get("enabled", False))
        min_score = str(pol.get("min_score", 0))
        min_tqi = str(pol.get("min_tqi", 0.0))
        sides = ",".join(pol.get("allowed_sides", []))
        allow_pats = ",".join(pol.get("allowed_symbol_patterns", []))
        block_pats = ",".join(pol.get("blocked_symbol_patterns", [])) or "(none)"
        rows.append(
            f"{src:<{col_w['source']}}"
            f"{enabled:<{col_w['enabled']}}"
            f"{min_score:<{col_w['min_score']}}"
            f"{min_tqi:<{col_w['min_tqi']}}"
            f"{sides:<{col_w['sides']}}"
            f"{allow_pats:<{col_w['allow_pats']}}"
            f"{block_pats:<{col_w['block_pats']}}"
        )

    return "\n".join(rows)


def _cli_show(args: argparse.Namespace) -> int:
    """Handle the ``show`` subcommand.

    Args:
        args: Parsed argparse namespace.

    Returns:
        Exit code (0 = ok).
    """
    config = copy.deepcopy(DEFAULT_POLICIES)

    if args.source and args.source not in config:
        print(f"error: unknown source '{args.source}'. "
              f"Known: {', '.join(config)}", file=sys.stderr)
        return 1

    target: dict[str, dict[str, Any]] = (
        {args.source: config[args.source]} if args.source else config
    )

    if args.format == "json":
        print(json.dumps(target, indent=2))
    else:
        print(_build_table(config, args.source))

    return 0


def main() -> int:
    """CLI entry point for ``python -m propfirm_engine.auto_exec.policies``.

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(
        prog="python -m propfirm_engine.auto_exec.policies",
        description="Inspect per-source auto-exec policies.",
    )
    sub = parser.add_subparsers(dest="cmd")

    show_p = sub.add_parser("show", help="Print policy config.")
    show_p.add_argument(
        "--source",
        metavar="NAME",
        default=None,
        help="Filter to a single source (fincept | willy | smc).",
    )
    show_p.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table).",
    )

    args = parser.parse_args()
    if args.cmd == "show":
        return _cli_show(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
