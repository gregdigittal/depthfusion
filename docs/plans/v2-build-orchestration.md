# DepthFusion V2 — Build Orchestration Plan

> Companion to `docs/plans/depthfusion-v2-plan.html` (the WHAT). This document is the HOW:
> model routing, workflow scripts, loop patterns, and delegation mechanics for executing
> E-48 → E-63 with minimal frontier-token spend.
>
> **Date:** 2026-06-10 · **Baseline:** v1.2.2 (E-48 / S-152 / T-532) · **Branch:** `v2-enterprise`

---

## 1. Execution Stack (verified on this machine)

| Runtime | Version | Invocation | Role |
|---|---|---|---|
| **Fable-5** (this session) | claude-fable-5 | main loop | Mediator, planner, chief architect. Decisions, gate reviews, consensus adjudication, workflow authoring. **Writes no production code.** |
| **Codex 5.5** | codex-cli 0.130.0 | `codex exec --full-auto` in lane worktree; `codex:codex-rescue` agentType in Workflow scripts; `/codex review` | **Primary dev model** — all implementation unless security-critical |
| **Opus 4.8** | via Agent/Workflow `model: "opus"` | Workflow `agent(..., {model:"opus"})` | Security-critical dev (auth, ACL filters, export gating, crypto, Rust token vault) + Critical-finding triage |
| **DeepSeek** | `deepseek` CLI (~/.cargo/bin) | Bash from workflow agents | Review lane 1 — backend/correctness (different model family = independent perspective) |
| **Gemini** | gemini-cli 0.42.0 | Bash from workflow agents | Review lane 2 — long-context whole-module audits, frontend review |
| **OpenRouter** | key set; also `depthfusion_bridge` MCP tool | curl / bridge tool | Tiebreak third opinion + burst capacity. Never decides alone. |
| **Haiku 4.5** | Agent/Workflow `model: "haiku"` | `agent(..., {model:"haiku"})` | Scaffolds, fixtures, docstrings, runbooks, test boilerplate |
| **Ollama** | 0.21.0 local | Bash | Zero-cost lint sweeps, format fixes, commit-message drafts |

**Token-economy invariant:** Fable-5's context is the scarcest resource. It receives
*structured verdicts* (JSON summaries, diffs of disagreements, gate checklists) — never
raw diffs, never full file contents, never test logs. Workflow subagents compress before
returning.

---

## 2. Routing Policy (supersedes §2.1 of the HTML plan)

| Work class | Dev | Review | Tiebreak | Rationale |
|---|---|---|---|---|
| Security-critical (E-49/50/51 auth, ACL filters, E-59 export gating, Rust vault) | **Opus 4.8** | DeepSeek + Gemini (dual) | OpenRouter | Highest-stakes code gets strongest dev + two independent families |
| Core backend (stores, parsers, sync engine, connectors, MCP tools) | **Codex 5.5** | DeepSeek | OpenRouter | Codex is a different family from both reviewer and mediator — natural independence |
| Frontend (React/TS, Tauri webview) | **Codex 5.5** | Gemini | OpenRouter | Gemini's context window reviews whole component trees in one pass |
| Rust core (non-crypto: IPC, cache schema, viewers) | **Codex 5.5** | DeepSeek + Gemini | Opus 4.8 | Rust mistakes are subtle; dual review, frontier tiebreak |
| Tests, fixtures, scaffolds, docs, runbooks | **Haiku 4.5** or Codex 5.5 | Codex 5.5 spot-check (1-in-3) | — | Cheap volume work |
| Lint/format/mechanical sweeps | **Ollama** | CI gates | — | Zero cost |
| Whole-module audits (>50K tokens) | — | **Gemini** | — | Only runtime with the window |
| Consensus adjudication, gates, replanning | **Fable-5** | — | user | The mediator never also develops |
| Adversarial pen-test (S-199) | **DeepSeek + Gemini** attack | Opus 4.8 fixes | Fable-5 triage | Attackers must not have built the defenses |

**Escalation ladder for any disputed finding:**

