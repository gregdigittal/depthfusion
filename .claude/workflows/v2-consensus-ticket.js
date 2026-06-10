export const meta = {
  name: 'v2-consensus-ticket',
  description: 'Dev → review → rebuttal → tiebreak pipeline for one V2 ticket (orchestration plan §4.1)',
  whenToUse: 'Execute a single V2 ticket through the consensus protocol. args: {ticketId, spec, workClass, worktree, baseRef, tiebreakModel?}',
  phases: [
    { title: 'Dev', detail: 'implement the ticket' },
    { title: 'Review', detail: 'independent model-family review' },
    { title: 'Rebut', detail: 'one fix-or-defend round' },
    { title: 'Verdict', detail: 'tiebreak on persistent split' },
  ],
}

// ---- routing (mirrors docs/plans/v2-build-orchestration.md §2) ----
const ROUTING = {
  'security-critical': { dev: 'opus', reviewers: ['deepseek', 'gemini'] },
  'core-backend': { dev: 'codex', reviewers: ['deepseek'] },
  'frontend': { dev: 'codex', reviewers: ['gemini'] },
  'rust-core': { dev: 'codex', reviewers: ['deepseek', 'gemini'] },
  'tests-docs': { dev: 'haiku', reviewers: ['codex-spot'] },
}

const IMPL_RESULT = {
  type: 'object',
  required: ['summary', 'filesTouched', 'testsPassed', 'commit'],
  properties: {
    summary: { type: 'string' },
    filesTouched: { type: 'array', items: { type: 'string' } },
    testsPassed: { type: 'boolean' },
    testEvidence: { type: 'string' },
    commit: { type: 'string' },
  },
}

const REVIEW_VERDICT = {
  type: 'object',
  required: ['reviewer', 'verdict', 'findings'],
  properties: {
    reviewer: { type: 'string' },
    verdict: { type: 'string', enum: ['approve', 'object'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'claim'],
        properties: {
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          file: { type: 'string' },
          line: { type: 'number' },
          claim: { type: 'string' },
          fix: { type: 'string' },
        },
      },
    },
  },
}

const ADVISORY = {
  type: 'object',
  required: ['lean', 'reasoning'],
  properties: {
    lean: { type: 'string', enum: ['dev', 'reviewers', 'mixed'] },
    reasoning: { type: 'string' },
    perFinding: { type: 'array', items: { type: 'object', properties: {
      claim: { type: 'string' }, valid: { type: 'boolean' }, note: { type: 'string' } } } },
  },
}

function devOpts(dev, phase) {
  if (dev === 'codex') return { agentType: 'codex:codex-rescue', phase }
  if (dev === 'opus') return { model: 'opus', phase }
  if (dev === 'haiku') return { model: 'haiku', phase }
  return { phase }
}

function devPrompt(a) {
  return `Implement V2 ticket ${a.ticketId} in the git worktree at ${a.worktree}. Work ONLY in that worktree.

SPEC:
${a.spec}

CONVENTIONS: Python 3.11+, ruff (line-length 100), mypy, pytest under tests/. Match surrounding code style. Run the relevant test package (pytest -x -q) before declaring done.

COMMIT one conventional commit on the current branch with "[skip-review]" in the subject and footer "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>". Do NOT push.

Return structured output: summary (<=10 lines), filesTouched, testsPassed, testEvidence (command + tail), commit hash.`
}

function reviewerPrompt(reviewer, a, impl) {
  if (reviewer === 'codex-spot') {
    return `Spot-review ticket ${a.ticketId} via Codex. In worktree ${a.worktree}, review the diff ${a.baseRef}..HEAD against the ticket spec below. Focus: correctness, conventions, test adequacy.
SPEC:\n${a.spec}\nDev summary: ${impl.summary}
Return structured output: reviewer="codex-spot", verdict approve|object, findings[{severity,file,line,claim,fix}]. Only object on real defects (medium+).`
  }
  return `You are the ${reviewer} review runner for ticket ${a.ticketId}. In the worktree ${a.worktree}:
1. Run: bash scripts/v2/review-${reviewer}.sh ${a.baseRef}..HEAD --checklist "correctness,security,conventions,test coverage. Ticket spec: ${a.ticketId}"
   (If scripts/v2 is absent in this worktree, locate it on the v2-enterprise branch: git show v2-enterprise:scripts/v2/review-${reviewer}.sh > /tmp/r.sh && bash /tmp/r.sh ...)
2. The script prints a JSON verdict. Relay it as structured output with reviewer="${reviewer}". Do not soften or editorialize the external reviewer's findings.
3. If the script fails entirely, return verdict="object" with one finding severity=high claim="reviewer runtime failure: <error>".`
}

