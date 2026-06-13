#!/bin/bash
# Append one cost entry to ~/.claude/v2-cost.jsonl
# Usage: log-cost.sh <ticket_id> <model> <phase> <est_input_tokens> <est_output_tokens>
set -euo pipefail
TICKET="${1?ticket_id required}"
MODEL="${2?model required}"
PHASE="${3?phase required}"
INPUT_TOK="${4:-0}"
OUTPUT_TOK="${5:-0}"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
LEDGER="${CLAUDE_COST_LEDGER:-$HOME/.claude/v2-cost.jsonl}"
echo "{\"ts\":\"$TIMESTAMP\",\"ticket\":\"$TICKET\",\"model\":\"$MODEL\",\"phase\":\"$PHASE\",\"input_tokens\":$INPUT_TOK,\"output_tokens\":$OUTPUT_TOK}" >> "$LEDGER"
echo "Logged: $TICKET $MODEL $PHASE ($INPUT_TOK in / $OUTPUT_TOK out)"
