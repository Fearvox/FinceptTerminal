#!/usr/bin/env bash
# Capture fincept-qt C++ build environment + cmake-configure dry-run status.
#
# Usage:
#   ./_check_build_env.sh [--preset NAME]
#
# Output: human-readable report on stdout.
# Exit codes:
#   0 — configure succeeded
#   1 — configure failed (toolchain mismatch, missing dep, etc.)
#   2 — environment missing required tools (cmake / Qt qmake)
#
# This is a non-destructive probe: it never builds, only configures into a
# scratch dir under /tmp. It's safe to run on a dirty workspace.

set -u

PRESET="${1:-macos-debug}"
if [ "${1:-}" = "--preset" ] && [ -n "${2:-}" ]; then
  PRESET="$2"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
QT_PROJECT="$REPO_ROOT/fincept-qt"

echo "=== fincept-qt C++ build env probe ==="
echo "  preset: $PRESET"
echo "  qt project root: $QT_PROJECT"
echo ""

# --- Tool inventory ---
echo "--- tool inventory ---"
if ! command -v cmake >/dev/null 2>&1; then
  echo "  ✗ cmake: NOT INSTALLED"
  exit 2
fi
CMAKE_VER=$(cmake --version | head -1 | awk '{print $3}')
echo "  cmake:        $CMAKE_VER  ($(command -v cmake))"

if command -v qmake >/dev/null 2>&1; then
  QT_VER=$(qmake -query QT_VERSION 2>/dev/null || echo "unknown")
  QT_PREFIX=$(qmake -query QT_INSTALL_PREFIX 2>/dev/null || echo "unknown")
  echo "  qmake:        $QT_VER at $QT_PREFIX"
else
  echo "  qmake:        NOT FOUND IN PATH"
fi

if command -v c++ >/dev/null 2>&1; then
  CXX_VER=$(c++ --version 2>&1 | head -1)
  echo "  c++:          $CXX_VER"
fi

if command -v ccache >/dev/null 2>&1; then
  echo "  ccache:       $(ccache --version | head -1 | awk '{print $3}')"
fi

# --- Pinned Qt version (read from CMakeLists.txt) ---
echo ""
echo "--- expected Qt pin ---"
PINNED=$(grep -E "^set\(FINCEPT_QT_VERSION " "$QT_PROJECT/CMakeLists.txt" 2>/dev/null | head -1 \
  | sed -E 's/.*FINCEPT_QT_VERSION[[:space:]]+([0-9.]+).*/\1/')
PIN_MODE_DECLARED=$(grep -E "FINCEPT_QT_PIN_MODE.*CACHE" "$QT_PROJECT/CMakeLists.txt" 2>/dev/null \
  | head -1 | grep -oE '"[A-Z]+"' | head -1 | tr -d '"')
echo "  FINCEPT_QT_VERSION: ${PINNED:-not found}"
echo "  FINCEPT_QT_PIN_MODE default: ${PIN_MODE_DECLARED:-EXACT (legacy — pin mode not yet introduced on this branch)}"

# --- cmake configure dry-run ---
echo ""
echo "--- cmake configure ($PRESET) ---"
SCRATCH_BUILD="${TMPDIR:-/tmp}/fincept-qt-buildprobe-$$"
mkdir -p "$SCRATCH_BUILD"
cd "$QT_PROJECT" || exit 2

CONFIGURE_LOG="$SCRATCH_BUILD/configure.log"
# Override binaryDir to keep the probe out of the repo build/ dir
if cmake --preset "$PRESET" -B "$SCRATCH_BUILD" >"$CONFIGURE_LOG" 2>&1; then
  echo "  ✓ configure succeeded"
  STATUS=0
else
  echo "  ✗ configure FAILED — last 12 lines:"
  tail -12 "$CONFIGURE_LOG" | sed 's/^/    /'
  STATUS=1
fi

# --- cleanup scratch ---
rm -rf "$SCRATCH_BUILD"

echo ""
if [ "$STATUS" -eq 0 ]; then
  echo "Build env OK. To do a real build:"
  echo "  cd fincept-qt && cmake --preset $PRESET && cmake --build --preset $PRESET"
else
  echo "Build env BLOCKED. See diagnosis above."
fi
exit "$STATUS"
