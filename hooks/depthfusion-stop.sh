#!/usr/bin/env bash
# DepthFusion Stop Hook — CM-1/CM-6 capture on session end
#
# Install: add to ~/.claude/settings.json hooks.Stop array, or
#          run `python3 -m depthfusion.install.install --install-hooks`
#
# This hook runs the LLM decision extractor and negative extractor on the
# current session's .tmp file, then compresses it into a discovery file.
#
# Environment variables honoured:
#   DEPTHFUSION_PROJECT   — project slug (default: auto-detect from git)
#   DEPTHFUSION_SESSION   — session ID (injected by Claude Code if configured)
#   DEPTHFUSION_DECISION_EXTRACTOR_ENABLED — set to "true" to enable LLM extraction
#
# Spec: docs/plans/v0.5/01-assessment.md §CM-1
# Backlog: T-138 (S-45)

set -euo pipefail

DEPTHFUSION_DIR="${DEPTHFUSION_DIR:-$(python3 -c "import depthfusion; import os; print(os.path.dirname(depthfusion.__file__))" 2>/dev/null || echo "")}"

if [ -z "$DEPTHFUSION_DIR" ]; then
    # depthfusion not installed or not in PATH — exit silently
    exit 0
fi

SESSION_FILE="${DEPTHFUSION_SESSION_FILE:-}"
PROJECT="${DEPTHFUSION_PROJECT:-}"

# Locate the most-recent .tmp session file if not explicitly provided
if [ -z "$SESSION_FILE" ]; then
    SESSIONS_DIR="${HOME}/.claude/sessions"
    if [ -d "$SESSIONS_DIR" ]; then
        SESSION_FILE=$(ls -t "${SESSIONS_DIR}"/*.tmp 2>/dev/null | head -1 || echo "")
    fi
fi

if [ -z "$SESSION_FILE" ] || [ ! -f "$SESSION_FILE" ]; then
    exit 0
fi

# Run the capture pipeline (Python handles all error recovery)
python3 -c "
import sys, os
os.environ.setdefault('DEPTHFUSION_DECISION_EXTRACTOR_ENABLED', '${DEPTHFUSION_DECISION_EXTRACTOR_ENABLED:-false}')

from pathlib import Path
from depthfusion.capture.compressor import SessionCompressor

session = Path('${SESSION_FILE}')
project = '${PROJECT}' or None

try:
    compressor = SessionCompressor()
    out = compressor.compress(session)
    if out:
        print(f'DepthFusion: compressed {session.name} -> {out.name}', file=sys.stderr)
except Exception as e:
    # Never fail the stop hook
    print(f'DepthFusion stop hook error: {e}', file=sys.stderr)
" 2>&1 || true

exit 0
