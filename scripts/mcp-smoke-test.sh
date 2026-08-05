#!/usr/bin/env bash
# DepthFusion MCP Streamable HTTP Smoke Test — E-73 S-246 T-838
#
# Verifies the initialize -> tools/list Streamable HTTP round-trip documented
# in docs/mcp-client-setup.md ("Verifying the Connection").
#
# Target selection:
#   - If DEPTHFUSION_MCP_TOKEN is set AND https://mcp.tonracein.com/health
#     responds within a short timeout, the round-trip runs LIVE against
#     mcp.tonracein.com using the caller's token.
#   - Otherwise this prints "SKIP: live endpoint unreachable / no token —
#     falling back to local" and runs the identical round-trip against a
#     loopback mcp/http_server.py instance, authenticated with a freshly
#     generated test token (DEPTHFUSION_V2_LEGACY_AUTH=1). No token or
#     credential is ever hardcoded in this script.
#
# Exit codes:
#   0  Round-trip succeeded against whichever target was actually used
#   1  Round-trip failed against whichever target was actually used
#   2  Setup failure (missing tooling, local server failed to start, etc.)
#      — NOT raised merely because the live endpoint is unreachable; that
#        condition triggers the local fallback instead.
#
# Usage:
#   bash scripts/mcp-smoke-test.sh
#
# Environment variables:
#   DEPTHFUSION_MCP_TOKEN        Bearer token for the live server (optional;
#                                 absence triggers the local fallback)
#   DEPTHFUSION_SMOKE_MCP_PORT   Override the local fallback port (default 7398)
#   DEPTHFUSION_SMOKE_TIMEOUT    Seconds to wait for the local server to start
#                                 (default 15)

set -euo pipefail

LIVE_BASE_URL="https://mcp.tonracein.com"
LIVE_PROBE_TIMEOUT=5
LOCAL_PORT="${DEPTHFUSION_SMOKE_MCP_PORT:-7398}"
LOCAL_BASE_URL="http://127.0.0.1:${LOCAL_PORT}"
STARTUP_TIMEOUT="${DEPTHFUSION_SMOKE_TIMEOUT:-15}"

TARGET=""
BASE_URL=""
TOKEN=""
SERVER_PID=""
TMPDIR_SMOKE=""
WORKDIR="$(mktemp -d)"

log()  { printf '[mcp-smoke] %s\n' "$*"; }
fail() { printf '[mcp-smoke] FAIL: %s\n' "$*" >&2; exit 1; }
die()  { printf '[mcp-smoke] ERROR: %s\n' "$*" >&2; exit 2; }

cleanup() {
    if [[ -n "${SERVER_PID}" ]]; then
        kill "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
    fi
    rm -rf "${WORKDIR}" 2>/dev/null || true
    if [[ -n "${TMPDIR_SMOKE}" && -d "${TMPDIR_SMOKE}" ]]; then
        rm -rf "${TMPDIR_SMOKE}"
    fi
}
trap cleanup EXIT

command -v curl >/dev/null 2>&1 || die "curl not found"
command -v python3 >/dev/null 2>&1 || die "python3 not found"

# ---------------------------------------------------------------------------
# Choose target: live server (token set + reachable) or loopback fallback
# ---------------------------------------------------------------------------
if [[ -n "${DEPTHFUSION_MCP_TOKEN:-}" ]] && \
   curl -sf --max-time "${LIVE_PROBE_TIMEOUT}" "${LIVE_BASE_URL}/health" >/dev/null 2>&1; then
    TARGET="live"
    BASE_URL="${LIVE_BASE_URL}"
    TOKEN="${DEPTHFUSION_MCP_TOKEN}"
    log "DEPTHFUSION_MCP_TOKEN set and ${LIVE_BASE_URL} reachable — testing LIVE"
