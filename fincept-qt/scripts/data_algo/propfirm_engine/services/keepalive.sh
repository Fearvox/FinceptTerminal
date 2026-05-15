#!/usr/bin/env bash
# Persistent background launch (no LaunchAgent — Documents folder requires
# Full Disk Access that we can't auto-grant). Uses nohup + setsid so
# processes survive terminal close.
#
# Usage:
#   bash keepalive.sh start    # launch webhook + daemon in background
#   bash keepalive.sh status   # check
#   bash keepalive.sh stop     # kill both
#   bash keepalive.sh restart
#   bash keepalive.sh logs     # tail both logs
#
# To auto-restart on reboot: add to ~/.zshrc or login items:
#   bash /Users/0xvox/Documents/GitHub/FinceptTerminal/.../services/keepalive.sh start
set -euo pipefail

DIR="/Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/data_algo"
PY="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"
WEBHOOK_LOG="/tmp/p5b_webhook.log"
DAEMON_LOG="/tmp/tv_pw_daemon.log"
WEBHOOK_PID="/tmp/p5b_webhook.pid"
DAEMON_PID="/tmp/tv_pw_daemon.pid"

action="${1:-start}"

start_webhook() {
    if [[ -f "$WEBHOOK_PID" ]] && kill -0 "$(cat $WEBHOOK_PID)" 2>/dev/null; then
        echo "  webhook already running PID=$(cat $WEBHOOK_PID)"
        return
    fi
    # Extract TV_WEBHOOK_SECRET from zshrc without sourcing the whole file
    # (sourcing may hang on interactive prompts / heavy initialization)
    local SECRET
    SECRET="$(/usr/bin/grep -E '^[[:space:]]*export[[:space:]]+TV_WEBHOOK_SECRET=' "$HOME/.zshrc" | tail -1 | sed -E 's/.*=["'\''"]?([^"'\''" ]+).*/\1/')"
    if [[ -z "$SECRET" ]]; then
        echo "  ✖ TV_WEBHOOK_SECRET not found in ~/.zshrc; webhook will reject all alerts"
    fi
    cd "$DIR"
    TV_WEBHOOK_SECRET="$SECRET" P5B_AUTOEXEC_ENABLED=1 P5B_MIN_SCORE=30 P5B_MIN_TQI=0.4 \
        nohup "$PY" -m propfirm_engine.tv_webhook --port 5555 >> "$WEBHOOK_LOG" 2>&1 &
    echo $! > "$WEBHOOK_PID"
    echo "  webhook started PID=$!"
}

start_daemon() {
    if [[ -f "$DAEMON_PID" ]] && kill -0 "$(cat $DAEMON_PID)" 2>/dev/null; then
        echo "  daemon already running PID=$(cat $DAEMON_PID)"
        return
    fi
    cd "$DIR"
    nohup "$PY" -m propfirm_engine.tv_playwright_daemon >> "$DAEMON_LOG" 2>&1 &
    echo $! > "$DAEMON_PID"
    echo "  daemon started PID=$!"
}

stop_one() {
    local pidfile="$1" name="$2"
    if [[ -f "$pidfile" ]]; then
        local pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" && echo "  $name PID=$pid killed" || echo "  $name kill failed"
        else
            echo "  $name PID=$pid already dead"
        fi
        rm -f "$pidfile"
    else
        echo "  $name pidfile missing"
    fi
}

case "$action" in
    start)
        echo "→ starting webhook..."
        start_webhook
        sleep 2
        echo "→ starting daemon..."
        start_daemon
        sleep 4
        echo ""
        echo "── health ──"
        curl -sS --max-time 2 http://127.0.0.1:5555/health 2>&1 | head -1 || echo "  webhook NOT responding"
        curl -sS --max-time 2 http://127.0.0.1:5556/health 2>&1 | head -1 || echo "  daemon NOT responding"
        ;;
    stop)
        echo "→ stopping..."
        stop_one "$WEBHOOK_PID" "webhook"
        stop_one "$DAEMON_PID" "daemon"
        # Belt-and-suspenders: kill any orphan processes
        pkill -f "propfirm_engine.tv_webhook" 2>/dev/null || true
        pkill -f "propfirm_engine.tv_playwright_daemon" 2>/dev/null || true
        ;;
    restart)
        bash "$0" stop
        sleep 2
        bash "$0" start
        ;;
    status)
        echo "── PID files ──"
        for pf in "$WEBHOOK_PID" "$DAEMON_PID"; do
            if [[ -f "$pf" ]]; then
                pid=$(cat "$pf")
                if kill -0 "$pid" 2>/dev/null; then
                    echo "  $(basename $pf .pid): PID=$pid ALIVE"
                else
                    echo "  $(basename $pf .pid): PID=$pid DEAD (stale pidfile)"
                fi
            else
                echo "  $(basename $pf .pid): not started"
            fi
        done
        echo ""
        echo "── Ports ──"
        for port in 5555 5556; do
            pid=$(lsof -ti :$port 2>/dev/null || true)
            [[ -n "$pid" ]] && echo "  :$port PID=$pid" || echo "  :$port empty"
        done
        echo ""
        echo "── Health ──"
        curl -sS --max-time 2 http://127.0.0.1:5555/health 2>&1 | head -1 || echo "  webhook DOWN"
        curl -sS --max-time 2 http://127.0.0.1:5556/health 2>&1 | head -1 || echo "  daemon DOWN"
        ;;
    logs)
        echo "── tailing both logs (Ctrl-C to exit) ──"
        tail -f "$WEBHOOK_LOG" "$DAEMON_LOG"
        ;;
    *) echo "usage: keepalive.sh [start|stop|restart|status|logs]"; exit 1 ;;
esac
