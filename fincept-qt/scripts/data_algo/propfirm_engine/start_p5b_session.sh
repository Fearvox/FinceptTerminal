#!/usr/bin/env bash
# Propfirm v4 P5b — session-start helper.
#
# Brings up tv_webhook on :5555 + a fresh quick cloudflared tunnel,
# then extracts the public URL and pbcopies it so the operator can
# paste into TV alert config.
#
# Idempotent: kills prior webhook on :5555 (if any) and prior quick
# tunnel process before starting new ones. Named cloudflared tunnel
# (anda-proxy) is NEVER touched — verified via pgrep arg match.
#
# Usage:
#   bash start_p5b_session.sh                # interactive, prints status
#   bash start_p5b_session.sh --no-tunnel    # just webhook (for local dev)
#
# Logs:
#   /tmp/p5b_webhook.log     tv_webhook stderr
#   /tmp/p5b_tunnel.log      cloudflared stderr (contains the URL line)
set -euo pipefail

NO_TUNNEL=0
if [[ "${1:-}" == "--no-tunnel" ]]; then NO_TUNNEL=1; fi

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
DATA_ALGO="$REPO_ROOT/fincept-qt/scripts/data_algo"
PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3

# ── 1. Resolve secret — priority: ~/.secrets/ file → env → ~/.zshrc ─────
# The .secrets/ file is the canonical home (mode 0600, no shell history,
# survives restarts). It takes priority over env so that the operator
# rotating the secret only has to touch one place: the file. Env stays
# as a CI/automation fallback.
SECRET_FILE="${TV_WEBHOOK_SECRET_FILE:-$HOME/.secrets/Webhook_Secret_FinceptEdgev2.txt}"

if [[ -r "$SECRET_FILE" ]]; then
    # Trim trailing whitespace/newlines so the exact-match check in
    # tv_webhook._check_secret() passes.
    FILE_SECRET="$(tr -d '[:space:]' < "$SECRET_FILE")"
    if [[ -n "$FILE_SECRET" ]]; then
        if [[ -n "${TV_WEBHOOK_SECRET:-}" ]] && [[ "$TV_WEBHOOK_SECRET" != "$FILE_SECRET" ]]; then
            echo "⚠ env TV_WEBHOOK_SECRET (${#TV_WEBHOOK_SECRET} chars) differs from $SECRET_FILE (${#FILE_SECRET} chars) — file wins"
        fi
        TV_WEBHOOK_SECRET="$FILE_SECRET"
        export TV_WEBHOOK_SECRET
        echo "✓ TV_WEBHOOK_SECRET loaded from $SECRET_FILE"
    fi
fi
if [[ -z "${TV_WEBHOOK_SECRET:-}" ]]; then
    # shellcheck disable=SC1090
    source ~/.zshrc 2>/dev/null || true
fi
if [[ -z "${TV_WEBHOOK_SECRET:-}" ]]; then
    echo "✖ TV_WEBHOOK_SECRET not set (tried $SECRET_FILE, env, ~/.zshrc)" >&2
    exit 1
fi
echo "✓ TV_WEBHOOK_SECRET present (${#TV_WEBHOOK_SECRET} chars)"

# ── 2. Kill prior webhook on :5555 (if any) ───────────────────────────────
PRIOR_WEBHOOK=$(lsof -ti :5555 2>/dev/null || true)
if [[ -n "$PRIOR_WEBHOOK" ]]; then
    echo "⚠ killing prior webhook PID(s): $PRIOR_WEBHOOK"
    kill $PRIOR_WEBHOOK 2>/dev/null || true
    sleep 1
fi

# ── 3. Start webhook ──────────────────────────────────────────────────────
cd "$DATA_ALGO"
nohup "$PY" -m propfirm_engine.tv_webhook --port 5555 \
    > /tmp/p5b_webhook.log 2>&1 &
WEBHOOK_PID=$!
sleep 1
if ! lsof -ti :5555 > /dev/null 2>&1; then
    echo "✖ webhook failed to bind :5555, see /tmp/p5b_webhook.log" >&2
    cat /tmp/p5b_webhook.log >&2
    exit 1