else
    TARGET="local"
    log "SKIP: live endpoint unreachable / no token — falling back to local"

    command -v openssl >/dev/null 2>&1 || die "openssl not found (needed to generate local test token)"
    python3 -c "import depthfusion" 2>/dev/null || die "depthfusion package not importable — run: pip install -e ."

    TMPDIR_SMOKE="$(mktemp -d)"
    TOKEN="$(openssl rand -hex 32)"
    BASE_URL="${LOCAL_BASE_URL}"

    export DEPTHFUSION_DATA_DIR="${TMPDIR_SMOKE}/data"
    export DEPTHFUSION_DISCOVERIES_DIR="${TMPDIR_SMOKE}/discoveries"
    export DEPTHFUSION_SESSIONS_DIR="${TMPDIR_SMOKE}/sessions"
    mkdir -p "${DEPTHFUSION_DATA_DIR}" "${DEPTHFUSION_DISCOVERIES_DIR}" "${DEPTHFUSION_SESSIONS_DIR}"
    export DEPTHFUSION_V2_LEGACY_AUTH=1
    export DEPTHFUSION_API_TOKEN="${TOKEN}"

    log "Starting loopback MCP HTTP server on ${BASE_URL}..."
    python3 -m uvicorn depthfusion.mcp.http_server:app \
        --host 127.0.0.1 \
        --port "${LOCAL_PORT}" \
        --log-level warning \
        >"${TMPDIR_SMOKE}/server.log" 2>&1 &
    SERVER_PID=$!

    WAITED=0
    until curl -sf --max-time 2 "${BASE_URL}/health" >/dev/null 2>&1; do
        sleep 1
        WAITED=$((WAITED + 1))
        if [[ ${WAITED} -ge ${STARTUP_TIMEOUT} ]]; then
            log "Server log:"
            cat "${TMPDIR_SMOKE}/server.log" >&2
            die "Local MCP server did not become ready within ${STARTUP_TIMEOUT}s"
        fi
    done
    log "Local MCP server ready (${WAITED}s)"
fi

# ---------------------------------------------------------------------------
# Round-trip: initialize -> tools/list (mirrors docs/mcp-client-setup.md)
# ---------------------------------------------------------------------------
log "Step 1/2 [${TARGET}]: initialize..."
INIT_HEADERS="${WORKDIR}/init_headers.txt"
INIT_BODY="${WORKDIR}/init_body.json"
INIT_STATUS=$(curl -s -o "${INIT_BODY}" -D "${INIT_HEADERS}" -w "%{http_code}" \
    -X POST "${BASE_URL}/mcp" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Authorization: Bearer ${TOKEN}" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"mcp-smoke-test","version":"1.0"}}}')

[[ "${INIT_STATUS}" == "200" ]] || fail "initialize returned HTTP ${INIT_STATUS} (target=${TARGET})"

SESSION_ID=$(grep -i '^mcp-session-id:' "${INIT_HEADERS}" | tail -1 | cut -d':' -f2- | tr -d ' \r\n' || true)
if [[ -n "${SESSION_ID}" ]]; then
    log "  session id: ${SESSION_ID}"
elif [[ "${TARGET}" == "local" ]]; then
    # The loopback server (this repo's mcp/http_server.py) always issues an
    # Mcp-Session-Id on initialize — its absence here is a real regression.
    fail "initialize response did not include an Mcp-Session-Id header (target=local)"
else
    # Mcp-Session-Id is optional per MCP spec 2025-03-26 (stateless servers
    # may omit it). The currently deployed mcp.tonracein.com does not emit
    # one; tools/list still works without it, so this is not a round-trip
    # failure — just proceed without the header on the next call.
    log "  note: live server did not return an Mcp-Session-Id header (optional per spec; proceeding statelessly)"
fi

python3 -c "
import json
with open('${INIT_BODY}') as f:
    data = json.load(f)
assert 'result' in data, f'no result in initialize response: {data}'
assert data['result'].get('protocolVersion') == '2025-03-26', f'unexpected protocolVersion: {data}'
print('  initialize result OK: protocolVersion=' + data['result']['protocolVersion'])
" || fail "initialize response failed validation (target=${TARGET})"

log "Step 2/2 [${TARGET}]: tools/list..."
TOOLS_BODY="${WORKDIR}/tools_body.json"
SESSION_HEADER_ARGS=()
if [[ -n "${SESSION_ID}" ]]; then
    SESSION_HEADER_ARGS=(-H "Mcp-Session-Id: ${SESSION_ID}")
fi
TOOLS_STATUS=$(curl -s -o "${TOOLS_BODY}" -w "%{http_code}" \
    -X POST "${BASE_URL}/mcp" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Authorization: Bearer ${TOKEN}" \
    "${SESSION_HEADER_ARGS[@]}" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')

[[ "${TOOLS_STATUS}" == "200" ]] || fail "tools/list returned HTTP ${TOOLS_STATUS} (target=${TARGET})"

TOOL_COUNT=$(python3 -c "
import json
with open('${TOOLS_BODY}') as f:
    data = json.load(f)
tools = data.get('result', {}).get('tools', [])
assert isinstance(tools, list) and len(tools) > 0, f'tools/list returned no tools: {data}'
print(len(tools))
") || fail "tools/list response failed validation (target=${TARGET})"

log "  tools/list result OK: tool_count=${TOOL_COUNT}"
log ""
log "==========================================="
log "  MCP Smoke Test: PASSED (target=${TARGET})"
log "  tool_count=${TOOL_COUNT}"
log "==========================================="

exit 0
