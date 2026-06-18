#!/usr/bin/env bash
# DepthFusion Service Heartbeat Reporter
# Checks service health and commits a status file to agent-hub-context.

set -euo pipefail

TIMESTAMP_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TIMESTAMP_FILE=$(date -u +%Y-%m-%dT%H-%M-%S)
DATE_ONLY=$(date -u +%Y-%m-%d)

CONTEXT_DIR="/home/gregmorris/agent-hub-context"
SESSION_DIR="${CONTEXT_DIR}/sessions/depthfusion"
HEARTBEAT_FILE="${SESSION_DIR}/heartbeat-${TIMESTAMP_FILE}.md"
ALERTS_DIR="/home/gregmorris/projects/depthfusion/.agent-hub/outputs/alerts"

mkdir -p "${SESSION_DIR}" "${ALERTS_DIR}"

# ---------------------------------------------------------------------------
# Check depthfusion-mcp on port 7301
# ---------------------------------------------------------------------------
MCP_BINDING=$(ss -tlnp 2>/dev/null | awk '$4 ~ /:7301$/ {print $4}' | head -1)
MCP_HEALTH_DETAIL=""
if [[ -n "${MCP_BINDING}" ]]; then
    HTTP_CODE=$(curl -s -o /tmp/df_mcp_health.json -w "%{http_code}" --max-time 5 http://127.0.0.1:7301/health 2>/dev/null || echo "000")
    if [[ "${HTTP_CODE}" == "200" ]]; then
        HEALTH_JSON=$(cat /tmp/df_mcp_health.json 2>/dev/null || echo "{}")
        MCP_STATUS="ACTIVE"
        MCP_ICON="✅"
        MCP_DETAIL="${MCP_BINDING} | ${HEALTH_JSON}"
    else
        MCP_STATUS="DEGRADED"
        MCP_ICON="⚠️"
        MCP_DETAIL="${MCP_BINDING} | health HTTP ${HTTP_CODE}"
    fi
else
    MCP_STATUS="DOWN"
    MCP_ICON="❌"
    MCP_DETAIL="no listener on 7301"
fi

# ---------------------------------------------------------------------------
# Check depthfusion-rest on port 7300
# ---------------------------------------------------------------------------
REST_BINDING=$(ss -tlnp 2>/dev/null | awk '$4 ~ /:7300$/ {print $4}' | head -1)
if [[ -n "${REST_BINDING}" ]]; then
    REST_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:7300/health 2>/dev/null || echo "000")
    if [[ "${REST_HTTP}" =~ ^(200|404)$ ]]; then
        REST_STATUS="ACTIVE"
        REST_ICON="✅"
        REST_DETAIL="${REST_BINDING} | HTTP ${REST_HTTP}"
    else
        REST_STATUS="DEGRADED"
        REST_ICON="⚠️"
        REST_DETAIL="${REST_BINDING} | health HTTP ${REST_HTTP}"
    fi
else
    REST_STATUS="DOWN"
    REST_ICON="❌"
    REST_DETAIL="no listener on 7300"
fi

# ---------------------------------------------------------------------------
# Check OQ-4: cloudflared metrics on port 20241
# ---------------------------------------------------------------------------
OQ4_RAW=$(ss -tlnp 2>/dev/null | grep ':20241' || true)
if [[ -z "${OQ4_RAW}" ]]; then
    OQ4_STATUS="NOT_RUNNING"
    OQ4_ICON="✅"
    OQ4_DETAIL="port 20241 not listening"
    OQ4_LABEL="RESOLVED (not running)"
else
    OQ4_ADDR=$(echo "${OQ4_RAW}" | awk '{print $4}' | head -1)
    if echo "${OQ4_ADDR}" | grep -qE '^(127\.0\.0\.1|::1):20241$'; then
        OQ4_STATUS="LOOPBACK"
        OQ4_ICON="✅"
        OQ4_DETAIL="${OQ4_ADDR}"
        OQ4_LABEL="RESOLVED (loopback)"
    else
        OQ4_STATUS="OPEN"
        OQ4_ICON="⚠️"
        OQ4_DETAIL="${OQ4_ADDR} — public bind (OQ-4 unresolved)"
        OQ4_LABEL="OPEN (*:20241)"
    fi
fi

# ---------------------------------------------------------------------------
# Overall health
# ---------------------------------------------------------------------------
if [[ "${MCP_STATUS}" == "DOWN" || "${REST_STATUS}" == "DOWN" ]]; then
    OVERALL="DOWN"
elif [[ "${MCP_STATUS}" == "DEGRADED" || "${REST_STATUS}" == "DEGRADED" || "${OQ4_STATUS}" == "OPEN" ]]; then
    OVERALL="DEGRADED"
else
    OVERALL="HEALTHY"
fi

# ---------------------------------------------------------------------------
# Write heartbeat markdown
# ---------------------------------------------------------------------------
cat > "${HEARTBEAT_FILE}" <<EOF
---
date: ${DATE_ONLY}
type: heartbeat
project: depthfusion
---
# DepthFusion Heartbeat — ${TIMESTAMP_ISO}

| Service | Status | Detail |
|---------|--------|--------|
| depthfusion-mcp (7301) | ${MCP_ICON} ${MCP_STATUS} | ${MCP_DETAIL} |
| depthfusion-rest (7300) | ${REST_ICON} ${REST_STATUS} | ${REST_DETAIL} |
| cloudflared metrics (20241) | ${OQ4_ICON} ${OQ4_STATUS} | ${OQ4_DETAIL} |

**Overall:** ${OVERALL}
**OQ-4 (cloudflared metrics):** ${OQ4_LABEL}
EOF

# ---------------------------------------------------------------------------
# Write alert file if any service is DOWN
# ---------------------------------------------------------------------------
if [[ "${OVERALL}" == "DOWN" ]]; then
    ALERT_FILE="${ALERTS_DIR}/alert-${TIMESTAMP_FILE}.md"
    cat > "${ALERT_FILE}" <<EOF
---
date: ${DATE_ONLY}
type: alert
project: depthfusion
severity: CRITICAL
---
# DepthFusion Service Alert — ${TIMESTAMP_ISO}

**Overall status: DOWN**

| Service | Status | Detail |
|---------|--------|--------|
| depthfusion-mcp (7301) | ${MCP_ICON} ${MCP_STATUS} | ${MCP_DETAIL} |
| depthfusion-rest (7300) | ${REST_ICON} ${REST_STATUS} | ${REST_DETAIL} |
| cloudflared metrics (20241) | ${OQ4_ICON} ${OQ4_STATUS} | ${OQ4_DETAIL} |

Action required: check systemctl --user status depthfusion-mcp depthfusion-rest
EOF
    echo "ALERT written: ${ALERT_FILE}" >&2
fi

# ---------------------------------------------------------------------------
# Commit and push to agent-hub-context
# ---------------------------------------------------------------------------
cd "${CONTEXT_DIR}"
git add "${HEARTBEAT_FILE}"
if git diff --cached --quiet; then
    echo "No changes to commit." >&2
else
    git commit -m "depthfusion: heartbeat ${TIMESTAMP_FILE}"
    git push
fi

echo "Heartbeat written: ${HEARTBEAT_FILE}"
echo "Overall: ${OVERALL}"
