# DepthFusion: Standalone vs SkillForge — Divergence & Alignment Analysis

> **Purpose:** Map the two DepthFusion codebases, their differences, and alignment path for new feature integration.
> **Generated:** 2026-04-16

---

## 1. Two Codebases, Two Purposes

DepthFusion exists as two complementary but architecturally distinct implementations:

| Aspect | Python Standalone | TypeScript `@depthfusion/core` |
|--------|------------------|-------------------------------|
| **Location** | `/home/gregmorris/projects/depthfusion` | `/home/gregmorris/projects/skillforge/packages/depthfusion-core/` |
| **Version** | v0.3.1 / v0.4.0 | v0.1.0 |
| **Language** | Python 3.10+ | TypeScript (ESM) |
| **Purpose** | Cross-session memory for Claude Code | Fusion primitives for SkillForge Core runtime |
| **Deployment** | Standalone MCP server + CLI | Embedded library in monorepo |
| **State** | Persistent (ChromaDB, SQLite, JSON files) | Stateless (entire state passed in function params) |
| **Dependencies** | numpy, pyyaml, structlog, optional anthropic/chromadb | Zero runtime deps (dev only: typescript, vitest) |
| **Tests** | 439 (pytest) | 119 (vitest) |
| **Consumers** | Claude Code (via MCP + hooks) | SkillForge runtime, GEPA, CLaRa |

**Key invariant (D-9):** `@depthfusion/core` never imports `@skillforge/*` — it is a pure algorithm library. SkillForge's adapter layer wraps it with governance concerns.

---

## 2. Module-by-Module Comparison

### Shared Algorithms (parity or near-parity)

