#!/bin/bash
# Gemini code review wrapper for V2 consensus pipeline
# Usage: review-gemini.sh <git-range> [--checklist <comma-sep-items>]
set -euo pipefail

GIT_RANGE="${1?git range required}"
CHECKLIST="${3:-correctness,security,conventions,test coverage}"

DIFF=$(git diff "$GIT_RANGE" 2>/dev/null || git show "$GIT_RANGE" 2>/dev/null || true)
if [[ -z "$DIFF" ]]; then
  echo '{"reviewer":"gemini","verdict":"approve","findings":[],"note":"empty diff"}'
  exit 0
fi

if ! command -v gemini &>/dev/null; then
  echo '{"reviewer":"gemini","verdict":"object","findings":[{"severity":"high","claim":"gemini CLI not found","fix":"install gemini-cli"}]}'
  exit 0
fi

DIFF_TRUNCATED=$(echo "$DIFF" | head -800)
gemini -p "You are a code reviewer. Review this git diff against: ${CHECKLIST}.

DIFF:
\`\`\`
${DIFF_TRUNCATED}
\`\`\`

Return ONLY valid JSON: {\"reviewer\":\"gemini\",\"verdict\":\"approve\"|\"object\",\"findings\":[{\"severity\":\"critical\"|\"high\"|\"medium\"|\"low\",\"file\":\"...\",\"line\":0,\"claim\":\"...\",\"fix\":\"...\"}]}
verdict=object if any medium+ findings exist." 2>/dev/null || echo '{"reviewer":"gemini","verdict":"object","findings":[{"severity":"high","claim":"gemini CLI runtime error","fix":"check gemini-cli setup"}]}'
