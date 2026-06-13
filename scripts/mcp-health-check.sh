#!/bin/bash
# DepthFusion MCP Health Check
# Runs 2x/day via cron: 0 6,18 * * * (06:00 UTC = 08:00 SAST, 18:00 UTC = 20:00 SAST)

set -euo pipefail

LOG="/home/gregmorris/projects/depthfusion/.pm/logs/mcp-health.log"
SERVICE="depthfusion-mcp.service"
LOCAL_HEALTH_URL="http://127.0.0.1:7301/health"
PUBLIC_HEALTH_URL="https://depthfusion.tonracein.com/health"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
MAX_LOG_LINES=500

rotate_log() {
    if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt "$MAX_LOG_LINES" ]; then
        tail -200 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
    fi
}

rotate_log

# Check 1: local HTTP health endpoint (fast, no TLS overhead)
LOCAL_STATUS=$(curl -s -o /tmp/df_health_local.json -w "%{http_code}" --max-time 5 "$LOCAL_HEALTH_URL" 2>/dev/null || echo "000")

if [ "$LOCAL_STATUS" = "200" ]; then
    BODY=$(cat /tmp/df_health_local.json 2>/dev/null || echo "")
    echo "$TIMESTAMP OK local=$LOCAL_STATUS body=$BODY" >> "$LOG"
    exit 0
fi

# Local check failed — try public URL as secondary verification
PUBLIC_STATUS=$(curl -s -o /tmp/df_health_public.json -w "%{http_code}" --max-time 10 "$PUBLIC_HEALTH_URL" 2>/dev/null || echo "000")

if [ "$PUBLIC_STATUS" = "200" ]; then
    # Public OK but local failed — nginx is up but direct port may have changed
    echo "$TIMESTAMP WARN local=$LOCAL_STATUS public=$PUBLIC_STATUS — local port unreachable, public OK" >> "$LOG"
    exit 0
fi

# Both checks failed — service is down
SERVICE_STATE=$(systemctl is-active "$SERVICE" 2>/dev/null || echo "unknown")
echo "$TIMESTAMP FAIL local=$LOCAL_STATUS public=$PUBLIC_STATUS service=$SERVICE_STATE — attempting restart" >> "$LOG"

# Attempt restart (requires sudo; will succeed if sudoers allows it)
if sudo -n systemctl restart "$SERVICE" 2>/dev/null; then
    sleep 8
    AFTER_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$LOCAL_HEALTH_URL" 2>/dev/null || echo "000")
    if [ "$AFTER_STATUS" = "200" ]; then
        echo "$TIMESTAMP RECOVERED restart=ok post_check=$AFTER_STATUS" >> "$LOG"
    else
        echo "$TIMESTAMP RESTART_FAILED post_check=$AFTER_STATUS — manual intervention required" >> "$LOG"
    fi
else
    echo "$TIMESTAMP NEEDS_MANUAL_RESTART sudo_restart=denied — run: sudo systemctl restart $SERVICE" >> "$LOG"
fi

exit 1
