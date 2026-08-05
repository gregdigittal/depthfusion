#!/bin/bash
# Rollback: recreate 7 stale local branches deleted after PR #50 merge
# Created: 2026-08-05T05:45:08Z
# Project: depthfusion | main at 1a4c946
# Note: 3 branches had unique commits (content already present in main via
#       other routes). Recoverable until git gc prunes unreachable objects.

git branch claude/depthfusion-mcp-connection-967efd ab1783735e1cad88ba27622c8860e5b2fdaf1485
git branch feat/e72-packaging-integrity fad1c4545542909a1972bf103644910c02f2e5eb
git branch feat/e72-w3-http-compliance 5ea39989c01cbc5cc34f622fb93f516addf32db4
git branch feat/e72-w3-packaging-gate 5ea39989c01cbc5cc34f622fb93f516addf32db4
git branch feat/e72-w4-http-compliance 7cef0f5bdcbc2fd3af69df84054695e0c56fe0be
git branch feat/e72-wave2-packaging-gate ead75ef3acf78ea4871af5ef3f368b13fd54e40d
git branch feat/e73-s246-s252 e8fa42add5507a8a103dc12450dac6c5793c719a

echo "Rollback complete."

# Worktree also removed (was clean): git worktree add .claude/worktrees/depthfusion-mcp-connection-967efd claude/depthfusion-mcp-connection-967efd

# --- Remote counterparts deleted 2026-08-05 (no open PRs at time of deletion) ---
# Restore: recreate the local branch above, then push it back:
# git push origin fad1c4545542909a1972bf103644910c02f2e5eb:refs/heads/feat/e72-packaging-integrity
# git push origin 5ea39989c01cbc5cc34f622fb93f516addf32db4:refs/heads/feat/e72-w3-packaging-gate
# git push origin 7cef0f5bdcbc2fd3af69df84054695e0c56fe0be:refs/heads/feat/e72-w4-http-compliance
# git push origin ead75ef3acf78ea4871af5ef3f368b13fd54e40d:refs/heads/feat/e72-wave2-packaging-gate
# git push origin e8fa42add5507a8a103dc12450dac6c5793c719a:refs/heads/feat/e73-s246-s252
