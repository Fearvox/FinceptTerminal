"""
Auto-execution dispatcher for propfirm-v4 p5b live week.

Sits between the webhook (which has already journalled the entry alert) and
the opencli TV driver (tv_autoexec).  Applies kill-switch, source allowlist,
per-source policies, and safety rails before deciding whether to click.

Interfaces expected from sibling modules (built by parallel subagents):

  safety.py must expose a class with:
    def can_dispatch(payload: dict) -> tuple[bool, str]: ...
    def compute_qty(payload: dict) -> int: ...   # always >= 1

  policies.py must expose a class with:
    def allows(payload: dict) -> tuple[bool, str]: ...
"""

from __future__ import annotations

import importlib
import json
import logging
import pathlib
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("propfirm.auto_exec.dispatcher")


class AutoExecDispatcher:
    """Decide whether to auto-execute an entry alert and, if so, call tv_autoexec.

    Parameters
    ----------
    dry_run:
        When True the full decision pipeline runs but no click is fired.
        The JSONL log records ``dry_run: true`` so you can verify logic before
        going live.
    source_allowlist:
        Only payloads whose ``source`` field is in this list proceed past the
        allowlist gate.  Example: ``["fincept"]``.
    safety:
        Instance of ``SafetyRails`` from ``auto_exec/safety.py``.  Must expose
        ``can_dispatch(payload) -> (bool, str)`` and
        ``compute_qty(payload) -> int``.
    policies:
        Instance of ``SourcePolicies`` from ``auto_exec/policies.py``.  Must
        expose ``allows(payload) -> (bool, str)``.
    log_dir:
        Directory for the append-only ``dispatches.jsonl`` log file.  Created
        if it does not exist.
    kill_switch_file:
        Path to a sentinel file.  If the file *exists* at call time, all
        dispatches are blocked immediately regardless of other checks.
    """

    def __init__(
        self,
        *,
        dry_run: bool = True,
        source_allowlist: list[str],
        safety: Any,
        policies: Any,
        log_dir: pathlib.Path,
        kill_switch_file: pathlib.Path,
    ) -> None:
        self._dry_run = dry_run
        self._source_allowlist = list(source_allowlist)
        self._safety = safety
        self._policies = policies
        self._log_dir = log_dir
        self._kill_switch_file = kill_switch_file
        self._lock = threading.Lock()

        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_path = self._log_dir / "dispatches.jsonl"

        logger.info(
            "AutoExecDispatcher ready | dry_run=%s | allowlist=%s | log=%s | kill_switch=%s",
            self._dry_run,
            self._source_allowlist,
            self._jsonl_path,
            self._kill_switch_file,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_entry_alert(self, payload: dict) -> dict:
        """Process an entry alert after the journal row has been written.

        Runs the full decision pipeline in order:
          1. Kill switch check
          2. Source allowlist check
          3. Per-source policy check (``policies.allows``)
          4. Safety rails check (``safety.can_dispatch``)
          5. Qty computation (``safety.compute_qty``)
          6. Dry-run branch (log + return, no click)
          7. Live branch (call ``tv_autoexec.execute_signal``)

        Always returns a dict and always appends one line to dispatches.jsonl.
        Never raises.

        Returns
        -------
        dict
            At minimum: ``{"dispatched": bool, "dry_run": bool, "reason": str,
            "qty": int | None, "raw": dict | None}``
        """
        try:
            decision = self._run_pipeline(payload)
        except Exception as exc:
            reason = f"exception: {type(exc).__name__}: {exc}"
            logger.exception("Unhandled exception in dispatch pipeline: %s", exc)
            decision = {
                "dispatched": False,
                "dry_run": self._dry_run,
                "reason": reason,
                "qty": None,
                "raw": None,
            }

        self._write_log(payload, decision)
        return decision

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _run_pipeline(self, payload: dict) -> dict:
        """Execute the ordered decision gates.  Returns a decision dict."""

        # ----------------------------------------------------------
        # Gate 1 — kill switch
        # ----------------------------------------------------------
        if self._kill_switch_file.exists():
            logger.warning("Kill switch active (%s) — no dispatch", self._kill_switch_file)
            return _rejected("kill_switch")

        # ----------------------------------------------------------
        # Gate 2 — source allowlist
        # ----------------------------------------------------------
        source: str = payload.get("source", "")
        if source not in self._source_allowlist:
            reason = f"source_not_allowed:{source}"
            logger.info("Dispatch blocked: %s", reason)
            return _rejected(reason)

        # ----------------------------------------------------------
        # Gate 3 — per-source policies
        # ----------------------------------------------------------
        pol_ok: bool
        pol_why: str
        pol_ok, pol_why = self._policies.allows(payload)
        if not pol_ok:
            # policies.allows already returns a "policy:*" prefixed slug — don't double-wrap.
            logger.info("Dispatch blocked: %s", pol_why)
            return _rejected(pol_why)

        # ----------------------------------------------------------
        # Gate 4 — safety rails
        # ----------------------------------------------------------
        safe_ok: bool
        safe_why: str
        safe_ok, safe_why = self._safety.can_dispatch(payload)
        if not safe_ok:
            reason = f"safety:{safe_why}"
            logger.info("Dispatch blocked: %s", reason)
            return _rejected(reason)

        # ----------------------------------------------------------
        # Gate 5 — compute qty
        # ----------------------------------------------------------
        qty: int = self._safety.compute_qty(payload)

        # ----------------------------------------------------------
        # Gate 6 — dry-run branch
        # ----------------------------------------------------------
        if self._dry_run:
            logger.info(
                "DRY-RUN | would execute action=%s ticker=%s qty=%d",
                payload.get("action"),
                payload.get("ticker"),
                qty,
            )
            return {
                "dispatched": True,
                "dry_run": True,
                "reason": "dry_run",
                "qty": qty,
                "raw": None,
            }

        # ----------------------------------------------------------
        # Gate 7 — live execution
        # ----------------------------------------------------------
        raw = self._execute_live(
            action=payload.get("action", ""),
            ticker=payload.get("ticker", ""),
            qty=qty,
        )
        # Bump rate-limiter only on successful live dispatch — so a TV-side
        # failure doesn't consume rate budget. safety.record_dispatch should
        # never raise; catch defensively just in case.
        try:
            self._safety.record_dispatch(payload)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("safety.record_dispatch failed (non-fatal): %s", exc)
        logger.info(
            "LIVE dispatch | action=%s ticker=%s qty=%d | raw=%s",
            payload.get("action"),
            payload.get("ticker"),
            qty,
            raw,
        )
        return {
            "dispatched": True,
            "dry_run": False,
            "reason": "executed",
            "qty": qty,
            "raw": raw,
        }

    def _execute_live(self, *, action: str, ticker: str, qty: int) -> dict:
        """Lazily import tv_autoexec and call execute_signal.

        Lazy import keeps the dispatcher unit-testable without opencli present.
        """
        # Lazy import — only touched on a real live dispatch.
        tv_autoexec = importlib.import_module(
            "propfirm_engine.tv_autoexec"
        )
        return tv_autoexec.execute_signal(action=action, ticker=ticker, qty=qty)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _write_log(self, payload: dict, decision: dict) -> None:
        """Append one JSON line to dispatches.jsonl.  Thread-safe."""
        record = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "payload": payload,
            "decision": decision,
        }
        line = json.dumps(record, default=str)
        with self._lock:
            with self._jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _rejected(reason: str) -> dict:
    """Build a standard 'not dispatched' decision dict."""
    return {
        "dispatched": False,
        "dry_run": False,
        "reason": reason,
        "qty": None,
        "raw": None,
    }
