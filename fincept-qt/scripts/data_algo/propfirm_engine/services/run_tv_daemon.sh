#!/usr/bin/env bash
# Wrapper launched by LaunchAgent for tv_playwright daemon.
set -euo pipefail
cd /Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/data_algo
exec /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m propfirm_engine.tv_playwright_daemon
