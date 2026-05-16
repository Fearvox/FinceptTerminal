#!/bin/zsh
# Hourly phantom_cleanup for propfirm-v4 trade_journal.
#
# Closes paper-shadow journal rows (smc, willy) older than 2h that never got
# a matching exit alert. FINCEPT IS EXCLUDED — real fincept dispatches must
# not be auto-closed; manage those manually if they ever phantom out.
#
# Install (one-time):
#     crontab -e
#     # add:
#     7 * * * * /Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/cron/phantom_cleanup_hourly.sh
#
# Log: <repo>/fincept-qt/scripts/data_algo/propfirm_engine/auto_exec/.logs/phantom_cleanup.log
#
# Note: Python path is pinned to /Library/Frameworks/Python.framework/Versions/3.13
# because that's where the playwright / propfirm_engine deps are installed on this
# machine. Update PY= if the env changes.

set -u
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P)
WORK="$SCRIPT_DIR/../data_algo"
LOG="$WORK/propfirm_engine/auto_exec/.logs/phantom_cleanup.log"
PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3

mkdir -p "$(dirname "$LOG")"
cd "$WORK"

{
  echo
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) phantom_cleanup hourly ==="
  for src in smc willy; do
    echo "--- source=$src"
    "$PY" -m propfirm_engine.phantom_cleanup --threshold-hours 2 --source "$src" --apply --no-backup \
        || echo "  (errored; continuing)"
  done
} >> "$LOG" 2>&1