```
1. Dev model + review model disagree
2. ONE automated rebuttal round (dev fixes or defends, in the workflow)
3. Still split → OpenRouter advisory (3rd family)
4. Still no 2-of-3 → Fable-5 adjudicates with the compressed positions
5. Fable-5 judges it a business/architecture call → DECISION ticket
   docs/decisions/V2-DEC-NNN.md → surfaced to Greg. Ticket halts; lane continues.
```

Steps 1–3 happen *inside* a Workflow script with no Fable-5 tokens spent. Fable-5 only
sees splits that survive three independent opinions.

---

## 3. Invocation Mechanics

### 3.1 Codex 5.5 (primary dev)

Two paths, by context:

```bash
# Path A — inside a Workflow script (preferred: tracked, resumable, parallel-capped)
agent(ticketPrompt, { agentType: "codex:codex-rescue", phase: "Dev", schema: TICKET_RESULT })

# Path B — direct lane work from the main session (single big tickets)
cd .claude/worktrees/v2-lane-a-authz && codex exec --full-auto "<ticket spec>"
```

Ticket prompts to Codex always contain: ticket ID, AC list verbatim from the plan,
file paths with line anchors (from §6 integration map), the project conventions
(ruff line-length 100, mypy, pytest layout), and the instruction to run
`pytest tests/<package>/ -x` before declaring done.

### 3.2 DeepSeek / Gemini reviewers (from workflow agents via Bash)

```bash
# DeepSeek review of a diff
git -C <worktree> diff <base>..HEAD | deepseek review --checklist correctness,security,conventions

# Gemini whole-module audit
gemini -p "Review these files against this checklist: ..." --include src/depthfusion/identity/
```

Each reviewer returns a structured verdict the workflow parses:
`{verdict: approve|object, findings: [{severity, file, line, claim, fix}]}`.
Wrapper scripts in `scripts/v2/` normalise CLI output to this JSON (T-537 equivalent).

### 3.3 OpenRouter tiebreak

```
mcp__depthfusion__depthfusion_bridge(model="<openrouter-model>", prompt="<both positions + diff excerpt>")
```

Using the bridge (not raw curl) gives us shared-memory storage of every tiebreak for
free — each adjudication becomes a recallable memory tagged `provider:{model}`.

### 3.4 Opus 4.8 (security dev + triage)

Workflow `agent(prompt, {model: "opus", phase: "Dev-Sec"})`. Budget-gated: any
Opus dispatch outside work-class=security requires a justification line in the
workflow `log()` — this is the cost guardrail from S-154 AC-3, enforced socially
in the script rather than by tooling.

---

## 4. Workflow Patterns (the reusable machinery)

Five scripts, written once in Phase 0, stored in `.claude/workflows/`, parameterised
by `args`. These replace ~80% of manual orchestration.

### 4.1 `v2-consensus-ticket` — the unit of work (S-155 codified)

Runs one ticket through dev → review → rebut → tiebreak. The atom every lane executes.

```js
export const meta = {
  name: 'v2-consensus-ticket',
  description: 'Dev→review→rebut→tiebreak pipeline for one V2 ticket',
  phases: [{ title: 'Dev' }, { title: 'Review' }, { title: 'Rebut' }, { title: 'Verdict' }],
}
// args: { ticketId, spec, workClass, worktree, devModel, reviewers }
phase('Dev')
const impl = await agent(buildTicketPrompt(args), {
  agentType: args.devModel === 'codex' ? 'codex:codex-rescue' : undefined,
  model: args.devModel === 'opus' ? 'opus' : undefined,
  schema: IMPL_RESULT,   // { filesTouched, testsPassed, summary, diffRef }
})
phase('Review')
const reviews = await parallel(args.reviewers.map(r => () =>
  agent(reviewPrompt(r, impl), { schema: REVIEW_VERDICT, phase: 'Review' })))
const objections = reviews.filter(Boolean).flatMap(r => r.findings.filter(f => f.severity !== 'low'))
if (!objections.length) return { ticket: args.ticketId, status: 'approved', impl }
phase('Rebut')
const rebut = await agent(rebutPrompt(impl, objections), {
  agentType: 'codex:codex-rescue', schema: REBUT_RESULT })   // fix or written defense
const reReview = await parallel(args.reviewers.map(r => () =>
  agent(reReviewPrompt(r, rebut), { schema: REVIEW_VERDICT, phase: 'Verdict' })))
const stillSplit = reReview.filter(Boolean).some(r => r.verdict === 'object')
if (!stillSplit) return { ticket: args.ticketId, status: 'approved-after-rebuttal', impl: rebut }
phase('Verdict')
const advisory = await agent(tiebreakPrompt(rebut, reReview), { schema: ADVISORY })  // OpenRouter via bridge
return { ticket: args.ticketId, status: 'split', positions: { impl: rebut, reviews: reReview, advisory } }
// status:'split' → Fable-5 adjudicates in the main loop (step 4 of the ladder)
```

