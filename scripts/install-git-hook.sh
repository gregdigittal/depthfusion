#!/usr/bin/env bash
# install-git-hook.sh — opt-in DepthFusion git post-commit hook installer
#
# Usage:
#   bash scripts/install-git-hook.sh                  # installs for current repo
#   bash scripts/install-git-hook.sh /path/to/repo    # installs for specified repo
#
# Behaviour:
#   - If no post-commit hook exists: creates one with the DepthFusion block
#   - If an existing hook is found: appends the DepthFusion block only if
#     the DepthFusion sentinel comment is NOT already present (idempotent)
#   - Never modifies hooks that already have the DepthFusion block
#
# Spec: docs/plans/v0.5/01-assessment.md §CM-3
# Backlog: T-141 (S-46)

set -euo pipefail

REPO_DIR="${1:-$(git rev-parse --show-toplevel 2>/dev/null || echo "")}"

if [ -z "$REPO_DIR" ]; then
    echo "Error: not inside a git repository and no path provided." >&2
    exit 1
fi

HOOK_FILE="${REPO_DIR}/.git/hooks/post-commit"
SENTINEL="# DepthFusion post-commit hook"

DF_BLOCK=$(cat <<'EOF'

# DepthFusion post-commit hook — added by scripts/install-git-hook.sh
# Remove this block to disable DepthFusion commit capture (CM-3)
if command -v python3 &>/dev/null; then
    python3 -m depthfusion.hooks.git_post_commit 2>/dev/null || true
fi
EOF
)

if [ ! -d "${REPO_DIR}/.git" ]; then
    echo "Error: ${REPO_DIR}/.git does not exist." >&2
    exit 1
fi

mkdir -p "${REPO_DIR}/.git/hooks"

if [ ! -f "$HOOK_FILE" ]; then
    # No existing hook — create one
    printf '#!/usr/bin/env bash\nset -e\n%s\n' "$DF_BLOCK" > "$HOOK_FILE"
    chmod +x "$HOOK_FILE"
    echo "Created ${HOOK_FILE} with DepthFusion post-commit block."
elif grep -qF "$SENTINEL" "$HOOK_FILE"; then
    echo "DepthFusion block already present in ${HOOK_FILE} — no changes made."
else
    # Append to existing hook
    printf '%s\n' "$DF_BLOCK" >> "$HOOK_FILE"
    echo "Appended DepthFusion block to existing ${HOOK_FILE}."
fi
