"""
ECC tools manifest drift probe.

.claude/ecc-tools.json declares a set of `managedFiles` that the
ECC (Everything Claude Code) bundle is supposed to maintain on this
repo. In practice many of those files have never been generated —
the manifest declares intent, not state. Without a check, "this
file should exist" silently rots.

This tool walks `managedFiles`, classifies each as existing or
missing, and exits nonzero when missing files exceed a tolerated
threshold (currently any missing = nonzero).

Run from anywhere; resolves repo root via git.

Usage:
    python3 _check_ecc_manifest.py [--verbose]

Exit codes:
    0 — all declared files exist
    1 — at least one declared file is missing
    2 — manifest itself is missing or malformed
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True
        )
        return Path(out.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fall back: assume we're invoked from data_algo/
        return Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    repo = _repo_root()
    manifest_path = repo / ".claude" / "ecc-tools.json"
    if not manifest_path.exists():
        print(f"✗ manifest missing: {manifest_path}")
        return 2

    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        print(f"✗ manifest malformed: {e}")
        return 2

    declared = manifest.get("managedFiles", [])
    if not declared:
        print("✗ manifest has no 'managedFiles' key — schema drift?")
        return 2

    existing: list[str] = []
    missing: list[str] = []
    for f in declared:
        (existing if (repo / f).exists() else missing).append(f)

    print(f"=== ecc-tools manifest probe ===")
    print(f"  declared: {len(declared)}")
    print(f"  existing: {len(existing)}")
    print(f"  missing:  {len(missing)}")

    if args.verbose or missing:
        if existing:
            print("\n--- existing ---")
            for f in existing:
                print(f"  ✓ {f}")
        if missing:
            print(f"\n--- missing ({len(missing)}) ---")
            for f in missing:
                print(f"  ✗ {f}")

    if missing:
        print(
            "\nDrift detected. Options:\n"
            "  1. Generate the missing files via the ECC tools CLI\n"
            "  2. Prune the manifest to only declare what exists\n"
            "  3. Update this probe's tolerance if some absences are\n"
            "     intentional (e.g. enterprise-controls.md is opt-in)\n"
        )
        return 1

    print("\nAll declared managed files exist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
