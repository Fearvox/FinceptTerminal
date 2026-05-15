#!/usr/bin/env bash
# Install P5b webhook + daemon as macOS LaunchAgents.
# Stops existing processes (port 5555/5556), copies plists, loads them.
#
# Usage:
#   bash install.sh         # install + load
#   bash install.sh status  # check status
#   bash install.sh stop    # unload (stops services)
#   bash install.sh restart # reload both services
set -euo pipefail

SERVICES_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_WEBHOOK="com.fincept.p5b-webhook.plist"
PLIST_DAEMON="com.fincept.p5b-daemon.plist"

action="${1:-install}"

cmd_install() {
    echo "→ killing any existing processes on :5555 and :5556..."
    pkill -f "propfirm_engine.tv_webhook" 2>/dev/null || true
    pkill -f "propfirm_engine.tv_playwright_daemon" 2>/dev/null || true
    sleep 2

    echo "→ ensuring scripts are executable..."
    chmod +x "$SERVICES_DIR/run_tv_webhook.sh"
    chmod +x "$SERVICES_DIR/run_tv_daemon.sh"

    echo "→ copying plists to $LAUNCH_AGENTS..."
    mkdir -p "$LAUNCH_AGENTS"
    cp "$SERVICES_DIR/$PLIST_WEBHOOK" "$LAUNCH_AGENTS/$PLIST_WEBHOOK"
    cp "$SERVICES_DIR/$PLIST_DAEMON" "$LAUNCH_AGENTS/$PLIST_DAEMON"

    echo "→ unloading old agents (if any)..."
    launchctl unload "$LAUNCH_AGENTS/$PLIST_WEBHOOK" 2>/dev/null || true
    launchctl unload "$LAUNCH_AGENTS/$PLIST_DAEMON" 2>/dev/null || true

    echo "→ loading new agents..."
    launchctl load "$LAUNCH_AGENTS/$PLIST_WEBHOOK"
    launchctl load "$LAUNCH_AGENTS/$PLIST_DAEMON"

    sleep 3
    echo ""
    cmd_status
}

cmd_stop() {
    echo "→ unloading both agents..."
    launchctl unload "$LAUNCH_AGENTS/$PLIST_WEBHOOK" 2>/dev/null || true
    launchctl unload "$LAUNCH_AGENTS/$PLIST_DAEMON" 2>/dev/null || true
    echo "→ killing any leftover processes..."
    pkill -f "propfirm_engine.tv_webhook" 2>/dev/null || true
    pkill -f "propfirm_engine.tv_playwright_daemon" 2>/dev/null || true
    echo "Stopped."
}

cmd_restart() {
    cmd_stop
    sleep 2
    cmd_install
}

cmd_status() {
    echo "── LaunchAgent status ──"
    for label in com.fincept.p5b-webhook com.fincept.p5b-daemon; do
        if launchctl list | grep -q "$label"; then
            line=$(launchctl list | grep "$label")
            echo "  $line  ← loaded"
        else
            echo "  $label  ← NOT loaded"
        fi
    done
    echo ""
    echo "── Port check ──"
    for port in 5555 5556; do
        pid=$(lsof -ti :$port 2>/dev/null || true)
        if [[ -n "$pid" ]]; then
            echo "  :$port  PID=$pid"
        else
            echo "  :$port  empty"
        fi
    done
    echo ""
    echo "── Health check ──"
    curl -sS --max-time 2 http://127.0.0.1:5555/health 2>&1 | head -1 || echo "  webhook NOT responding"
    curl -sS --max-time 2 http://127.0.0.1:5556/health 2>&1 | head -1 || echo "  daemon NOT responding"
}

case "$action" in
    install) cmd_install ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    *) echo "usage: install.sh [install|stop|restart|status]"; exit 1 ;;
esac
