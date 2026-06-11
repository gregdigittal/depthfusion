export const meta = {
  name: 'v2-record-decision',
  description: 'Write a V2-DEC-NNN decision file (split adjudication, gate-pass, ADR) and optionally update the gate file status',
  whenToUse: 'After Fable-5 adjudicates a split, declares a gate, or ratifies a business decision. args: {kind, worktree, ...kind-specific fields}',
  phases: [{ title: 'Draft' }, { title: 'Write' }],
}

// ---- schemas ----
const DECISION_DRAFT = {
  type: 'object',
  required: ['decisionId', 'title', 'body', 'filePath'],
  properties: {
    decisionId: { type: 'string' },   // V2-DEC-NNN or G{n}-PASS
    title: { type: 'string' },
    body: { type: 'string' },         // full markdown body
    filePath: { type: 'string' },     // absolute path to write
    laneHalt: { type: 'boolean' },
    laneHaltPaths: { type: 'array', items: { type: 'string' } },
  },
}

const NEXT_ID_RESULT = {
  type: 'object',
  required: ['nextId', 'nextIdPadded'],
  properties: {
    nextId: { type: 'number' },
    nextIdPadded: { type: 'string' },  // e.g. "003"
  },
}

// ---- helpers ----
// args may arrive as a JSON string depending on Workflow runtime version — normalise.
const a = typeof args === 'string' ? JSON.parse(args) : (args || {})

if (!a.kind) throw new Error('args.kind is required: split | gate-pass | gate-fail | adr')
if (!a.worktree) throw new Error('args.worktree is required')

// ---- Phase 1: draft the decision document ----
phase('Draft')

// Find the next V2-DEC-NNN ID by scanning existing files
const nextId = await agent(
  `In ${a.worktree}/docs/decisions/, list all files matching V2-DEC-*.md. Find the highest NNN suffix (zero-padded to 3 digits). Return nextId (integer, max found + 1, starting at 1 if none) and nextIdPadded (zero-padded to 3 digits, e.g. "001").`,
  { schema: NEXT_ID_RESULT, phase: 'Draft', label: 'next-id' })
if (!nextId) throw new Error('could not determine next V2-DEC ID')

const decId = `V2-DEC-${nextId.nextIdPadded}`
// Date must be passed via args.date — new Date() throws in the Workflow runtime.
// Caller can pass date:"2026-06-10"; if absent, agents will resolve it via `date` shell command.
const today = a.date || 'DATE_NOT_PROVIDED'

