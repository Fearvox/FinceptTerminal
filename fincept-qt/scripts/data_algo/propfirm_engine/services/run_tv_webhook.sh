#!/usr/bin/env bash
# Wrapper launched by LaunchAgent. Sources zshrc for TV_WEBHOOK_SECRET,
# sets P5B env, then exec's the webhook.
set -euo pipefail
# shellcheck disable=SC1090
source "$HOME/.zshrc" 2>/dev/null || true
export P5B_AUTOEXEC_ENABLED=1
export P5B_MIN_SCORE=30
export P5B_MIN_TQI=0.4
cd /Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/data_algo
exec /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m propfirm_engine.tv_webhook --port 5555
