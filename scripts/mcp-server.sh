#!/usr/bin/env bash
# scripts/mcp-server.sh — thin wrapper so `claude mcp add` can register
# the stdio server without -m being intercepted by the Claude CLI parser.
#
# Usage (register once):
#   claude mcp add depthfusion -s user \
#     /home/gregmorris/projects/depthfusion/scripts/mcp-server.sh
#
# The script exec's python3 so it replaces the shell process and stdio
# passes through cleanly to the MCP server.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${SCRIPT_DIR}/../.venv/bin/python3"

if [[ ! -x "${VENV_PYTHON}" ]]; then
    echo "error: venv python not found at ${VENV_PYTHON}" >&2
    echo "       Run: cd ${SCRIPT_DIR}/.. && python3 -m venv .venv && pip install -e ." >&2
    exit 1
fi

exec "${VENV_PYTHON}" -m depthfusion.mcp.server "$@"