| Algorithm | Python (`depthfusion/`) | TS (`depthfusion-core/`) | Status |
|-----------|------------------------|--------------------------|--------|
| Cosine similarity | `core/scoring.py` | `scoring/index.ts` | Parity |
| Softmax scores | `core/scoring.py` | `scoring/index.ts` | Parity |
| Weighted aggregate | `core/scoring.py` | `scoring/index.ts` | Parity |
| RRF (k=60) | `fusion/rrf.py` | `fusion/rrf.ts` | Parity |
| AttnRes weighted fusion | `fusion/weighted.py` | `fusion/weighted.ts` | Parity |
| RetrievedChunk type | `core/types.py` | `types.ts` | Parity (field naming differs: snake_case vs camelCase) |
| SessionBlock type | `core/types.py` | `types.ts` | Parity |
| ContextItem type | `core/types.py` | `types.ts` | Parity |
| FeedbackEntry type | `core/types.py` | `types.ts` | Parity |
| FusionConfig type | `core/config.py` | `types.ts` | Partial (TS has subset of Python's 20 flags) |

### TS-only (not in Python)

| Module | TS Location | Description | Python Roadmap |
|--------|-------------|-------------|---------------|
| **Selective Fusion Weighter** | `fusion/selective-fusion-weighter.ts` | Mamba B/C/Delta gates for AttnRes fusion | Planned for Python v0.5.0 |
| **Chunk State Compression** | `fusion/chunk-state-compression.ts` | Compressed boundary state between chunks | Planned |
| **Materialisation Policy** | `fusion/materialisation-policy.ts` | Include/reference/defer decisions for chunks | Planned |
| **HorizonTuner** | `strategies/horizon-tuning.ts` | Adaptive content strategy selection | Not planned |
| **TrajectoryAnalyser** | `trajectory/trajectory-analyser.ts` | Run logging + feedback analysis | Different design in Python |
| **AsyncLLMReranker** | `reranker/llm-reranker.ts` | Provider-agnostic LLM reranker interface | Python has Haiku-specific only |
| **12 named strategies** | `strategies/index.ts` | Content selection strategies with metadata | Python has 4 (peek/summarize/grep/full) |

### Python-only (not in TS)

| Module | Python Location | Description | TS Applicability |
|--------|----------------|-------------|-----------------|
| **BM25 retrieval** | `retrieval/bm25.py` | Full keyword retrieval pipeline | N/A — MCP responsibility |
| **Haiku reranker** | `retrieval/reranker.py` | Claude Haiku semantic reranking | Covered by AsyncLLMReranker interface |
| **Hybrid pipeline** | `retrieval/hybrid.py` | RecallPipeline orchestration | N/A — SkillForge uses adapter layer |
| **Knowledge Graph** | `graph/` (7 files) | Entity/edge extraction, linking, traversal | N/A — not applicable to SkillForge |
| **Session processing** | `session/` (4 files) | .tmp file loading, tagging, scoring | N/A — Claude Code-specific |
| **Auto-capture** | `capture/` (2 files) | Heuristic/Haiku session extraction | N/A — Claude Code hooks |
| **Context bus** | `router/` (5 files) | Pub/sub context routing | SkillForge uses channels package |
| **MCP server** | `mcp/server.py` | 11 tools, JSON-RPC over stdio | N/A — Claude Code-specific |
| **Compatibility** | `analyzer/` (4 files) | C1-C11 Claude Code checks | N/A |
| **ChromaDB storage** | `storage/` (2 files) | Vector store + tier management | N/A — SkillForge uses Prisma/Postgres |
| **Metrics** | `metrics/` (2 files) | JSONL telemetry | SkillForge uses InvocationLog |
| **Install/migrate** | `install/` (2 files) | Setup CLI | N/A |
| **Sync script** | `sync.sh` | Local↔VPS rsync | N/A |

---

## 3. Architectural Divergences

### 3a. Statefulness

**Python:** Persistent state is fundamental to the design. BM25 indexes files from disk on every query. ChromaDB persists embeddings. SQLite stores the knowledge graph. Session sidecars are written to disk.

**TypeScript:** Strictly stateless by design (Invariant D-2). No database, no cross-call state. The entire context — chunks, embeddings, config — must be passed into each function call. State persistence is the responsibility of the SkillForge runtime adapter.

**Alignment concern:** Any new feature added to Python that involves persistent state will NOT have a direct TS equivalent. The TS version will need a stateless interface that receives the state from the caller.

### 3b. Reranker Interface

**Python:** `HaikuReranker` is hardcoded to Claude Haiku. It checks `DEPTHFUSION_API_KEY` and `ANTHROPIC_API_KEY`, instantiates `anthropic.Anthropic()`, and calls `messages.create()` directly.

**TypeScript:** `Reranker` is an interface with a single method:
```typescript
interface Reranker {
  complete(prompt: string): Promise<string>;
}
```
`AsyncLLMReranker` implements it generically — any LLM provider can be injected. `PassthroughReranker` is a no-op fallback.

**Alignment path:** Python should extract a `Reranker` protocol (abstract base) and make `HaikuReranker` one implementation. This enables future rerankers (e.g., local model, different API) without changing the pipeline.

### 3c. Fusion Gates (Mamba B/C/Delta)

**TypeScript has, Python lacks:**
```
B gate — semantic relevance gating (query similarity threshold)
C gate — content-dependent decay (topic-aware, adjacent chunk similarity)
Δ gate — output threshold filtering (variable-length output)
```

The TS `selectiveAttnresFusion()` applies these three gates to produce variable-length output (only chunks passing all gates are included). The classic `attnresFusion()` is preserved as fallback.

**Python's current approach:** Flat BM25 scoring with source weights and recency tie-breaker. No semantic gating. Top-k is fixed (not data-dependent).

**Alignment path:** Port `selective-fusion-weighter.ts` logic to Python as an optional fusion mode gated by `DEPTHFUSION_SELECTIVE_FUSION_ENABLED`. Requires embeddings on chunks (currently only available in VPS Tier 2 with ChromaDB).

### 3d. Strategies

**Python:** 4 preset RLM strategies (peek, summarize, grep, full) focused on recursive LLM execution patterns.

**TypeScript:** 12 named content strategies with metadata, plus `HorizonTuner` for adaptive selection based on performance history. These are content selection strategies (what to include/exclude), not execution strategies.

**These are different concerns.** Python strategies control HOW to execute a recursive LLM call. TS strategies control WHAT context to include. They are complementary, not competing.

### 3e. Knowledge Graph

**Python:** Full temporal entity-relationship graph with 8 entity types, 7 edge types, extraction pipeline (Regex + Haiku), linking pipeline (CoOccurrence + Temporal + Haiku), traversal, query expansion, and score boosting. Stores in JSON (local) or SQLite (VPS).

**TypeScript:** No graph concept. Not applicable — SkillForge uses different persistence models (Prisma/Postgres for validation memory, flat vector store for invocation history).

**Alignment concern:** Graph features added to Python have no TS counterpart to align with. This is a Python-exclusive domain.

---

## 4. SkillForge Integration Architecture

### Adapter Layer

The SkillForge runtime wraps `@depthfusion/core` via `SkillForgeDepthFusionAdapter`:

```
SkillForge Runtime
    └── SkillForgeDepthFusionAdapter
            ├── fuse()              → @depthfusion/core combineScores
            └── fuseSelective()     → @depthfusion/core selectiveAttnresFusion
                    ├── Tier 1 bypass (Invariant I-2)
                    ├── ACS floor enforcement (Invariant I-9)
                    └── Audit logging (Invariant I-11)
```

### Consumers

| Package | Import | Usage |
|---------|--------|-------|
| **runtime** | `@depthfusion/core` | Fusion adapter, scoring, reranker, trajectory |
| **gepa** | `@depthfusion/core` | Experience routing, gate parameter evolution |
| **clara** | `@depthfusion/core/scoring` | Selective-history scorer, context packer |

### GEPA Bidirectional Loop (Phase 4, not yet implemented)

```
DepthFusion → GEPA: FusionWeightsComputed events (B/C/Δ gate values)
GEPA → DepthFusion: evolved gate parameter configs
Safeguards: Pareto frontier retention, ACS quality floors, 3-decline rollback
```

---

## 5. Five Integration Seams (from `docs/skillforge-integration-plan.md`)

| Seam | SkillForge Location | DepthFusion Modules | What It Enables |
|------|--------------------|--------------------|----------------|
| **A: Router Scoring** | `runtime/src/router/phases.ts:83-100` | RRF + weighted + reranker | SkillForge router uses RRF fusion instead of flat scoring |
| **B: Semantic Judgment Cache** | `runtime/src/validator/validation-memory.ts:137` | scoring + dispatcher | Validator recalls semantically similar past judgments |
| **C: Vector Store Attention** | `state-memory/src/vector-store.ts:130-165` | scoring + weighted | Session blocks weighted by recency + source reliability |
| **D: RL Router State** | Phase 4 stub (not implemented) | trajectory + strategies + feedback | RL router accumulates trajectory-level feedback |
| **E: Context Budget** | `runtime/src/context/types.ts:23-28` | cost_estimator + config | Budget allocation becomes configurable per-task |

**Status:** AWAITING APPROVAL. All seams are additive — no existing SkillForge code needs to change.

**Implementation sequence:** SF-1 (Seams C+E5) → SF-2 (Seam A) → SF-3 (Seam B) → SF-4 (E1-E4)

---

## 6. Non-Negotiable Invariants

### DepthFusion Core (D-*)
| # | Rule |
|---|------|
| D-1 | Firewall compliance: no knowledge of model identities, providers, org structure |
| D-2 | Stateless: no DB, no cross-call state in `@depthfusion/core` |
| D-3 | Gate log mandatory: every fusion run emits complete gateLog |
| D-7 | Budget slice respected: works within allocated token slice |
| D-9 | Zero SkillForge imports: `@depthfusion/core` never imports `@skillforge/*` |
| D-10 | Mamba Section 10 gated: no Section 10 enhancements until Greg confirms |
| D-11 | Python MCP local-only: never calls external APIs for storage |
| D-12 | Verbatim default: stores raw text; AAAK is opt-in |

### SkillForge Adapter (I-*)
| # | Rule |
|---|------|
| I-2 | Tier 1 bypass: risk tier 1 skills NEVER undergo fusion |
| I-9 | Plugin isolation: extension hooks run AFTER ACS floor enforcement |
| I-11 | Audit logging: all gating decisions logged in InvocationLog |

---

## 7. Alignment Strategy for New Features

When adding a new feature to DepthFusion, consider which codebase(s) it affects:

### Decision Matrix

| Feature Type | Python Action | TS Action |
|-------------|--------------|-----------|
| **Retrieval algorithm** (new scorer, ranker) | Add to `retrieval/` or `fusion/` | Port pure algorithm to `@depthfusion/core` |
| **MCP tool** | Add to `mcp/server.py` | N/A (Claude Code-specific) |
| **Graph feature** | Add to `graph/` | N/A (Python-exclusive domain) |
| **Fusion strategy** | Add to `fusion/` | Port stateless version to `@depthfusion/core/fusion/` |
| **Hook/lifecycle** | Add to hooks | N/A (Claude Code-specific) |
| **Type/data model** | Add to `core/types.py` | Add to `types.ts` (maintain parity) |
| **Storage backend** | Add to `storage/` | N/A (TS is stateless) |
| **Scoring primitive** | Add to `core/scoring.py` | Port to `scoring/index.ts` |

### Alignment Checklist

For any new feature:

1. **Is it a pure algorithm?** → Implement in both Python and TS, keeping function signatures aligned
2. **Does it involve persistence?** → Python only; TS gets a stateless interface that receives state
3. **Does it extend types?** → Update both `core/types.py` and `types.ts`
4. **Does it need a feature flag?** → Add to Python config; TS doesn't use flags (adapter handles)
5. **Does it affect the fusion pipeline?** → Must emit gateLog entries in TS (Invariant D-3)
6. **Does it need embeddings?** → Available in Python VPS Tier 2; available in TS via metadata

---

## 8. Current Gaps Requiring Alignment

| Gap | Python State | TS State | Priority |
|-----|-------------|----------|----------|
| Selective fusion (B/C/Δ gates) | Not implemented | Implemented | High — core scoring divergence |
| Chunk state compression | Not implemented | Implemented | Medium |
| Materialisation policy | Not implemented | Implemented | Medium |
| Reranker abstraction | Haiku-specific | Provider-agnostic interface | Medium — limits Python extensibility |
| HorizonTuner | Not applicable (different strategy domain) | Implemented | Low |
| Knowledge graph | Full implementation | Not applicable | N/A — Python-exclusive |
| Auto-capture hooks | Full implementation | Not applicable | N/A — Claude Code-specific |

### Recommended Alignment Sequence

1. **Port selective fusion gates to Python** — highest impact alignment gap. Requires embedding support in BM25 results (available in Tier 2).
2. **Abstract reranker interface in Python** — extract `Reranker` protocol from `HaikuReranker`. Enables pluggable reranking.
3. **Port chunk state compression to Python** — enables compressed boundary state in multi-block retrieval.
4. **Port materialisation policy to Python** — enables include/reference/defer decisions.

---

## 9. TS Core File Inventory (24 files)

```
packages/depthfusion-core/src/
├── index.ts                                    Root exports
├── types.ts                                    Core interfaces
├── context/index.ts                            ContextItem definitions
├── vector/index.ts                             Vector store interface
├── scoring/
│   ├── index.ts                                cosineSimilarity, softmaxScores, weightedAggregate
│   └── scoring.test.ts
├── fusion/
│   ├── index.ts                                Re-exports
│   ├── rrf.ts                                  Reciprocal Rank Fusion (k=60)
│   ├── weighted.ts                             AttnRes weighting
│   ├── selective-fusion-weighter.ts            Mamba B/C/Δ gates
│   ├── selective-fusion-weighter.test.ts
│   ├── chunk-state-compression.ts              Compressed boundary state
│   ├── chunk-state-compression.test.ts
│   ├── materialisation-policy.ts               Include/reference/defer decisions
│   └── materialisation-policy.test.ts
├── reranker/
│   ├── index.ts                                Reranker interface + combineScores
│   ├── llm-reranker.ts                         AsyncLLMReranker
│   └── llm-reranker.test.ts
├── trajectory/
│   ├── index.ts                                RecursiveTrajectory
│   ├── trajectory-analyser.ts                  TrajectoryAnalyser + feedback
│   └── trajectory-analyser.test.ts
└── strategies/
    ├── index.ts                                12 named strategies, getStrategy()
    ├── horizon-tuning.ts                       Adaptive strategy selection
    └── horizon-tuning.test.ts
```

---

## 10. TS Core Key Interfaces

```typescript
// types.ts
interface RetrievedChunk {
  readonly chunkId: string;
  readonly content: string;
  readonly source: string;
  score: number;
  rank: number | null;
  readonly metadata: Record<string, unknown>;
}

interface FusionConfig {
  readonly k?: number;          // RRF constant (default 60)
  readonly topK?: number;       // max results (default 10)
  readonly sourceWeights?: Readonly<Record<string, number>>;
}

// fusion/selective-fusion-weighter.ts
interface SelectiveGateConfig {
  readonly bGateMinSimilarity?: number;   // default 0.1
  readonly cGateDecayRatio?: number;      // default 3
  readonly deltaGateThreshold?: number;   // default 0.05
}

interface SelectiveGateResult {
  readonly chunkId: string;
  readonly bGateValue: number;
  readonly cGateValue: number;
  readonly deltaIncluded: boolean;
  readonly fusedScore: number;
}

// reranker/index.ts
interface Reranker {
  complete(prompt: string): Promise<string>;
}
```

---

## 11. Summary

**The two codebases share scoring/fusion algorithms but serve different deployment targets.** Python is a standalone memory tool for Claude Code with persistent storage, retrieval pipelines, and auto-capture. TypeScript is a stateless algorithm library embedded in SkillForge's runtime.

**The primary alignment gap** is the Mamba selective fusion gates (B/C/Δ) — implemented in TS but not yet in Python. New features should be assessed against the decision matrix in Section 7 to determine which codebase(s) need changes.

**The integration plan** (5 seams, awaiting approval) details how Python's capabilities could be exposed to SkillForge via TypeScript ports and adapter wrappers. No existing SkillForge code needs to change — all integration points are additive.
