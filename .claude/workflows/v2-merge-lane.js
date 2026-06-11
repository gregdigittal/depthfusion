export const meta = {
  name: 'v2-merge-lane',
  description: 'Merge a lane branch into v2-enterprise: collision-detect → green-loop → ACL-leak → merge',
  whenToUse: 'When a lane has passed its gate criterion and is ready to merge into v2-enterprise. args: {lane, branch, worktree, skipGreenLoop?}',
  phases: [
    { title: 'Collision' },
    { title: 'Green' },
    { title: 'Merge' },
    { title: 'Post-merge' },
  ],
}

// schemas
const COLLISION_REPORT = {
  type: 'object',
  required: ['hasConflicts', 'conflictFiles', 'mcpServerConflict', 'recommendation'],
  properties: {
    hasConflicts: { type: 'boolean' },
    conflictFiles: { type: 'array', items: { type: 'string' } },
    mcpServerConflict: { type: 'boolean' },   // mcp/server.py or mcp/server.py-adjacent
    recommendation: { type: 'string' },        // 'merge-safe' | 'rebase-first' | 'manual-resolution-required'
    notes: { type: 'string' },
  },
}

const MERGE_RESULT = {
  type: 'object',
  required: ['merged', 'mergeCommit'],
  properties: {
    merged: { type: 'boolean' },
    mergeCommit: { type: 'string' },
    message: { type: 'string' },
  },
}

const ACL_RESULT = {
  type: 'object',
  required: ['passed', 'failures'],
  properties: {
    passed: { type: 'boolean' },
    failures: { type: 'array', items: { type: 'string' } },
    output: { type: 'string' },
  },
}

// args normalisation
const a = typeof args === 'string' ? JSON.parse(args) : (args || {})
if (!a.lane)     throw new Error('args.lane is required (e.g. "a", "b", "c", "d")')
if (!a.branch)   throw new Error('args.branch is required (e.g. "v2/lane-a-authz")')
if (!a.worktree) throw new Error('args.worktree is required (absolute path to worktree)')

const lane = a.lane.toLowerCase()
const branch = a.branch
const wt = a.worktree

// ---- Phase 1: collision detection ------------------------------------------
// Dry-run merge to find conflicts BEFORE touching the working tree.
// mcp/server.py is the highest-risk file — all lanes can collide there.
phase('Collision')
log(`checking ${branch} → v2-enterprise for conflicts`)

const collisions = await agent(
  `You are a git collision analyst. Check whether the branch ${branch} can be merged into v2-enterprise cleanly in the worktree at ${wt}.

Procedure:
1. Run: git -C ${wt} fetch origin ${branch}:${branch} 2>/dev/null || true
2. Run: git -C ${wt} merge-tree $(git -C ${wt} merge-base HEAD ${branch}) HEAD ${branch}
   (merge-tree with 3 args does a dry-run and prints conflicting paths)
3. Check the output for conflict markers (<<<<<<<, >>>>>>>)
4. Identify ALL conflicting files.
5. Flag mcpServerConflict=true if ANY of these appear in the conflict list:
   - mcp/server.py
   - mcp/__init__.py
   - mcp/router.py
   - mcp/auth.py
   (These are the shared MCP registration and auth layer — every lane can touch them)
6. If merge-tree is unavailable, fall back to:
   git -C ${wt} checkout -b __dry-run-merge__ ${branch} 2>/dev/null
   git -C ${wt} merge --no-commit --no-ff v2-enterprise
   git -C ${wt} merge --abort
   git -C ${wt} checkout - && git -C ${wt} branch -D __dry-run-merge__
7. Set recommendation:
   - 'merge-safe' if hasConflicts=false
   - 'rebase-first' if conflicts exist but only in non-critical files (not mcp/*)
   - 'manual-resolution-required' if mcpServerConflict=true or >5 conflicting files

Return the collision report.`,
  { schema: COLLISION_REPORT, phase: 'Collision', label: `collision:${lane}` })

if (!collisions) return { status: 'collision-check-failed', lane, branch }

log(`collision check: ${collisions.recommendation} | mcp/server.py conflict: ${collisions.mcpServerConflict}`)

if (collisions.mcpServerConflict) {
  // Hard stop — mcp/server.py conflicts require manual resolution by Fable-5
  return {
    status: 'blocked:mcp-server-collision',
    lane,
    branch,
    collisions,
    message: 'mcp/server.py conflict detected — manual resolution required before merge. ' +
             'Fable-5 must resolve by coordinating with the other lane owners. ' +
             'File a V2-DEC-NNN via v2-record-decision (kind=adr) before proceeding.',
  }
}

if (collisions.recommendation === 'manual-resolution-required') {
  return {
    status: 'blocked:manual-resolution-required',
    lane,
    branch,
    collisions,
    message: `${collisions.conflictFiles.length} conflicting files require manual resolution: ${collisions.conflictFiles.join(', ')}`,
  }
}

// If rebase-first, proceed but note it in the merge commit message
const needsRebase = collisions.recommendation === 'rebase-first'

// ---- Phase 2: test green loop (optional skip for non-code branches) ---------
phase('Green')
let greenResult = { green: true, attempts: 0 }

