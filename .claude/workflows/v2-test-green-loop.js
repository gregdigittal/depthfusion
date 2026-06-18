export const meta = {
  name: 'v2-test-green-loop',
  description: 'Loop-until-green: run ruff+mypy+pytest in a worktree, dispatch Codex fixers per failure group, repeat',
  whenToUse: 'After a ticket batch lands on a lane branch. args: {worktree, maxAttempts?}',
  phases: [{ title: 'Check' }, { title: 'Fix' }],
}

const CI_RESULT = {
  type: 'object',
  required: ['failures'],
  properties: {
    failures: { type: 'array', items: { type: 'object', required: ['kind', 'detail'], properties: {
      kind: { type: 'string', enum: ['ruff', 'mypy', 'pytest'] },
      detail: { type: 'string' },   // file:line + message or test nodeid + assertion tail
    } } },
    summary: { type: 'string' },
  },
}

// args may arrive as a JSON string depending on Workflow runtime version — normalise.
const a = typeof args === 'string' ? JSON.parse(args) : (args || {})

const maxAttempts = a.maxAttempts || 4
const chunk = (arr, n) => Array.from({ length: Math.ceil(arr.length / n) }, (_, i) => arr.slice(i * n, i * n + n))

let attempt = 0
while (attempt < maxAttempts) {
  phase('Check')
  const run = await agent(
    `In ${a.worktree} run, in order: (1) ruff check src/ ; (2) mypy src/ --ignore-missing-imports ; (3) python -m pytest tests/ -q --tb=line. Parse all failures into structured output (kind + one-line detail each, max 40; if more, keep the first 40 and note the count in summary). Empty failures array if all green.`,
    { schema: CI_RESULT, phase: 'Check', label: `check#${attempt + 1}` })
  if (!run) return { green: false, escalate: true, reason: 'check agent died' }
  if (run.failures.length === 0) {
    log(`green after ${attempt} fix round(s)`)
    return { green: true, attempts: attempt }
  }

  phase('Fix')
  log(`attempt ${attempt + 1}: ${run.failures.length} failure(s) → Codex fixers`)
  await parallel(chunk(run.failures, 5).map((group, gi) => () =>
    agent(
      `Fix these CI failures in ${a.worktree}. Minimal diffs only — no architectural changes, no test deletions, no assertion weakening. Failures:
${group.map(f => `[${f.kind}] ${f.detail}`).join('\n')}
Verify each fix by re-running the specific check. Commit "fix(v2): green-loop round [skip-review]" (amend-style separate commit is fine).`,
      { agentType: 'codex:codex-rescue', phase: 'Fix', label: `fix#${attempt + 1}.${gi}` })))
  attempt++
}

return { green: false, escalate: true, attempts: attempt }
// escalate:true → Fable-5 triage; per orchestration plan §8 the rebuttal
// escalates to Opus on repeated same-root-cause failures, not the whole lane.