### 4.2 `v2-lane-batch` — a phase's worth of tickets per lane

Takes the unblocked ticket list for one lane, pipelines them through
`v2-consensus-ticket` via nested `workflow()` calls, respecting intra-lane
dependencies (sequential groups, parallel within a group).

### 4.3 `v2-test-green-loop` — loop-until-green

After any batch lands on a lane branch:

```js
let attempt = 0
while (attempt < 4) {
  const run = await agent('Run pytest + ruff + mypy in <worktree>; return failures as JSON', { schema: CI_RESULT })
  if (run.failures.length === 0) return { green: true, attempts: attempt }
  const fixes = await parallel(chunk(run.failures, 5).map(group => () =>
    agent(fixPrompt(group), { agentType: 'codex:codex-rescue', isolation: undefined })))
  attempt++
  log(`green-loop attempt ${attempt}: ${run.failures.length} failures being fixed`)
}
return { green: false, escalate: true }   // 4 strikes → Fable-5 + possibly Opus
```

### 4.4 `v2-leak-hunt` — loop-until-dry adversarial sweep (E-51/E-61)

The security analogue: DeepSeek + Gemini finders attack the trimmed-retrieval
surface (REST routes + 29 MCP tools + fabric SSE + aggregates) until two
consecutive rounds find nothing new. Findings are adversarially verified
(3-lens: reproduces?, actually-unauthorized?, exploitable-offline?) before
Opus fixes. This implements the "every data-returning path" enumeration the
HTML plan was missing (see §7 corrections).

### 4.5 `v2-gate-review` — phase-gate checklist

A judge panel: each gate criterion (G0–G4 from the HTML plan §3) gets one
verification agent that must produce *evidence* (command output, test name,
commit hash), not assertion. Fable-5 reads the evidence table and declares the
gate. Gate verdicts are recorded via `depthfusion_record_decision`.

---

## 5. Phase Execution Map

Numbering, epics, stories, ACs: unchanged from the HTML plan. This table only
re-maps *who executes*.

### Phase 0 — Rails (E-48, trunk, ~Fable-5-heavy by design)

