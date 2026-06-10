export const meta = {
  name: 'v2-leak-hunt',
  description: 'Loop-until-dry adversarial sweep: hunt ACL leaks across every data-returning path (REST + 29 MCP tools + fabric SSE + aggregates)',
  whenToUse: 'Merge gate for E-51 and Phase 4 hardening (D-2 mitigation). args: {worktree, apiBase?, principals:{owner, outsider}, maxDryRounds?}',
  phases: [{ title: 'Find' }, { title: 'Verify' }],
}

const FINDINGS = {
  type: 'object',
  required: ['findings'],
  properties: { findings: { type: 'array', items: { type: 'object',
    required: ['path', 'surface', 'claim', 'repro'],
    properties: {
      path: { type: 'string' },      // route, tool name, or stream
      surface: { type: 'string', enum: ['rest', 'mcp', 'sse', 'aggregate', 'cache', 'sync'] },
      claim: { type: 'string' },     // what leaked and to whom
      repro: { type: 'string' },     // exact command/tool-call that demonstrates it
      severity: { type: 'string', enum: ['critical', 'high', 'medium'] },
    } } } },
}

const VERDICT = {
  type: 'object',
  required: ['real'],
  properties: { real: { type: 'boolean' }, note: { type: 'string' } },
}

// Finder lenses — each blind to the others. The deepseek/gemini lenses drive the
// external CLIs via scripts/v2/review-*.sh for code-level analysis; the probe
// lenses execute live requests as the outsider principal.
const FINDERS = [
  { key: 'code-deepseek', prompt: w => `Static ACL-leak hunt via DeepSeek. In ${w.worktree}, run the deepseek review wrapper over src/depthfusion/retrieval/ src/depthfusion/api/ src/depthfusion/mcp/ with checklist "paths that return record content without consulting the principal's ACL (pre-rank filter at retrieval/hybrid.py + post-rank PolicyEngine re-verify are BOTH required)". Convert its output to findings.` },
  { key: 'code-gemini', prompt: w => `Whole-module ACL audit via Gemini (long context). In ${w.worktree}, feed gemini the FULL api/ + mcp/ modules and ask for every code path that can return memory/discovery/graph/document content, flagging any that lack BOTH ACL layers. Convert to findings.` },
  { key: 'probe-rest', prompt: w => `Live REST probe. Against ${w.apiBase || 'the worktree test server (start it)'}: authenticate as the OUTSIDER principal (${w.principals?.outsider || 'test-outsider'}) and attempt to retrieve content owned by ${w.principals?.owner || 'owner'} via every /query/* and /v1/* route, including aggregate count-leakage (facet/count endpoints revealing hidden-record existence). Findings = any non-empty unauthorized result.` },
  { key: 'probe-mcp', prompt: w => `MCP tool probe. Enumerate ALL tools in src/depthfusion/mcp/server.py TOOLS dict (29 expected). As the outsider principal, invoke each content-returning tool (recall, retrieve_context, graph_traverse, session_seed, query_telemetry, bridge, ...) targeting owner-only records. Findings = any tool returning unauthorized content or metadata that proves a hidden record exists.` },
  { key: 'probe-sse', prompt: w => `Fabric/SSE probe. Subscribe to /v1/events/stream and /v1/events/seed as the outsider while owner-scoped events publish. Findings = any event payload or seed bundle item the outsider's ACL does not allow.` },
]

const maxDry = args.maxDryRounds || 2
const seen = new Set(), confirmed = []
const key = f => `${f.surface}:${f.path}:${f.claim.slice(0, 80)}`
let dry = 0, round = 0

while (dry < maxDry) {
  round++
  phase('Find')
  log(`leak-hunt round ${round} (dry streak ${dry}/${maxDry}, confirmed so far: ${confirmed.length})`)
  const found = (await parallel(FINDERS.map(f => () =>
    agent(f.prompt(args) + '\nReturn structured findings only — no prose. Empty array if nothing found.',
      { schema: FINDINGS, phase: 'Find', label: `find:${f.key}#${round}` }))))
    .filter(Boolean).flatMap(r => r.findings)

  const fresh = found.filter(f => !seen.has(key(f)))
  if (!fresh.length) { dry++; continue }
  dry = 0
  fresh.forEach(f => seen.add(key(f)))

  phase('Verify')
  const judged = await parallel(fresh.map(f => () =>
    parallel([
      `Reproduce lens: execute the repro EXACTLY (${f.repro}) in ${args.worktree}. Does unauthorized content actually come back?`,
      `Authorization lens: is the returned data genuinely outside the outsider's ACL, or is it public/own-record/metadata-only-by-design?`,
      `Exploitability lens: can a real enrolled principal (not a test artifact) reach this path through the documented API surface?`,
    ].map(lens => () =>
      agent(`Adversarially judge this leak claim. Default real=false unless evidence is conclusive.\nCLAIM: ${JSON.stringify(f)}\nLENS: ${lens}`,
        { schema: VERDICT, phase: 'Verify', label: `verify:${f.surface}:${f.path}`.slice(0, 60) })))
      .then(vs => ({ f, real: vs.filter(Boolean).filter(v => v.real).length >= 2 }))))
  confirmed.push(...judged.filter(j => j.real).map(j => j.f))
}

log(`leak-hunt dry after ${round} rounds: ${confirmed.length} confirmed leak(s)`)
return { rounds: round, confirmed, candidatesSeen: seen.size }
// confirmed.length > 0 → merge BLOCKED; Opus 4.8 fixes (routing table: pen-test row)
