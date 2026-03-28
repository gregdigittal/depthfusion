# DepthFusion → SkillForge Integration Plan
# Date: 2026-03-28 | Status: AWAITING APPROVAL

---

## 1. Current State

| Item | Value |
|------|-------|
| SkillForge location | `~/skillforge/` |
| SkillForge stack | TypeScript / Node.js / PostgreSQL / Prisma / Zod |
| SkillForge packages | 12–13 packages (skill-ir, runtime, state-memory, db, opl, acs, gsci, channels, osg, mpce, csie, scheduler) |
| SkillForge apps | 3 (api, channel-gateway, web) |
| Existing DepthFusion concepts in SkillForge | **NONE** — no RRF, no weighted fusion, no AttnRes references |
| Integration classification | **SEAMS_ONLY** — 5 natural attachment points, no refactoring required |
| Blockers | **NONE** — sub-call routing requires new method (additive, not a blocker) |

---

## 2. Module Portability Table

All 28 DepthFusion modules assessed for TypeScript port vs Python sidecar strategy.

### Legend
- **PORT** — rewrite in TypeScript, embed directly in SkillForge package
- **SIDECAR** — keep as Python, call via subprocess or HTTP from SkillForge
- **WRAP** — expose DepthFusion Python module via FastAPI/gRPC endpoint; SkillForge calls it

| # | Module | Path | Strategy | Rationale |
|---|--------|------|----------|-----------|
| 1 | `types` | `core/types.py` | PORT | Pure data structures — trivial Zod/TS types |
| 2 | `config` | `core/config.py` | PORT | Env-var config object — map to SkillForge env pattern |
| 3 | `scoring` | `core/scoring.py` | PORT | Cosine + softmax + weighted — pure math, 60 lines |
| 4 | `feedback` | `core/feedback.py` | SIDECAR | JSONL persistence — SkillForge uses Prisma/Postgres; retain as Python until DB layer decision |
| 5 | `rrf` | `fusion/rrf.py` | PORT | RRF k=60 formula — 20 lines, pure function |
| 6 | `weighted` | `fusion/weighted.py` | PORT | AttnRes attention weights — port alongside `rrf` |
| 7 | `block_retrieval` | `fusion/block_retrieval.py` | SIDECAR | k-means clustering — depends on numpy; WRAP via HTTP if needed at scale |
| 8 | `reranker` | `fusion/reranker.py` | PORT | Score combiner — wraps rrf + weighted; port after those two |
| 9 | `tagger` | `session/tagger.py` | SIDECAR | Writes `.meta.yaml` sidecars — filesystem I/O, C1-specific, not needed in SkillForge |
| 10 | `scorer` | `session/scorer.py` | PORT | Tag + keyword scoring — port to SkillForge validator layer |
| 11 | `loader` | `session/loader.py` | SIDECAR | Loads Claude session `.tmp` files — Claude Code-specific, no SkillForge equivalent |
| 12 | `compactor` | `session/compactor.py` | SIDECAR | Claude session compaction — Claude Code-specific |
| 13 | `bus` | `router/bus.py` | PORT | InMemoryBus / FileBus — port to SkillForge event emitter or use existing channels package |
| 14 | `publisher` | `router/publisher.py` | PORT | Pub/sub publisher — thin wrapper; port to channels layer |
| 15 | `subscriber` | `router/subscriber.py` | PORT | Pub/sub subscriber — port alongside publisher |
| 16 | `dispatcher` | `router/dispatcher.py` | PORT | Context routing dispatch — **Seam B** attachment point |
| 17 | `cost_estimator` | `router/cost_estimator.py` | PORT | Token cost ceiling — port to SkillForge budget/context layer |
| 18 | `trajectory` | `recursive/trajectory.py` | PORT | RecursiveTrajectory dataclass — trivial Zod schema |
| 19 | `sandbox` | `recursive/sandbox.py` | SIDECAR | Restricted subprocess — security boundary; keep as Python |
| 20 | `strategies` | `recursive/strategies.py` | PORT | 4 preset strategies (peek/summarize/grep/full) — port as TS enum + config |
| 21 | `client` | `recursive/client.py` | WRAP | RLMClient — depends on `rlm` Python package; expose via HTTP endpoint |
| 22 | `scanner` | `analyzer/scanner.py` | SIDECAR | Scans `~/.claude` inventory — Claude Code-specific |
| 23 | `compatibility` | `analyzer/compatibility.py` | SIDECAR | C1-C11 checker — Claude Code-specific, not applicable to SkillForge |
| 24 | `recommender` | `analyzer/recommender.py` | SIDECAR | Claude Code recommendations — Claude Code-specific |
| 25 | `installer` | `analyzer/installer.py` | SIDECAR | Claude Code hook installer — Claude Code-specific |
| 26 | `server` | `mcp/server.py` | SIDECAR | MCP stdio server — Claude Code-specific, already registered |
| 27 | `collector` | `metrics/collector.py` | PORT | JSONL metrics → port to SkillForge telemetry/OpenTelemetry span |
| 28 | `aggregator` | `metrics/aggregator.py` | PORT | Digest formatter → port to SkillForge metrics aggregator |

