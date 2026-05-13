"""
Vendor-sync check for data_algo/propfirm_engine/vendor/.

propfirm_engine keeps its own copies of three modules under vendor/ to stay
self-contained (so the package doesn't depend on importing siblings up the
tree). The risk: silent drift between the production module in data_algo/
and the vendored copy inside propfirm_engine/.

This tool diffs each (production, vendored) pair and classifies the result:

  * exact-match modules — must be byte-identical
  * partial-mirror modules — diff is allowed only in declared "expected" line
    ranges (typically the import block, where vendor and root use different
    paths). Any drift outside the expected window is a failure.

Usage:
    python3 _check_vendor_sync.py [--verbose]

Exit codes:
    0 — all pairs in sync
    1 — drift detected outside the allowed window
    2 — a vendored file is missing
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
VENDOR = ROOT / "propfirm_engine" / "vendor"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _check_exact(name: str) -> tuple[bool, str]:
    prod = ROOT / name
    vend = VENDOR / name
    if not prod.exists():
        return False, f"  ✗ {name}: production file missing at {prod}"
    if not vend.exists():
        return False, f"  ✗ {name}: vendored copy missing at {vend}"
    if _sha(prod) == _sha(vend):
        return True, f"  ✓ {name}: byte-identical"
    return False, f"  ✗ {name}: sha256 mismatch — vendor has drifted"


def _check_partial(name: str, expected_diff_lines: set[int]) -> tuple[bool, str]:
    """expected_diff_lines is a set of 1-indexed line numbers in the PRODUCTION
    file that are allowed to differ from the vendored copy. Any other diff is
    a failure."""
    prod = ROOT / name
    vend = VENDOR / name
    if not prod.exists() or not vend.exists():
        return False, f"  ✗ {name}: one side missing"

    prod_lines = prod.read_text().splitlines(keepends=True)
    vend_lines = vend.read_text().splitlines(keepends=True)

    unexpected: list[int] = []
    diff = difflib.unified_diff(prod_lines, vend_lines, n=0, lineterm="")
    # Parse hunk headers to find which prod-side line numbers diff
    for line in diff:
        if line.startswith("@@"):
            # @@ -<prod_start>,<count> +<vend_start>,<count> @@
            parts = line.split()
            prod_hunk = parts[1]  # -X,Y
            start = int(prod_hunk.lstrip("-").split(",")[0])
            count_str = prod_hunk.split(",")[1] if "," in prod_hunk else "1"
            count = int(count_str)
            for ln in range(start, start + max(count, 1)):
                if ln not in expected_diff_lines:
                    unexpected.append(ln)

    if not unexpected:
        return True, f"  ✓ {name}: drift confined to expected lines {sorted(expected_diff_lines)}"
    return False, f"  ✗ {name}: unexpected drift on prod lines {unexpected}"


# Contracts: which vendor pairs exist and how should they relate
EXACT_MATCH = [
    "regime_dual_engine.py",
    "strategy_bake_off.py",
]
PARTIAL_MATCH = [
    # regime_v3_volume.py: line 18 of production reads
    #   `from regime_dual_engine import adx, ...`
    # vendor copy uses a different import path; diff is expected there only.
    ("regime_v3_volume.py", {18}),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print(f"=== vendor sync check: {VENDOR.relative_to(ROOT.parent)} ===")
    all_ok = True
    for name in EXACT_MATCH:
        ok, msg = _check_exact(name)
        all_ok &= ok
        print(msg)
    for name, expected in PARTIAL_MATCH:
        ok, msg = _check_partial(name, expected)
        all_ok &= ok
        print(msg)

    if all_ok:
        print("\nAll vendor copies in sync.")
        return 0
    print(
        "\nDrift detected. Either re-sync the vendor copy or update the contract "
        "in _check_vendor_sync.py if the divergence is intentional."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
