export const meta = {
  name: 'v2-gate-review',
  description: 'Phase-gate evidence panel: reads the gate file, verifies every criterion via evidence (not assertion), completeness critic closes',
  whenToUse: 'G0–G4 gate checks. args: {gate, worktree, criteria?} — criteria may be omitted (workflow reads docs/plans/${gate}-gate.md) or passed as string IDs ["C1","C2",...] or full objects [{id, description, evidenceCmd?}]',
  phases: [{ title: 'Parse' }, { title: 'Verify' }, { title: 'Critic' }],
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

const PARSED_CRITERIA = {
  type: 'object',
  required: ['criteria'],
  properties: {
    criteria: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'description'],
        properties: {
          id: { type: 'string' },
          description: { type: 'string' },
          checklist: { type: 'array', items: { type: 'string' } },
          evidenceSlots: { type: 'array', items: { type: 'string' } },  // filled or blank
          evidenceFilled: { type: 'boolean' },  // true if evidence slots contain non-blank values
        },
      },
    },
    gateStatus: { type: 'string' },  // PASSED / PENDING extracted from gate file header
  },
}

// args may arrive as a JSON string depending on Workflow runtime version — normalise.
const a = typeof args === 'string' ? JSON.parse(args) : (args || {})

// --- Phase 1: resolve criteria -------------------------------------------------
// Three input modes:
//   A. criteria omitted or empty → read gate file and extract criteria
//   B. criteria is string[]       → read gate file for descriptions of those IDs
//   C. criteria is object[]       → use as-is (backward compat)
phase('Parse')
let resolvedCriteria  // [{id, description, checklist?, evidenceSlots?, evidenceFilled?}]

const rawCriteria = a.criteria
const needsGateFile = !rawCriteria || rawCriteria.length === 0 || typeof rawCriteria[0] === 'string'

if (needsGateFile) {
  const gateFilePath = `${a.worktree}/docs/plans/${a.gate}-gate.md`
  log(`reading gate file: ${gateFilePath}`)
  const parsed = await agent(
    `Read the gate definition file at ${gateFilePath}.
Extract every criterion section (headings matching "### C\\d+" or "### C\\d+[ab]?").
For each criterion:
  - id: the C-label (e.g. "C1", "C4a")
  - description: the heading text after the label (e.g. "OIDC login against Entra ID test tenant")
  - checklist: the bullet items (- [ ] or - [x])
  - evidenceSlots: the lines in the Evidence code block (the "Key: value" lines)
  - evidenceFilled: true if every evidence slot has been replaced (no "___" remaining)
Also extract the gate status from the Status line in the header.
Return all criteria found and the gateStatus string.`,
    { schema: PARSED_CRITERIA, phase: 'Parse', label: `parse:${a.gate}` })

  if (!parsed) throw new Error(`failed to parse gate file for ${a.gate}`)

  // If caller passed specific IDs, filter to those; otherwise use all
  const filter = rawCriteria && rawCriteria.length > 0 ? new Set(rawCriteria) : null
  resolvedCriteria = filter
    ? parsed.criteria.filter(c => filter.has(c.id))
    : parsed.criteria

  log(`${a.gate}: found ${resolvedCriteria.length} criteria, gate status: ${parsed.gateStatus}`)
  if (parsed.gateStatus && parsed.gateStatus.includes('PASSED')) {
    log(`${a.gate} gate file already records PASSED — re-verifying for confirmation only`)
  }
} else {
  // Mode C: full objects passed in — use directly
  resolvedCriteria = rawCriteria
}

// --- Phase 2: verify each criterion -------------------------------------------
phase('Verify')
const rows = (await parallel(resolvedCriteria.map(c => () =>
  agent(
    `Verify gate criterion ${c.id} for gate ${a.gate}.
Working directory / worktree: ${a.worktree}

CRITERION: ${c.description}
${c.checklist ? `CHECKLIST:\n${c.checklist.map(item => '  ' + item).join('\n')}` : ''}
${c.evidenceSlots ? `EVIDENCE SLOTS (filled values in the gate file):\n${c.evidenceSlots.map(s => '  ' + s).join('\n')}` : ''}
${c.evidenceFilled === false ? 'WARNING: evidence slots contain blanks (___). Criterion cannot be met until evidence is recorded.' : ''}

RULES:
- met=true requires EXECUTED verification: run a command, read a file, query git, or confirm the evidence slot value matches reality.
- An assertion without execution is met=false with caveats="unverifiable".
- Blank evidence slots (___) = met=false automatically.
- Do NOT fix anything — verify only.
- evidence field: paste the command you ran and the key output tail (max 5 lines), or the commit hash you confirmed.`,
    { schema: EVIDENCE, phase: 'Verify', label: `gate:${c.id}` })
))).filter(Boolean)

// --- Phase 3: completeness critic ---------------------------------------------
phase('Critic')
const critic = await agent(
  `Completeness critic for gate ${a.gate}. Evidence table:
${JSON.stringify(rows, null, 2)}
All criteria: ${resolvedCriteria.map(c => c.id + ': ' + c.description).join(' | ')}
What is MISSING? Flag: unverified claims, blank evidence slots, gate-relevant risks no criterion covers, criteria with evidence that is present but weak (assertion only, no command output or commit hash). Be adversarial — this is a security/correctness gate.`,
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
  assessment: critic?.assessment,
}
// verdict:'PASS' → Fable-5 declares the gate, records via depthfusion_record_decision,
// updates the gate file Status to [x] PASSED, and per EXEC-1 presents as HARD STOP.
// verdict:'HOLD' → Fable-5 surfaces unmet[] and gaps[] to Greg before proceeding.
