"""
Scanner liveness probe.

Each scanner under data_algo/ talks to a different external API
(Manifold, Polymarket Gamma, Open-Meteo). The Python smoke test
(_smoke_imports.py) only verifies they parse and import — not that
the endpoints they hit are reachable, not that the auth still works,
not that the response shape matches what the scanner expects.

This probe runs each registered scanner in a controlled way:
  * with a short timeout so we don't hang on dead endpoints
  * with stdout/stderr captured into a status report
  * with a known-acceptable outcome per scanner (e.g. for paid /
    region-locked endpoints, "VPN required" or "unreachable" is a
    valid evidence point, not a regression)

Designed to be re-run on every stability sweep. The iter-4/iter-5
deliverable was *registering Wolf Hour, Polymarket, and weather here*;
adding a new scanner in the future = add one entry to SCANNERS.

Usage:
    python3 _smoke_scanners.py                # run all registered
    python3 _smoke_scanners.py wolf_hour      # run just one by name
    python3 _smoke_scanners.py --json         # machine-readable

Exit codes:
    0  every scanner ran to completion with an acceptable outcome
    1  one or more scanners crashed or produced a wholly unexpected
       result (acceptable outcomes include "endpoint unreachable" if
       declared via env_dependent_ok)
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional


ROOT = Path(__file__).parent.resolve()


@dataclasses.dataclass
class ScannerSpec:
    name: str
    cmd: list[str]                 # argv to execute under data_algo/
    timeout_seconds: int           # hard cap; SIGKILL after this
    expect_exit: int = 0           # expected exit code on green
    # If output (combined stdout/stderr) contains any of these substrings,
    # we treat the run as "acceptable degraded" — verify still passes,
    # but the status reflects the degradation. Useful for endpoints that
    # require VPN / are sometimes regionally blocked.
    env_dependent_ok: tuple[str, ...] = ()
    # Optional: callable that runs before the command and decides if the
    # scanner should even be attempted. Return (skip: bool, reason: str).
    precheck: Optional[Callable[[], tuple[bool, str]]] = None
    # Optional: env vars to set for the run (e.g. inject API key from
    # keychain). Caller is responsible for security; we don't log values.
    env_inject: dict[str, str] = dataclasses.field(default_factory=dict)


def _manifold_key_from_keychain() -> str:
    """Best-effort: try macOS keychain. Empty string if unavailable."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "manifold-api", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def _wolf_hour_precheck() -> tuple[bool, str]:
    key = _manifold_key_from_keychain()
    if not key:
        return True, "no MANIFOLD_API_KEY in keychain (run: security add-generic-password -s manifold-api -a $USER -w <key>)"
    return False, ""


SCANNERS: list[ScannerSpec] = [
    ScannerSpec(
        name="wolf_hour",
        cmd=["python3", "wolf_hour_scanner.py", "scan"],
        timeout_seconds=90,
        expect_exit=0,
        env_dependent_ok=(
            "endpoint unreachable",
            "connection refused",
            "Could not reach",
        ),
        precheck=_wolf_hour_precheck,
        env_inject={"MANIFOLD_API_KEY": _manifold_key_from_keychain()},
    ),
    ScannerSpec(
        name="polymarket",
        # --category=fed is one of the smallest, fastest slices (~15 of 5000
        # markets match the "fed" keyword set); good for liveness, not for
        # actual edge discovery.
        cmd=["python3", "polymarket_scanner.py", "scan", "--category", "fed",
             "--limit", "5"],
        timeout_seconds=60,
        expect_exit=0,
        env_dependent_ok=(
            # Polymarket gamma-api is region-blocked in some jurisdictions.
            # If a contributor is on US residential IP without VPN, we
            # accept "blocked" as degraded-OK rather than fail.
            "403 Forbidden",
            "blocked in your region",
            "geo-restricted",
            "ConnectionError",
        ),
    ),
    ScannerSpec(
        name="weather",
        # The default `scan` walks 7 stable-weather cities × ECMWF/GFS
        # ensemble; no auth, just public Open-Meteo. Empty result (no
        # high-confidence candidates) is acceptable on volatile weather
        # weeks.
        cmd=["python3", "weather_arb_scanner.py", "scan"],
        timeout_seconds=120,
        expect_exit=0,
        env_dependent_ok=(
            "Open-Meteo unreachable",
            "rate limit",
        ),
    ),
]


@dataclasses.dataclass
class RunResult:
    name: str
    status: str          # "OK" | "DEGRADED" | "SKIPPED" | "FAIL"
    exit_code: Optional[int]
    duration_s: float
    summary: str         # one-line human summary
    output_tail: str     # last 400 chars of combined output


def run_one(spec: ScannerSpec) -> RunResult:
    if spec.precheck is not None:
        skip, reason = spec.precheck()
        if skip:
            return RunResult(spec.name, "SKIPPED", None, 0.0, reason, "")

    env = os.environ.copy()
    env.update({k: v for k, v in spec.env_inject.items() if v})

    started = time.monotonic()
    try:
        proc = subprocess.run(
            spec.cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=spec.timeout_seconds,
        )
        duration = time.monotonic() - started
        combined = (proc.stdout or "") + (proc.stderr or "")
        tail = combined[-400:].strip()

        if proc.returncode == spec.expect_exit:
            return RunResult(spec.name, "OK", proc.returncode, duration,
                             f"exit {proc.returncode}, {len(combined)} bytes of output", tail)

        degraded_hit = next((s for s in spec.env_dependent_ok if s in combined), None)
        if degraded_hit is not None:
            return RunResult(spec.name, "DEGRADED", proc.returncode, duration,
                             f"endpoint signal: {degraded_hit!r}", tail)

        return RunResult(spec.name, "FAIL", proc.returncode, duration,
                         f"exit {proc.returncode} (expected {spec.expect_exit})", tail)
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - started
        return RunResult(spec.name, "FAIL", None, duration,
                         f"timeout after {spec.timeout_seconds}s", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="Subset of scanners to run by name")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args()

    selected = SCANNERS
    if args.names:
        wanted = set(args.names)
        selected = [s for s in SCANNERS if s.name in wanted]
        unknown = wanted - {s.name for s in SCANNERS}
        if unknown:
            print(f"unknown scanner(s): {sorted(unknown)}", file=sys.stderr)
            return 1

    results = [run_one(s) for s in selected]

    if args.json:
        print(json.dumps([dataclasses.asdict(r) for r in results], indent=2))
    else:
        print(f"=== scanner liveness: {len(results)} run(s) ===")
        for r in results:
            icon = {"OK": "✓", "DEGRADED": "~", "SKIPPED": "-", "FAIL": "✗"}[r.status]
            print(f"  {icon} {r.name:<14s} [{r.status:<8s}] {r.duration_s:5.1f}s  {r.summary}")
            if r.status == "FAIL" and r.output_tail:
                for ln in r.output_tail.splitlines()[-6:]:
                    print(f"      | {ln}")

    if any(r.status == "FAIL" for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
