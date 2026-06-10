export const meta = {
  name: 'v2-gate-review',
  description: 'Phase-gate evidence panel: every criterion verified by an agent that must produce evidence, not assertion; completeness critic closes',
  whenToUse: 'G0–G4 gate checks. args: {gate, worktree, criteria:[{id, description, evidenceCmd?}]}',
  phases: [{ title: 'Verify' }, { title: 'Critic' }],
}

const EVIDENCE = {
  type: 'object',
  required: ['criterion', 'met', 'evidence'],
  properties: {
    criterion: { type: 'string' },
    met: { type: 'boolean' },
    evidence: { type: 'string' },   // command + output tail, commit hash, test name — something checkable
    caveats: { type: 'string' },
  },
}

// args may arrive as a JSON string depending on Workflow runtime version — normalise.
const a = typeof args === 'string' ? JSON.parse(args) : (args || {})

phase('Verify')
const rows = (await parallel(a.criteria.map(c => () =>
  agent(
    `Verify gate criterion ${c.id} for gate ${a.gate} in ${a.worktree}.
CRITERION: ${c.description}
${c.evidenceCmd ? `Suggested evidence command: ${c.evidenceCmd}` : ''}
Rules: met=true requires EXECUTED evidence (run the command, cite the output tail / commit hash / passing test id). An assertion without execution is met=false with caveats="unverifiable". Do not fix anything — verify only.`,
    { schema: EVIDENCE, phase: 'Verify', label: `gate:${c.id}` })
))).filter(Boolean)

phase('Critic')
const critic = await agent(
  `Completeness critic for gate ${a.gate}. Evidence table:
${JSON.stringify(rows, null, 2)}
Criteria list: ${a.criteria.map(c => c.id + ': ' + c.description).join(' | ')}
What is missing? Unverified claims, criteria with weak evidence, gate-relevant risks nobody checked. Return structured output.`,
  { schema: { type: 'object', required: ['gaps'], properties: {
    gaps: { type: 'array', items: { type: 'string' } },
    assessment: { type: 'string' } } }, phase: 'Critic', label: 'critic' })

const unmet = rows.filter(r => !r.met)
return {
  gate: a.gate,
  verdict: unmet.length === 0 && (critic?.gaps || []).length === 0 ? 'PASS' : 'HOLD',
  evidence: rows,
  unmet: unmet.map(r => r.criterion),
  gaps: critic?.gaps || [],
}
// Fable-5 reads this table, declares the gate, records via depthfusion_record_decision,
// and per EXEC-1 presents it to Greg as a HARD STOP before the next phase.
