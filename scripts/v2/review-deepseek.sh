#!/bin/bash
# DeepSeek code review wrapper for V2 consensus pipeline
# Usage: review-deepseek.sh <git-range> [--checklist <comma-sep-items>]
# Output: JSON verdict: {reviewer, verdict: "approve"|"object", findings: [{severity, file, line, claim, fix}]}
set -euo pipefail

GIT_RANGE="${1?git range required (e.g. HEAD~1..HEAD)}"
CHECKLIST="${3:-correctness,security,conventions,test coverage}"

# Get the diff
DIFF=$(git diff "$GIT_RANGE" 2>/dev/null || git show "$GIT_RANGE" 2>/dev/null || true)
if [[ -z "$DIFF" ]]; then
  echo '{"reviewer":"deepseek","verdict":"approve","findings":[],"note":"empty diff"}'
  exit 0
fi

# Check deepseek CLI availability
if ! command -v deepseek &>/dev/null; then
  echo '{"reviewer":"deepseek","verdict":"object","findings":[{"severity":"high","claim":"deepseek CLI not found — install deepseek-cli or set PATH","fix":"install deepseek CLI"}]}'
  exit 0
fi

PROMPT="You are a code reviewer. Review this git diff against these criteria: ${CHECKLIST}.

DIFF:
\`\`\`
$(echo "$DIFF" | head -500)
\`\`\`

Return ONLY valid JSON in this exact format:
{\"reviewer\":\"deepseek\",\"verdict\":\"approve\",\"findings\":[{\"severity\":\"medium\",\"file\":\"path/to/file.py\",\"line\":42,\"claim\":\"what is wrong\",\"fix\":\"how to fix\"}]}

verdict must be \"approve\" if no medium/high/critical findings, else \"object\".
findings array may be empty. severity: critical|high|medium|low."

deepseek chat "$PROMPT" --json 2>/dev/null || echo '{"reviewer":"deepseek","verdict":"object","findings":[{"severity":"high","claim":"deepseek CLI runtime error","fix":"check deepseek CLI setup"}]}'
