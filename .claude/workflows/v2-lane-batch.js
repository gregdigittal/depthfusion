export const meta = {
  name: 'v2-lane-batch',
  description: 'Run a lane\'s ticket batch through v2-consensus-ticket in dependency waves, then the green loop',
  whenToUse: 'Execute one V2 lane phase. args: {lane, worktree, baseRef, tickets:[{ticketId, spec, workClass, dependsOn?:[]}], tiebreakModel?}',
  phases: [{ title: 'Tickets' }, { title: 'Green' }],
}

phase('Tickets')
const done = new Set()
const results = []
let remaining = [...args.tickets]

while (remaining.length) {
  const wave = remaining.filter(t => (t.dependsOn || []).every(d => done.has(d)))
  if (!wave.length) throw new Error('dependency cycle among: ' + remaining.map(t => t.ticketId).join(', '))
  log(`lane ${args.lane}: wave of ${wave.length} ticket(s): ${wave.map(t => t.ticketId).join(', ')}`)

  const waveResults = await parallel(wave.map(t => () =>
    workflow('v2-consensus-ticket', {
      ticketId: t.ticketId, spec: t.spec, workClass: t.workClass,
      worktree: args.worktree, baseRef: args.baseRef, tiebreakModel: args.tiebreakModel,
    })))

  for (let i = 0; i < wave.length; i++) {
    const r = waveResults[i] || { ticket: wave[i].ticketId, status: 'workflow-error' }
    results.push(r)
    // splits and failures do NOT unblock dependents — the lane continues with
    // whatever is still unblocked (consensus protocol: ticket halts, lane doesn't)
    if (r.status === 'approved' || r.status === 'approved-after-rebuttal') done.add(wave[i].ticketId)
    else log(`lane ${args.lane}: ${wave[i].ticketId} → ${r.status} (held for adjudication; dependents stay blocked)`)
  }
  remaining = remaining.filter(t => !wave.find(w => w.ticketId === t.ticketId))
}

phase('Green')
const green = await workflow('v2-test-green-loop', { worktree: args.worktree, maxAttempts: 3 })

const splits = results.filter(r => r.status === 'split')
const failed = results.filter(r => !['approved', 'approved-after-rebuttal', 'split'].includes(r.status))
return {
  lane: args.lane,
  approved: results.filter(r => r.status.startsWith('approved')).map(r => r.ticket),
  splits,          // → Fable-5 adjudication
  failed,          // → Fable-5 triage
  green,
}