**Summary:** 14 PORT | 10 SIDECAR | 2 WRAP (client, block_retrieval)

---

## 3. Integration Seams

Five natural attachment points — no existing SkillForge code needs to change. All additions are **additive**.

### Seam A — Router Scoring Hook
**File:** `packages/runtime/src/router/phases.ts:83-100`
**Current:** `computeScore()` and `scoreAndRank()` use a single flat scoring function
**Integration:** Add `FusionStrategy` interface with `score(candidates: Candidate[]): RankedCandidate[]`
**DepthFusion modules:** `rrf` (module 5), `weighted` (module 6), `reranker` (module 8)
**What it enables:** SkillForge router uses RRF × block_weight × source_weight instead of flat scoring

```typescript
// New interface to add (additive):
interface FusionStrategy {
  score(candidates: Candidate[], context: RouterContext): RankedCandidate[];
}

// Inject at phases.ts:97 (after scoreAndRank call):
if (fusionStrategy) {
  ranked = fusionStrategy.score(ranked, context);
}
```

---

### Seam B — Semantic Judgment Cache
**File:** `packages/runtime/src/validator/validation-memory.ts:137-196`
**Current:** `recallSimilar()` uses exact-match hash lookup for past validation decisions
**Integration:** Wrap with `ContextRouter` (DepthFusion dispatcher module 16) for cosine-similarity fallback
**DepthFusion modules:** `scoring` (module 3), `dispatcher` (module 16)
**What it enables:** Validator retrieves semantically similar past judgments, not just exact matches

```typescript
// Current at line 137:
async recallSimilar(hash: string): Promise<ValidationMemoryEntry | null>

// Proposed augmentation (additive — new overload):
async recallSimilarSemantic(embedding: number[], topK: number): Promise<ValidationMemoryEntry[]>
```

---

### Seam C — Vector Store Attention Layer
**File:** `packages/state-memory/src/vector-store.ts:130-165`
**Current:** `FlatVectorStore.similarInvocations()` returns cosine-ranked results without attention weighting
**Integration:** Add AttnRes attention weight post-processing step after cosine ranking
**DepthFusion modules:** `scoring` (module 3), `weighted` (module 6)
**What it enables:** Session blocks weighted by recency + source reliability, not flat cosine

```typescript
// Inject after line 165 (current return):
const attentionWeighted = applyAttnResWeights(results, sessionWeights);
return attentionWeighted;
```

---

### Seam D — RL Router State Extension
**File:** Phase 4 stub in `packages/runtime/src/router/` (not yet implemented)
**Current:** `RoutingState` carries flat reward signal
**Integration:** Extend to `LearnedRoutingState` with trajectory history from `RecursiveTrajectory` (module 18)
**DepthFusion modules:** `trajectory` (module 18), `strategies` (module 20), `feedback` (module 4)
**What it enables:** RL router accumulates trajectory-level feedback, not just step-level reward

```typescript
// New type (additive):
interface LearnedRoutingState extends RoutingState {
  trajectories: RecursiveTrajectory[];
  strategyPerformance: Map<string, number>; // strategy → mean reward
}
```

---

### Seam E — Context Budget Allocation
**File:** `packages/runtime/src/context/types.ts:23-28`
**Current:** `BUDGET_FRACTIONS` is a hardcoded const object
**Integration:** Replace with `ContextAllocationStrategy` interface, keep const as default implementation
**DepthFusion modules:** `cost_estimator` (module 17), `config` (module 2)
**What it enables:** Budget allocation becomes configurable per-task; DepthFusion cost ceiling enforced

```typescript
// Current at line 23:
export const BUDGET_FRACTIONS = { system: 0.15, history: 0.45, tools: 0.25, scratch: 0.15 } as const;

// Proposed (additive — const stays as default):
export interface ContextAllocationStrategy {
  allocate(totalBudget: number, taskType: string): BudgetAllocation;
}
export const DEFAULT_BUDGET_FRACTIONS: ContextAllocationStrategy = { /* wraps existing const */ };
```

---

## 4. Required Extension Points

Five additions needed in existing SkillForge packages — all **additive**, no destructive changes:

| # | Extension | Package | Type | Notes |
|---|-----------|---------|------|-------|
| E1 | `recursive_llm_call` step type | `packages/skill-ir/src/schema.ts` | New Zod discriminatedUnion variant | `schema.ts` already uses discriminatedUnion — extensible by design |
| E2 | `weighted_retrieval` step type | `packages/skill-ir/src/schema.ts` | New Zod discriminatedUnion variant | Same file as E1 |
| E3 | Retrieval quality validator | `packages/runtime/src/validator/` | New validator class | Validates weighted_retrieval step outputs |
| E4 | `routeSubCall()` method | `packages/runtime/src/router/CapabilityRouter` | New method on existing class | Required for recursive step execution; not currently implemented |
| E5 | Trajectory telemetry scoring | `packages/runtime/src/metrics/` or OpenTelemetry spans | New span attribute | Time-decay scoring on span timestamps; replaces EMA where trajectory depth matters |