// ---- pipeline ----
const route = ROUTING[args.workClass]
if (!route) throw new Error(`unknown workClass: ${args.workClass}`)

phase('Dev')
log(`${args.ticketId}: dev=${route.dev}, reviewers=${route.reviewers.join('+')}`)
const impl = await agent(devPrompt(args), { ...devOpts(route.dev, 'Dev'), schema: IMPL_RESULT, label: `dev:${args.ticketId}` })
if (!impl) return { ticket: args.ticketId, status: 'dev-failed' }
if (!impl.testsPassed) return { ticket: args.ticketId, status: 'dev-tests-red', impl }

phase('Review')
const reviews = (await parallel(route.reviewers.map(r => () =>
  agent(reviewerPrompt(r, args, impl), { schema: REVIEW_VERDICT, phase: 'Review', label: `rev:${r}:${args.ticketId}` })
))).filter(Boolean)
const objections = reviews.flatMap(r => r.verdict === 'object' ? r.findings.filter(f => f.severity !== 'low') : [])
if (!objections.length) return { ticket: args.ticketId, status: 'approved', impl, reviews }

phase('Rebut')
log(`${args.ticketId}: ${objections.length} objection(s) — rebuttal round`)
const rebut = await agent(
  `Rebuttal round for ticket ${args.ticketId} in worktree ${args.worktree}. Independent reviewers raised these findings:
${JSON.stringify(objections, null, 2)}
For each: FIX it (amend with a new [skip-review] commit) or DEFEND it (precise technical justification, citing code). Return structured output: same shape as before plus defended:[claims you defend with reasons].`,
  { ...devOpts(route.dev, 'Rebut'), schema: { ...IMPL_RESULT, required: ['summary', 'filesTouched', 'testsPassed', 'commit'] }, label: `rebut:${args.ticketId}` })
if (!rebut) return { ticket: args.ticketId, status: 'split', positions: { impl, reviews } }

const reReviews = (await parallel(route.reviewers.map(r => () =>
  agent(reviewerPrompt(r, args, rebut) + '\nThis is a RE-REVIEW after a rebuttal. Concede fixed/validly-defended findings; maintain only what is still wrong.',
    { schema: REVIEW_VERDICT, phase: 'Rebut', label: `rerev:${r}:${args.ticketId}` })
))).filter(Boolean)
const stillObjecting = reReviews.filter(r => r.verdict === 'object')
if (!stillObjecting.length) return { ticket: args.ticketId, status: 'approved-after-rebuttal', impl: rebut, reviews: reReviews }

phase('Verdict')
const advisory = await agent(
  `Tiebreak advisory for ticket ${args.ticketId}. Use ToolSearch to load mcp__depthfusion__depthfusion_bridge, then call it with model="${args.tiebreakModel || 'openai/gpt-4o'}" and a prompt containing BOTH positions below. Relay the external model's judgment as structured output (lean: dev|reviewers|mixed).
DEV position: ${JSON.stringify({ summary: rebut.summary, defended: rebut.defended || [] })}
REVIEWER position: ${JSON.stringify(stillObjecting.flatMap(r => r.findings))}`,
  { schema: ADVISORY, phase: 'Verdict', label: `tiebreak:${args.ticketId}` })

return {
  ticket: args.ticketId,
  status: 'split',
  positions: { impl: rebut, reviews: stillObjecting, advisory },
}
// status:'split' → Fable-5 adjudicates in the main loop (escalation ladder step 4).