if (!a.skipGreenLoop) {
  log(`running test green loop on ${branch} before merge`)
  // Run green loop on the lane branch (not on v2-enterprise yet)
  greenResult = await workflow('v2-test-green-loop', {
    worktree: wt,
    branch,
    maxAttempts: a.maxAttempts || 4,
  })
  log(`green loop: green=${greenResult?.green}, attempts=${greenResult?.attempts}`)

  if (!greenResult?.green) {
    return {
      status: 'blocked:tests-not-green',
      lane,
      branch,
      greenResult,
      message: `Tests not green after ${greenResult?.attempts} attempts. Fix failures before merging.`,
    }
  }
} else {
  log('skipGreenLoop=true — skipping pre-merge test run')
}

// ---- Phase 3: merge ---------------------------------------------------------
phase('Merge')
log(`merging ${branch} → v2-enterprise`)

const mergeMsg = needsRebase
  ? `merge(v2/${lane}): merge lane ${lane} branch ${branch} into v2-enterprise (rebased to resolve conflicts) [skip-review]`
  : `merge(v2/${lane}): merge lane ${lane} branch ${branch} into v2-enterprise [skip-review]`

const mergeResult = await agent(
  `Merge the lane branch into v2-enterprise in the worktree at ${wt}.

Steps:
1. ${needsRebase ? `First rebase ${branch} on v2-enterprise: git -C ${wt} checkout ${branch} && git -C ${wt} rebase v2-enterprise` : `Checkout v2-enterprise: git -C ${wt} checkout v2-enterprise`}
2. git -C ${wt} checkout v2-enterprise
3. git -C ${wt} merge --no-ff ${branch} -m "${mergeMsg}"
4. If the merge fails (exit code != 0), abort: git -C ${wt} merge --abort and return merged=false.
5. If the merge succeeds, return merged=true and the full merge commit hash from git -C ${wt} rev-parse HEAD.

Return the merge result.`,
  { schema: MERGE_RESULT, phase: 'Merge', label: `merge:${lane}` })

if (!mergeResult?.merged) {
  return {
    status: 'blocked:merge-failed',
    lane,
    branch,
    collisions,
    greenResult,
    mergeResult,
    message: 'Merge failed — likely a conflict that dry-run did not catch (e.g. rename conflict). Manual resolution required.',
  }
}

log(`merged at ${mergeResult.mergeCommit}`)

// ---- Phase 4: post-merge checks --------------------------------------------
// Run ACL leak suite on the merged v2-enterprise state — this is mandatory for
// every lane merge because ACL trimming is the core security guarantee of V2.
phase('Post-merge')
log('running ACL leak suite on v2-enterprise post-merge')

const aclResult = await agent(
  `Run the ACL leak test suite on the worktree at ${wt} (currently on v2-enterprise after the merge).

Command: cd ${wt} && python -m pytest tests/test_acl_leak.py -v --tb=short 2>&1 | tail -50

If the test file does not exist yet (early-phase merge), return passed=true with failures=[] and a note in output.
If tests exist, run them and return passed=(exit code 0), failures=[names of failing tests], output=[last 50 lines].

Do NOT fix failing tests — report only.`,
  { schema: ACL_RESULT, phase: 'Post-merge', label: `acl-leak:${lane}` })

if (aclResult && !aclResult.passed) {
  // ACL leak failure after merge — must revert
  log(`ACL leak failures detected after merge: ${aclResult.failures.join(', ')} — reverting`)
  await agent(
    `ACL leak tests failed after merging ${branch} into v2-enterprise. Revert the merge immediately.

Command: git -C ${wt} revert -m 1 HEAD --no-edit

Then verify: git -C ${wt} log --oneline -3

Return the revert commit hash.`,
    { schema: { type: 'object', required: ['reverted'], properties: { reverted: { type: 'boolean' }, revertCommit: { type: 'string' } } },
      phase: 'Post-merge', label: `revert:${lane}` })

  return {
    status: 'blocked:acl-leak-reverted',
    lane,
    branch,
    collisions,
    greenResult,
    mergeResult,
    aclResult,
    message: `ACL leak suite failed post-merge. Merge has been reverted. Fix the leaks in ${branch} before retrying.`,
  }
}

// Also run the full green loop on the merged state to confirm nothing regressed
const postMergeGreen = a.skipGreenLoop ? { green: true, attempts: 0 } : await workflow('v2-test-green-loop', {
  worktree: wt,
  branch: 'v2-enterprise',
  maxAttempts: 2,   // one pass — green loop already ran on the lane branch
})

return {
  status: postMergeGreen?.green ? 'merged-green' : 'merged-tests-failing',
  lane,
  branch,
  collisions,
  greenResult,
  mergeResult,
  aclResult,
  postMergeGreen,
  // merged-tests-failing does NOT auto-revert — tests passed on the branch but something
  // regressed in the merge. Fable-5 must diagnose. The merge commit is recorded.
  message: postMergeGreen?.green
    ? `Lane ${lane} merged successfully at ${mergeResult.mergeCommit}. All checks green.`
    : `Lane ${lane} merged but post-merge tests are failing. Merge is NOT reverted — diagnose manually.`,
}
// Caller responsibilities:
// - On 'merged-green': update BACKLOG.md to mark lane epics [x], announce to user.
// - On 'merged-tests-failing': surface to user with mergeResult.mergeCommit so they can diagnose or revert.
// - On any 'blocked:*': surface the message and collisions/failures to the user.
