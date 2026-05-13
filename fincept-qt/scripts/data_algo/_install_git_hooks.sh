#!/usr/bin/env bash
# Install the vendored git hooks into this clone.
#
# Sets `core.hooksPath` so git looks at the in-repo hook directory
# instead of .git/hooks. This means contributors get the hooks
# automatically after `_install_git_hooks.sh` is run once per clone —
# nothing to install, no Python `pre-commit` framework, no Husky.
#
# Idempotent: safe to re-run.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK_DIR="$SCRIPT_DIR/_git_hooks"

cd "$REPO_ROOT" || exit 2

# --- Sanity ---
if [ ! -d "$HOOK_DIR" ]; then
  echo "✗ hook dir missing: $HOOK_DIR"
  exit 2
fi
if [ ! -x "$HOOK_DIR/pre-commit" ]; then
  echo "  chmod +x $HOOK_DIR/pre-commit"
  chmod +x "$HOOK_DIR/pre-commit"
fi

# --- Set core.hooksPath relative to repo root ---
REL_HOOK_DIR="$(python3 -c "import os; print(os.path.relpath('$HOOK_DIR', '$REPO_ROOT'))")"
git config core.hooksPath "$REL_HOOK_DIR"

current="$(git config --get core.hooksPath)"
echo "✓ core.hooksPath = $current"

# --- Self-test ---
echo ""
echo "--- self-test: scan a synthetic secret file ---"
TMP="${TMPDIR:-/tmp}/_git_hook_selftest_$$"
cat >"$TMP" <<EOF
# This file should trigger the secret scanner
EVERMEM_API_KEY=9db9eb89-aeea-4fa2-9da8-f70590394614
OTHER_API_KEY="sk-proj-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
EOF

if "$HOOK_DIR/pre-commit" "$TMP" > /tmp/_hook_selftest.out 2>&1; then
  echo "✗ self-test FAILED: hook did NOT block a known-bad input"
  echo "--- hook output ---"
  cat /tmp/_hook_selftest.out
  rm -f "$TMP" /tmp/_hook_selftest.out
  exit 1
else
  hit_count=$(grep -c "✗" /tmp/_hook_selftest.out || echo 0)
  echo "✓ self-test PASSED: hook blocked the input ($hit_count pattern hits)"
fi

rm -f "$TMP" /tmp/_hook_selftest.out

echo ""
echo "Pre-commit hook installed. To remove:"
echo "  git config --unset core.hooksPath"