| Ticket | Executor | Notes |
|---|---|---|
| T-533 branch + worktrees + protection | Haiku | mechanical |
| T-534 CI workflow (lint/types/tests/80% floor) | Codex 5.5 | rev: DeepSeek |
| T-535 PR template | Ollama | rev: Haiku |
| T-536/T-537 routing schema + `scripts/v2/route.sh` CLI wrappers | Codex 5.5 | **Critical:** the JSON-normalising wrappers for deepseek/gemini CLIs power every review thereafter. rev: DeepSeek + manual smoke by Fable-5 |
| T-538 cost ledger `~/.claude/v2-cost.jsonl` | Haiku | every workflow agent appends |
| T-539 five-provider smoke test | Haiku | gate G0 evidence |
| T-540–T-543 consensus workflow (the §4.1 script + checklist prompts + dry run) | **Fable-5 authors the workflow scripts** (they're orchestration, not product code); Codex 5.5 writes the prompt templates | Dry-run both paths before G0 |

**Phase 0 addition (from plan review):** `T-533a` — fix the four stale references
in the HTML plan before BACKLOG.md transcription (§7 below). Executor: Haiku.

### Phase 1 — Foundation (4 lanes in parallel, 4 worktrees)

| Lane | Epic | Dev | Review | Fable-5 touchpoints |
|---|---|---|---|---|
| A | E-49 identity (OIDC/JWKS/device-code) | **Opus 4.8** for T-544/549/553; Codex 5.5 for the rest | DS+GM dual on Opus tickets; DS on Codex tickets | Adjudicates D-1 (IdP scope) **before** lane start |
| B | E-53 parsers (docx/xlsx/pptx/PDF/OCR) | Codex 5.5 (perfect fit — well-specified, pattern-following: mirrors existing `parsers/` protocol at `src/depthfusion/parsers/base.py`) | DS | None expected |
| C | E-56 Tauri shell | Codex 5.5; **Opus 4.8 for T-628/T-630** (IPC hardening, token vault) | GM (frontend), DS+GM (Rust security) | Adjudicates D-3 (already decided: Tauri) · **T-628 ✓ 2026-06-14** (typed IPC + CSP, compiled on VPS) · **T-630 ✓ 2026-06-14** (vault expiry + 19 unit tests, READY_TO_MERGE) |
| D | E-52 sync design docs (T-581/T-582) | **Opus 4.8** (design doc, not code — one shot, high leverage) | DS+GM | **Moved earlier:** design doc starts in Phase 0, parallel to E-48 (see §7, bottleneck fix) |

Each lane = one `v2-lane-batch` workflow run in the background. Fable-5 monitors
via task notifications, handles `status:'split'` returns, runs `v2-gate-review`
for G1.

### Phase 2 — Integration

| Epic | Dev | Review | Notes |
|---|---|---|---|
| E-50 RBAC/ACL/classification | Opus 4.8 (T-556/557/560/565/568), Codex 5.5 (migrations, CLI, tests) | DS+GM on policy engine | The six-store ACL migration (T-561) touches every file in §6 — run in a worktree, rehearse on VPS copy (T-564) before merge |
| E-51 trimmed retrieval | Opus 4.8 (T-572 filter internals), Codex 5.5 (threading, benchmarks) | DS+GM | Insert at `hybrid.py:420–480` replacing the project filter; post-rank re-verify after fusion. Run `v2-leak-hunt` as the exit test |
| E-54 SharePoint connector | Codex 5.5; Opus 4.8 for T-604 (cert auth) + T-611 (permission resolver) | DS, +GM on T-611 | Graph API quirks: give Codex the Graph docs via context7 in the ticket prompt |
| E-57 UI features | Codex 5.5 | GM | Pure frontend; Gemini reviews component trees whole |
| E-52 sync build | Codex 5.5; Opus 4.8 for T-584 (pull trim) | DS | Server endpoints land in `rest.py` — coordinate with Lane A's auth middleware (same file, see §6 collision table) |

### Phase 3 — Intelligence

| Epic | Dev | Review |
|---|---|---|
| E-55 BI layer | Codex 5.5 | DS; GM for entity-extraction prompts |
| E-58 cache (SQLCipher, relevance model, leases) | Opus 4.8 (T-649/650/653/657/660 — crypto + scoring), Codex 5.5 (rest) | DS+GM on crypto |
| E-59 export controls | Opus 4.8 (T-662/663/664/666), Codex 5.5 (policy CRUD, footers) | DS+GM |

Fable-5 decision required before E-58 S-189: **cold-start policy for the activity
centroid** (plan review found a circular dependency: cache fill needs the
centroid, the centroid needs activity, activity needs cache). Proposed default:
first-run cache fill = pinned projects + most-recent-N records by recency only;
centroid activates after 50 activity signals. → record as V2-DEC-001.

### Phase 4 — Hardening & Landing

| Epic | Executor pattern |
|---|---|
| E-60 audit/observability | Codex 5.5 dev, DS rev; Opus 4.8 on hash-chain (T-669) |
| E-61 perf + pen-test | `v2-leak-hunt` workflow at full scale: DS+GM attack lanes, Opus fixes, Fable-5 triages. Load benchmarks: Codex 5.5 |
| E-62 docs | Gemini dev (long-context capability audit), Haiku runbooks, Codex spot-review |
| E-63 integration/pilot/merge | E2E suite: Codex 5.5. Migration CLI: Opus 4.8 (T-693). Merge-gate: `v2-gate-review` + final DS+GM consensus on the merge diff, Fable-5 presents to Greg |

---

## 6. Codebase Integration Map (where V2 lands in V1)

From the architecture survey — the anchors every ticket prompt cites:

| V2 concern | V1 anchor | Detail |
|---|---|---|
| Auth insertion | `src/depthfusion/api/rest.py:152` (`_check_auth`) | Replace token check with principal extraction; `_check_query_auth` at :159 |
| ACL columns | `storage/memory_store.py:16–33` (DDL), `storage/vector_store.py` (Chroma metadata), `storage/event_log.py`, `storage/file_index.py`, `graph/store.py:108–133`, discoveries frontmatter | Six stores, exactly as S-160 AC-1 lists |
| Retrieval trim point | `retrieval/hybrid.py:420–480` (project filter → ACL filter) | Post-BM25 pre-fusion; add post-rank re-verify after RRF (~line 300) |
| MCP principal binding | `mcp/server.py:804` (`_dispatch_tool`) | **3,655-line file — the #1 merge-collision risk.** See collision table below |
| New parsers | `parsers/base.py` protocol (49 LOC) | DocumentParser mirrors ConversationParser ergonomics |
| Backends | `backends/factory.py` quality chains (lines 53–81) | OpenRouterBackend already exists (E-48, 132 LOC) |
| Sync retirement | `sync.sh` (rsync, 100 lines) | Freeze at G1 per R-1 |
| Tests | 165 files, 24 packages; CI = ruff + mypy + pytest | 2,151 tests green at baseline |

**Merge-collision control for `mcp/server.py` and `rest.py`** (multiple lanes touch both):

- Lane A owns `rest.py` and `server.py` *auth/dispatch* surfaces in Phases 1–2.
- Lanes B/D queue their `server.py` additions (new tools) as *append-only patches*
  reviewed and merged by Fable-5 at phase gates, never directly on lane branches.
- Phase 0 adds a refactor ticket (new, `T-535a`, Codex 5.5): split `server.py`'s
  tool implementations into `mcp/tools/*.py` modules with `server.py` keeping only
  registration + dispatch. This converts the 3,655-line collision zone into
  per-domain files that lanes can own independently. *Do this before lanes fork.*

---

## 7. Corrections to the HTML Plan (apply before BACKLOG.md transcription)

Found during plan review; all are transcription-blocking but trivial:

1. **D-3** says "Blocks S-176" → should be **S-180** (Tauri scaffold, E-56).
2. **D-4** says "Blocks S-168 (E-52)" → should be **S-173 (E-54)** (SharePoint ingestion).
3. **R-2** cites "T-545" as the weekly cost report → should be **T-538** (cost ledger).
4. **R-3** says key escrow lives in "(E-51)" → should be **E-58**; recovery drill in E-61.
5. **New story needed (E-52 or E-63):** V1 memory migration validation — the S-160
   backfill (`classification=internal`, `acl_allow=[legacy-all]`) must be rehearsed
   *before* the pilot mixes legacy memories with SharePoint data, not discovered during it.
6. **New AC for S-189:** centroid cold-start definition (see Phase 3 note, V2-DEC-001).
7. **S-163/T-552 scope widening:** the route-walker test must also walk the 29 MCP
   tools and the fabric SSE stream, not just REST routes (the `v2-leak-hunt`
   workflow operationalises this).
8. **E-52 design (T-581/582) moves to Phase 0** — Lane A's ACL schema and Lane D's
   sync envelope must co-design, or E-52-build retrofits.

---

## 8. Loops, Budgets, Cost Control

- **Per-ticket budget:** every workflow agent logs to `~/.claude/v2-cost.jsonl`
  (T-538). Fable-5 reviews the ledger at each gate; >20% drift from the phase
  estimate triggers a routing-table revision (e.g., demote a work class from
  dual-review to single-review).
- **Workflow budget loops:** long sweeps (`v2-leak-hunt`, doc audits) use
  `while (budget.total && budget.remaining() > 50_000)` so a "+500k" directive
  scales depth instead of the script guessing.
- **Codex-first discipline:** if a Codex ticket fails review twice on the same
  root cause, the *rebuttal* escalates to Opus 4.8 — not the whole lane. One
  ticket's difficulty doesn't inflate the lane's cost class.
- **Fable-5 context budget:** lane reports arrive as ≤30-line structured
  summaries. Anything longer goes to `.agent-hub/outputs/` with a path reference.
- **Checkpointing:** every lane workflow's `runId` is recorded; crashed lanes
  resume via `resumeFromRunId` (unchanged prefix returns cached, only live work
  re-runs). DepthFusion checkpoints publish per the 30-min protocol.

---

## 9. Decision Log (adjudicated with Greg, 2026-06-10)

All pre-build decisions are resolved. These are binding; changes require a new V2-DEC record.

| ID | Decision | Resolution |
|---|---|---|
| **D-1** | IdP scope | **Entra ID only + one sealed break-glass admin.** No local fallback accounts. Break-glass: offline-stored credential, mandatory audit + alert on use. |
| **D-2** | ACL enforcement | **Dual-layer security-trimmed retrieval.** ACL filter inside BM25 doc-mask + HNSW metadata filter (pre-rank), PolicyEngine re-verification post-rank. No partitioned indexes. `v2-leak-hunt` is a merge gate. |
| **D-3** | UI shell | **Tauri 2 + React/TS.** Rust core owns secrets, cache key, export enforcement; webview gets session handles only. |
| **D-4** | SharePoint sync | **Full-content, site-scoped** (reverses HTML plan's metadata-first default). Cost bounded by Sites.Selected admission + delta sync. Size/type parse ceiling per S-171 folded in: oversized/media files metadata-only with on-demand fetch. Watch the metered bill on the pilot site before widening. |
| **V2-DEC-001** | Cache cold-start | **Deterministic warm-start → ML at threshold.** First fill = pinned + recency-ordered within budget; centroid scorer activates after ~50 activity signals; deterministic fill is the permanent fallback. Add as AC-5 to S-189. |
| **V2-DEC-002** | Legacy ACL backfill | **Owner-only, widen deliberately.** All V1 records → `acl_allow=[greg]`, `classification=internal`. Bulk per-project grant via admin CLI. **Amends S-160 AC-2** (was `[legacy-all]`). |
| **EXEC-1** | Autonomy mode | **Autonomous within phases, hard stops at gates.** Interrupts only for: G0–G4 sign-off, business-call DECISION tickets, Critical security findings, spend-cap events. Status summary every 5 tasks. Review gate fires after every task regardless. |

---

## 10. Kickoff Sequence (first session on `v2-enterprise`)

1. ~~Adjudicate D-1/D-2~~ **Done — see §9.**
2. Haiku: T-533 (branches/worktrees) + the §7 HTML-plan corrections (incl. S-160
   AC-2 amendment per V2-DEC-002 and S-189 AC-5 per V2-DEC-001).
3. Codex 5.5: T-534 (CI), T-536/537 (routing wrappers), **T-535a (server.py split)**.
4. Fable-5: author the five §4 workflow scripts; dry-run `v2-consensus-ticket`
   on a dummy ticket — both the approve path and a forced split (G0 AC-4).
5. Opus 4.8: T-581/582 (sync design doc) in parallel with steps 2–4.
6. `v2-gate-review` for G0 → lanes fork.

---

*Maintained by the chief-architect session. Changes to routing policy or escalation
rules require a V2-DEC record; changes to ticket scope go through the HTML plan +
BACKLOG.md as usual.*