fi
echo "✓ webhook started PID=$WEBHOOK_PID port=5555"
if ! curl -s --max-time 2 http://127.0.0.1:5555/health > /dev/null; then
    echo "⚠ /health did not respond (continuing)"
fi

if [[ "$NO_TUNNEL" == "1" ]]; then
    # If an existing quick tunnel is already up (common after a secret
    # rotation where we only want to reload webhook), surface its URL +
    # the freshly-built full alert URL so the operator can pbcopy it
    # straight into TV without re-running the full launcher.
    EXISTING_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/p5b_tunnel.log 2>/dev/null | tail -1 || true)
    if [[ -n "$EXISTING_URL" ]]; then
        FULL_URL="${EXISTING_URL}/tv-signal?secret=${TV_WEBHOOK_SECRET}"
        # Push to clipboard so the secret never lands in shell history /
        # this script's stdout. The plain URL (no secret) is printed for
        # context, then the full URL goes silently to pbcopy.
        printf "%s" "$FULL_URL" | pbcopy
        echo ""
        echo "✓ session ready locally (no tunnel)."
        echo "✓ existing tunnel: $EXISTING_URL"
        echo "✓ full TV alert URL (with new secret) copied to clipboard — paste into TV alert"
    else
        echo ""
        echo "✓ session ready locally (no tunnel). Use curl 127.0.0.1:5555 for tests."
    fi
    exit 0
fi

# ── 4. Kill prior quick tunnel (do NOT touch named anda-proxy tunnel) ─────
# Match: cloudflared ... --url http://localhost:5555 (quick tunnels have --url)
PRIOR_TUNNEL=$(pgrep -f "cloudflared.*--url.*5555" || true)
if [[ -n "$PRIOR_TUNNEL" ]]; then
    echo "⚠ killing prior quick tunnel PID(s): $PRIOR_TUNNEL"
    kill $PRIOR_TUNNEL 2>/dev/null || true
    sleep 1
fi

# ── 5. Start fresh quick tunnel ──────────────────────────────────────────
nohup cloudflared tunnel --config /dev/null \
    --url http://localhost:5555 \
    > /tmp/p5b_tunnel.log 2>&1 &
TUNNEL_PID=$!
echo "✓ tunnel started PID=$TUNNEL_PID, waiting for URL..."

# ── 6. Wait for URL to appear in log (up to 15s) ─────────────────────────
URL=""
for i in $(seq 1 15); do
    sleep 1
    # Quick tunnels print: "https://*.trycloudflare.com" in their banner
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/p5b_tunnel.log | head -1 || true)
    if [[ -n "$URL" ]]; then
        break
    fi
done

if [[ -z "$URL" ]]; then
    echo "✖ quick tunnel did not surface URL in 15s. Log:" >&2
    tail -30 /tmp/p5b_tunnel.log >&2
    exit 1
fi
echo "✓ tunnel URL: $URL"

# ── 7. Build the full TV alert webhook URL (with secret) ─────────────────
FULL_URL="${URL}/tv-signal?secret=${TV_WEBHOOK_SECRET}"
echo "$FULL_URL" | pbcopy
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "TV ALERT WEBHOOK URL (copied to clipboard):"
echo ""
echo "  $FULL_URL"
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Open TV, edit your Willy alert"
echo "  2. Webhook URL field: cmd-V (URL is on clipboard)"
echo "  3. Alert message: paste from pine_willy_alert_template.pine"
echo "  4. Save alert, fire a test bar-close to verify"
echo ""
echo "Live status:"
echo "  webhook log: tail -f /tmp/p5b_webhook.log"
echo "  tunnel log:  tail -f /tmp/p5b_tunnel.log"
echo "  journal:     python3 -m propfirm_engine.log_tv_trade list-open"
echo ""
echo "On exit:"
echo "  kill $WEBHOOK_PID $TUNNEL_PID    # stop both"