let draftPrompt
if (a.kind === 'split') {
  // A consensus split that survived escalation — Fable-5 has adjudicated
  draftPrompt = `Write a V2-DEC-NNN decision file for a split verdict that Fable-5 has adjudicated.

Decision ID: ${decId}
Ticket: ${a.ticketId || 'unknown'}
Date: ${today}
Fable-5 adjudication: ${a.adjudication || '(fill in)'}
Dev position summary: ${JSON.stringify(a.devPosition || {}, null, 2)}
Reviewer positions: ${JSON.stringify(a.reviewerPositions || [], null, 2)}
Tiebreak advisory: ${JSON.stringify(a.advisory || {}, null, 2)}

Write a markdown file in this format:
  # ${decId} — [short title derived from the ticket/finding]
  **Decision ID:** ${decId}
  **Ticket:** [ticketId]
  **Status:** RESOLVED | SPLIT-ONGOING | DEFERRED
  **Date:** ${today}
  **Fable-5 adjudication:** [the actual decision: what was resolved, what was deferred]
  ---
  ## What split
  [table of findings: ID | File | Severity | Claim]
  ## Positions
  ### Dev
  [summary]
  ### Reviewers
  [summary per reviewer]
  ## Tiebreak advisory
  [advisory lean and reasoning]
  ## Resolution
  [what happens next: implement fix / accept-with-note / defer to which story]
  ## Lane halt
  [if applicable: which file paths are halted until resolved: true]

Set laneHalt=true if any finding has a lane-halt implication (security-critical file touched, unresolved MEDIUM+ DoS or auth bypass).
File path: ${a.worktree}/docs/decisions/${decId}.md`

} else if (a.kind === 'gate-pass') {
  // Gate passed — record the declaration
  draftPrompt = `Write a gate-pass declaration file.

Decision ID: ${decId}
Gate: ${a.gate || 'unknown'}
Date: ${today}
Evidence summary: ${JSON.stringify(a.evidence || [], null, 2)}
Gaps noted: ${JSON.stringify(a.gaps || [], null, 2)}

Write a markdown file:
  # ${decId} — Gate ${a.gate} PASSED
  **Decision ID:** ${decId}
  **Gate:** ${a.gate}
  **Status:** PASSED
  **Date:** ${today}
  **Declared by:** Fable-5
  ---
  ## Evidence summary
  [one line per criterion: C1: [what was verified]]
  ## Gaps noted (accepted)
  [any gaps the critic flagged that were accepted rather than blocking]
  ## Phase unlocked
  [which epics/lanes are now unblocked]

File path: ${a.worktree}/docs/decisions/${decId}.md`

} else if (a.kind === 'gate-fail') {
  draftPrompt = `Write a gate-fail record.

Decision ID: ${decId}
Gate: ${a.gate || 'unknown'}
Date: ${today}
Unmet criteria: ${JSON.stringify(a.unmet || [], null, 2)}
Gaps: ${JSON.stringify(a.gaps || [], null, 2)}

Write a markdown file:
  # ${decId} — Gate ${a.gate} HOLD
  **Decision ID:** ${decId}
  **Gate:** ${a.gate}
  **Status:** HOLD
  **Date:** ${today}
  ---
  ## Blocking criteria
  [list each unmet criterion with what evidence is missing]
  ## Required remediation
  [tasks to file, assigned to which lane/story]
  ## Re-check procedure
  Run v2-gate-review for ${a.gate} after remediation — only unmet criteria need re-verification.

File path: ${a.worktree}/docs/decisions/${decId}.md`

} else if (a.kind === 'adr') {
  // Generic architectural decision record
  draftPrompt = `Write an architectural decision record.

Decision ID: ${decId}
Title: ${a.title || 'Architectural Decision'}
Date: ${today}
Context: ${a.context || '(provide context)'}
Options considered: ${JSON.stringify(a.options || [], null, 2)}
Decision: ${a.decision || '(provide decision)'}
Rationale: ${a.rationale || '(provide rationale)'}
Consequences: ${a.consequences || '(provide consequences)'}

Write a standard ADR in markdown:
  # ${decId} — ${a.title || 'Decision'}
  **Decision ID:** ${decId}
  **Status:** ACCEPTED
  **Date:** ${today}
  ---
  ## Context
  ## Options Considered
  ## Decision
  ## Rationale
  ## Consequences / Follow-ups

File path: ${a.worktree}/docs/decisions/${decId}.md`
}

const draft = await agent(draftPrompt, { schema: DECISION_DRAFT, phase: 'Draft', label: `draft:${decId}` })
if (!draft) return { status: 'draft-failed', decisionId: decId }

// ---- Phase 2: write the file (and optionally update the gate file) ----
phase('Write')
const written = await agent(
  `Write the following content to ${draft.filePath}. Create any missing parent directories.
Content:
${draft.body}

${a.kind === 'gate-pass' && a.gate ? `
Also update the gate file at ${a.worktree}/docs/plans/${a.gate}-gate.md:
- Change the Status line from \`[ ] PENDING\` to \`[x] PASSED\`
- Fill in the Declared by field with "Fable-5"
- Fill in the Date field with "${today}"
- Fill in the Workflow run field with "${a.workflowRunId || '(fill in)'}"
` : ''}

After writing, run: git -C ${a.worktree} add docs/decisions/${decId}.md ${a.kind === 'gate-pass' && a.gate ? `docs/plans/${a.gate}-gate.md` : ''} && git -C ${a.worktree} commit -m "docs(v2): record ${decId} — ${draft.title} [skip-review]"

Return the commit hash.`,
  { schema: { type: 'object', required: ['commit'], properties: { commit: { type: 'string' } } },
    phase: 'Write', label: `write:${decId}` })

return {
  status: 'written',
  decisionId: decId,
  title: draft.title,
  filePath: draft.filePath,
  laneHalt: draft.laneHalt || false,
  laneHaltPaths: draft.laneHaltPaths || [],
  commit: written?.commit || 'unknown',
}
// laneHalt:true → Fable-5 must announce the halt in the active output sink before
// dispatching any further tickets that touch laneHaltPaths.