---

## 5. Recommended Implementation Sequence

### Phase SF-1: Foundation (non-breaking, Seams C + E5)
**Goal:** Replace flat vector store with attention-weighted retrieval
- Port `scoring.py` + `weighted.py` to TypeScript (`packages/runtime/src/fusion/`)
- Inject AttnRes layer at Seam C (`vector-store.ts:165`)
- Add trajectory telemetry at Seam E (E5)
- **Risk:** LOW — additive only, existing tests unaffected
- **Test gate:** Run SkillForge test suite; add unit tests for `applyAttnResWeights()`

### Phase SF-2: Router Fusion (Seam A)
**Goal:** Replace flat router scoring with RRF × attention weights
- Port `rrf.py` + `reranker.py` to TypeScript (`packages/runtime/src/fusion/`)
- Add `FusionStrategy` interface and inject at Seam A (`phases.ts:97`)
- **Risk:** MEDIUM — changes router output ordering; validate with integration tests
- **Test gate:** A/B comparison: flat scoring vs fusion scoring on recorded SkillForge invocations

### Phase SF-3: Semantic Memory (Seam B)
**Goal:** Augment validation memory with semantic similarity recall
- Port `dispatcher.py` to TypeScript; bind to SkillForge context model
- Add `recallSimilarSemantic()` overload at Seam B (`validation-memory.ts:137`)
- **Risk:** LOW — new overload, existing `recallSimilar()` unchanged
- **Test gate:** Unit tests on cosine fallback; verify no regression on exact-match path

### Phase SF-4: Recursive Step Support (E1-E4)
**Goal:** Enable SkillForge to execute `recursive_llm_call` steps
- Add `recursive_llm_call` + `weighted_retrieval` step types (E1, E2)
- Add retrieval quality validator (E3)
- Implement `routeSubCall()` on CapabilityRouter (E4)
- Wrap `recursive/client.py` as HTTP sidecar (module 21)
- **Risk:** MEDIUM — new execution path; sandbox module stays as Python
- **Test gate:** TDD: write failing tests for new step types first; implement to green

### Phase SF-5: RL State + Budget (Seams D + E)
**Goal:** Connect trajectory feedback to RL router and budget allocation
- Port `trajectory.py` + `strategies.py` to TypeScript
- Add `LearnedRoutingState` at Seam D (Phase 4 RL stub)
- Add `ContextAllocationStrategy` interface at Seam E (`types.ts:23`)
- **Risk:** LOW (Seam E additive) | MEDIUM (Seam D depends on RL Phase 4 completion)
- **Test gate:** Unit tests on LearnedRoutingState accumulation; budget strategy interface tests

---

## 6. Constraints

- **Do NOT modify existing SkillForge code destructively** — all changes must be additive (new interfaces, new overloads, new variants)
- **Do NOT move DepthFusion Python modules** into SkillForge — SIDECAR modules stay as Python
- **Do NOT run Phase SF-4 before Phase SF-2** — router fusion must be proven stable before recursive steps are added
- **SkillForge tests must remain GREEN** at the end of every phase
- **C1-C11 compliance is DepthFusion-side only** — does not apply to SkillForge code

---

## 7. Files That Will Change (per phase)

| Phase | Files Modified | Files Created |
|-------|---------------|---------------|
| SF-1 | `packages/state-memory/src/vector-store.ts` | `packages/runtime/src/fusion/weighted.ts`, `packages/runtime/src/fusion/scoring.ts` |
| SF-2 | `packages/runtime/src/router/phases.ts` | `packages/runtime/src/fusion/rrf.ts`, `packages/runtime/src/fusion/reranker.ts`, `packages/runtime/src/fusion/types.ts` |
| SF-3 | `packages/runtime/src/validator/validation-memory.ts` | `packages/runtime/src/router/dispatcher.ts` |
| SF-4 | `packages/skill-ir/src/schema.ts`, `packages/runtime/src/router/CapabilityRouter.ts` | `packages/runtime/src/validator/retrieval-validator.ts`, `services/recursive-sidecar/` (Python HTTP wrapper) |
| SF-5 | `packages/runtime/src/router/` (RL stub), `packages/runtime/src/context/types.ts` | `packages/runtime/src/router/learned-routing-state.ts` |

---

## 8. Approval Required

This plan makes NO changes to SkillForge code. It is a specification only.

**To proceed with Phase SF-1, reply: "approve SF-1"**
**To proceed with all phases sequentially, reply: "approve all phases"**
**To modify the plan before approving, specify the change.**
