# MemPalace vs Saihai Deep (DepthFusion) + CLaRa — Comparative Analysis
**Date:** 2026-05-18
**Analyst:** Claude (automated, via analysis prompt)
**Deliverable:** MEMPALACE_DEPTHFUSION_ANALYSIS.md

---

## Inputs Confirmed

| Input | Access path / URL | Summary |
|---|---|---|
| **MemPalace** | `https://github.com/MemPalace/mempalace` (README, `backends/base.py`, `knowledge_graph.py`, `searcher.py`, directory listing) | Actively maintained local-first AI memory system. 52K stars, commits as of 2026-05-18. Hierarchical palace metaphor + SQLite KG + ChromaDB vector backend. |
| **DepthFusion source** | `/home/gregmorris/projects/depthfusion/src/depthfusion/` — `retrieval/hybrid.py`, `fusion/gates.py`, `core/types.py`, `storage/vector_store.py`, `core/hit_tracker.py`, `capture/auto_learn.py`, `capture/decay.py`, `cognitive/scorer.py`, `cognitive/consolidator.py`, `graph/store.py`, `session/scorer.py`, `capture/pruner.py`, `mcp/server.py`, `core/memory_object.py` | Full Python MCP server. BM25 + optional ChromaDB pipeline with Mamba-ported B/C/Δ fusion gates, CognitiveScorer, salience decay, graph traversal, HitTracker feedback loop, 40+ source files. |
| **DEPTHFUSION_ARCHITECTURE.md** | `/home/gregmorris/projects/skillforge/docs/research/DEPTHFUSION_ARCHITECTURE.md` | Canonical architecture doc for both TS `@depthfusion/core` and Python `gregdigittal/depthfusion`. Defines invariants D-1 through D-12, two-implementation distinction, and open decisions OD-1/OD-2/OD-3. |
| **SAIHAI_PLATFORM_CONTEXT.md** | `/home/gregmorris/projects/skillforge/docs/research/SAIHAI_PLATFORM_CONTEXT.md` | Platform context document defining CLaRa's six subsystems, 15 non-negotiable invariants (I-1 through I-15), Skill IR firewall, and the three-tier ownership model (CLaRa → Deep TS → Deep Python). |

---

## Section 1 — MemPalace Characterisation

### Summary

MemPalace is a local-first, privacy-focused AI memory system for Claude Code (and potentially other LLM clients). It stores conversation history verbatim and retrieves it via hybrid semantic + keyword search, achieving 96.6% R@5 on LongMemEval with no LLM calls and ≥99% with LLM reranking. The project is actively developed (52K stars, daily commits as of 2026-05-18), has 551 open issues indicating genuine production usage, and was created in April 2026.

### Core Mental Model / Metaphor

The **palace metaphor**: information is organised into **Wings** (people or projects), **Rooms** (topics), and **Drawers** (atomic content units). The system extends this with a **knowledge graph layer** (temporal entity-relationship graph over the same SQLite database) and **agent diaries** accessible via MCP. The palace metaphor serves as a namespace + scoping mechanism for retrieval, not just a UI label. [MemPalace README ¶2]

### Architecture

**Components:**
- `palace.py` — Wing/Room/Drawer hierarchy management (27 KB)
- `backends/` — Pluggable storage backends; `base.py` defines RFC 001 contract; default is ChromaDB with HNSW cosine space
- `knowledge_graph.py` — SQLite-backed temporal knowledge graph with `[valid_from, valid_to]` validity windows [MemPalace `knowledge_graph.py`]
- `searcher.py` — Hybrid BM25 + vector search with closet boosting and "union" mode for FTS5 fallback [MemPalace `searcher.py`]
- `miner.py` + `convo_miner.py` — Ingest Claude Code sessions and project files into the corpus (49 KB each)
- `mcp_server.py` — 29 MCP tools (106 KB); the primary integration surface
- `entity_detector.py` / `entity_registry.py` — Named entity extraction and caching
- `embedding.py` — Embedding generation (pluggable backends)
- `dedup.py` — Deduplication logic
- `llm_client.py` / `llm_refine.py` — Optional LLM integration for reranking and refinement

**Data flow (write path):**
```
Claude Code session → miner.py (parse + chunk) → embedding.py (embed) → ChromaDB (upsert)
                                                                         → SQLite KG (entities + triples)
```

**Data flow (read path):**
```
Query → searcher.py → BM25 (SQLite FTS5) + ChromaDB (vector) → hybrid_rank() → [LLM rerank] → results
```

**Storage:** ChromaDB (vectors + documents, HNSW cosine space) + SQLite (KG triples, entities, BM25 FTS5 index). All local by default. [MemPalace README §Core Architecture]

### Memory Operations

| Operation | Implementation |
|---|---|
| Write | `add` / `upsert` via `BaseCollection.add()/upsert()` — embed + store in ChromaDB, entity extraction → SQLite KG. [MemPalace `backends/base.py`] |
| Read | `searcher.py` hybrid query — BM25 floor + vector similarity + closet boost [MemPalace `searcher.py`] |
| Update | `BaseCollection.update()` = get + merge + upsert semantics [MemPalace `backends/base.py`] |
| Forget | `BaseCollection.delete()` — by ID or filter. Also: KG `invalidate()` marks triples as expired without physical deletion [MemPalace `knowledge_graph.py`] |
| Consolidate | Not present as a dedicated pass; dedup.py handles at-write deduplication |
| Search | `query()` → hybrid BM25 + vector + closet boost; fallback to FTS5-only on vector failure [MemPalace `searcher.py`] |
| Temporal query | KG `query_entity(as_of=)`, `timeline()`, `query_relationship()` [MemPalace `knowledge_graph.py`] |

### Scoring / Ranking

MemPalace's hybrid score is:

```
final_score = (0.6 × vector_sim) + (0.4 × normalised_bm25)
```

BM25 is **min-max normalised within the candidate set** (relative), while vector similarity uses **absolute cosine** (`max(0, 1 - distance)`) so the two signals share a [0, 1] range. Closet boosts are additive (+0.40/+0.25/+0.15/... at ranks 0–4), applied only when `closet_distance ≤ 1.5`. "Closets are a ranking SIGNAL, never a GATE." The "union" candidate strategy merges BM25-only FTS5 hits with vector candidates to catch lexically strong / vector-distant documents. [MemPalace `searcher.py`]

LLM reranking is opt-in (no API key required for base operation). The benchmark reports 96.6% R@5 raw → 98.4% hybrid v4 → ≥99% with LLM reranking. [MemPalace README §Performance Benchmarks]

### Dependencies

- Python 3.9+
- ChromaDB (default vector backend)
- SQLite (built-in; used for KG and BM25 FTS5)
- Optional: LLM API key for reranking; sentence-transformers for local embeddings

Runtime: ~300 MB disk; runs entirely on local hardware. [MemPalace README §Requirements]

### Maturity Signals

- **Stars:** 52,406 (as of 2026-05-18) [GitHub API]
- **Commits:** Daily, multiple PRs merged per day (2026-05-17/18 evidence: `#1528`, `#1445`, `#1438`) [GitHub commits API]
- **Open issues:** 551 [GitHub API]
- **Created:** 2026-04-05 — approximately 6 weeks old [GitHub API]
- **Tests:** `test_convo_miner.py`, `test_mcp_server.py` confirmed (ruff formatting applied to both). Test count unknown from available data.
- **Production usage:** High star count + active issue tracker suggests broad production/power-user adoption. No enterprise reference customers confirmed.

### Licence and Adoption Constraints

Licence not directly confirmed in read data; project is on PyPI as `uv tool install mempalace`. Official repo at `github.com/MemPalace/mempalace`; documentation warns against impostor domains. [MemPalace README §Official Resources]

---

## Section 2 — DepthFusion Characterisation (as-built vs as-designed)

### As-Built State (from source)

DepthFusion (Saihai Deep, Python) is a Python MCP server with 40+ source files organised into 14 packages. Current production version is v0.6.x (inferred from `apply_cognitive_scoring` docstring noting "preserves v0.6.x byte-identity"). Key as-built capabilities:

**Retrieval pipeline** (`retrieval/hybrid.py`):
- Three-mode pipeline: `LOCAL` (BM25 only), `VPS_TIER1` (BM25 → Haiku reranker), `VPS_TIER2` (ChromaDB + BM25 → RRF → Haiku reranker). [file:retrieval/hybrid.py:L100-L138]
- Boilerplate penalty (0.2× for pure session envelopes) and lexical richness penalty (0.5–1.0× based on TTR) applied to BM25 scores. [file:retrieval/hybrid.py:L593-L632]
- Project-scoped recall filter: YAML frontmatter `project:` key; back-compat includes no-frontmatter blocks. [file:retrieval/hybrid.py:L515-L590]
- FTS5 pre-filter (`DEPTHFUSION_FTS_ENABLED=true`): reduces full-scan cost. [file:retrieval/hybrid.py:L466-L489]
- Query-feedback boost: `query_hits_boost()` returns 1.0–1.5× multiplier from 30-day HitTracker log. [file:retrieval/hybrid.py:L634-L650]
- Graph-based query expansion (`DEPTHFUSION_GRAPH_ENABLED=true`). [file:retrieval/hybrid.py:L153-L169]

**Fusion gates** (`fusion/gates.py`):
- Mamba B/C/Δ port: B gate (query similarity), C gate (topical coherence), Δ gate (blended threshold). Feature-flagged (`DEPTHFUSION_FUSION_GATES_ENABLED`). [file:fusion/gates.py:L1-L37]
- Every `apply()` call emits a `GateLog` (D-3 invariant); `GateConfig.version_id()` provides I-8-compliant audit hash. [file:fusion/gates.py:L160-L203]
- Fail-open: if gates reject all candidates, returns original pool. [file:retrieval/hybrid.py:L244-L250]

**Storage** (`storage/vector_store.py`):
- `ChromaDBStore` with cosine HNSW; admission gate (`_admission_score`) pre-filters low-quality content at index time. [file:storage/vector_store.py:L29-L43, L83-L91]

**Memory objects** (`core/memory_object.py`):
- Rich `MemoryObject` with `MemoryType` (DECISION/SEMANTIC/OPERATIONAL/PROCEDURAL/EPISODIC/SOCIAL/TEMPORAL), `MemoryStatus` (ACTIVE/STALE/DISPUTED/SUPERSEDED/ARCHIVED), `MemoryValidity` (valid_from/valid_until), `MemoryConfidence` (score + verification_count + contradiction_count). [file:core/memory_object.py:L1-L188]

**Salience decay** (`capture/decay.py`):
- Bucketed daily decay: pinned=0%, importance≥0.8→1%/day, ≥0.5→2%/day, <0.5→5%/day. Hard-archive at salience<0.05. Idempotent (last_decay_date frontmatter). [file:capture/decay.py:L1-L33, L131-L239]

**Feedback loop** (`core/hit_tracker.py`):
- Persistent JSONL log of retrieval hits; 30-day rolling window; 5 MB prune threshold; thread-safe singleton. [file:core/hit_tracker.py:L1-L124]

**Cognitive scorer** (`cognitive/scorer.py`):
- 8-dimensional weighted scoring: semantic(0.25), lexical(0.18), confidence(0.15), regime_match(0.12), graph_proximity(0.10), recency(0.08), historical_usefulness(0.07), workflow_intent(0.05). Feature-flagged. [file:cognitive/scorer.py:L1-L56]

**Knowledge graph** (`graph/store.py`, `capture/auto_learn.py`):
- Three backend tiers: JSONGraphStore (local), SQLiteGraphStore (vps-cpu), ChromaGraphStore (vps-gpu). [file:graph/store.py:L452-L473]
- Entity + edge types; confidence threshold gate (min 0.7 by default). [file:graph/store.py:L18-L30]
- Temporal session linking via `PRECEDED_BY` edges. [file:capture/auto_learn.py:L347-L465]
- Contradiction detection (pairwise, capped at 20 decisions). [file:capture/auto_learn.py:L149-L214]

**Auto-learning** (`capture/auto_learn.py`):
- `HeuristicExtractor` (regex, no API) + `HaikuSummarizer` (opt-in API). LLM decision extractor + negative extractor, embedding-based dedup after write. [file:capture/auto_learn.py:L63-L146]

**MCP server** (`mcp/server.py`):
- 19+ registered tools including: `recall_relevant`, `publish_context`, `auto_learn`, `compress_session`, `graph_traverse`, `graph_status`, `confirm_discovery`, `prune_discoveries`, `set_memory_score`, `recall_feedback`, `tag_session`, `tier_status`. [file:mcp/server.py:L22-L82]

### As-Designed State (from DEPTHFUSION_ARCHITECTURE.md)

Per the canonical architecture doc:
- Python standalone is "Phase C" of Saihai's deployment plan — cross-session memory MCP server, distinct from the TypeScript `@depthfusion/core` stateless scoring library. [DEPTHFUSION_ARCHITECTURE.md §2]
- Palace metaphor described: Wing → Room → Closet → Drawer; Hall (intra-wing); Tunnel (cross-wing). L0/L1/L2/L3 tiered recall. [DEPTHFUSION_ARCHITECTURE.md §6]
- 96.6% R@5 raw / 100% with Haiku rerank stated as design targets. [DEPTHFUSION_ARCHITECTURE.md §6]
- Invariants D-1 (firewall) through D-12 (raw verbatim default) govern both implementations. [DEPTHFUSION_ARCHITECTURE.md §13]

### Drift Between As-Built and As-Designed

| Design feature | As-designed | As-built status |
|---|---|---|
| Wing/Room/Closet/Drawer hierarchy | Defined in arch doc | **Not observed in Python source** — DepthFusion Python uses flat `.tmp` session files + discovery `.md` files in `~/.claude/shared/discoveries/`, not the named room hierarchy |
| L0/L1/L2/L3 tiered recall | Documented as a layered system | BM25 pipeline tiers exist (LOCAL/VPS_TIER1/VPS_TIER2) but the L0–L3 label is absent from the Python source |
| Mamba B/C/Δ gates | TS fully built; Python roadmap | Python B/C/Δ gates **built** in `fusion/gates.py`, feature-flagged as `DEPTHFUSION_FUSION_GATES_ENABLED` |
| 19 MCP tools (arch doc) | 19 tools stated | Source shows **19+** (mcp/server.py TOOLS dict has more entries post v0.5.0) |
| VPS deployment pending | Phase C prerequisite | Still pending in Phase C per platform context |

The most significant drift is the **palace metaphor not implemented** in the Python standalone. The Python tool uses flat file-based storage, not the Wing/Room/Drawer hierarchy described in the architecture doc. This hierarchy appears to have been introduced (or planned) at the level of the TypeScript Core's context manager metaphor, not the Python MCP server.

### Current Memory-Management Surface Area

The Python MCP server manages three storage layers:
1. **Session capture files** (`.tmp` — raw session transcripts in `~/.claude/`)
2. **Discovery files** (`.md` in `~/.claude/shared/discoveries/` — importance/salience-scored with YAML frontmatter)
3. **Graph store** (JSON/SQLite/ChromaDB depending on tier — entity/edge/temporal data)

Lifecycle: capture → dedup → decay (salience) → prune (age/superseded) → archive.

---

## Section 3 — CLaRa Characterisation

CLaRa is the in-session context management subsystem of Saihai Core. It is relevant to this analysis only where it shares boundary responsibilities with DepthFusion or where MemPalace might map to it.

**Six subsystems** [SAIHAI_PLATFORM_CONTEXT.md §CLaRa]:
1. **SelectiveHistoryScorer** — Mamba-3 doubly-adaptive B/C/Δ gates (both A matrix and Δ input-dependent). This is the in-session analogue of DepthFusion's cross-session gates.
2. **AdaptiveTokenAllocator** — Dynamic content-aware budget allocation; system prompt ≥ 15% always (I-5).
3. **SelectiveContextPacker** — Write-gated context packing.
4. **BatchRecompute** — Session-start state bootstrap.
5. **TieredMemoryCache** — Hot/Warm/Cold three-tier cache (in-session).
6. **MultiTimescaleHistoryBuffer** — Fine/Medium/Coarse history compression, pinned message protection.

**CLaRa ↔ Deep interface**: CLaRa provides `ScoredMessage[]`, `BudgetAllocation`, `SessionBlock[]` to `@depthfusion/core`. Deep returns `FusedContextPayload`, `TrajectoryFeedback`, `MaterialisationResult`. [SAIHAI_PLATFORM_CONTEXT.md §CLaRa]

**Key boundary**: CLaRa owns **in-session state**; Deep Python owns **cross-session state**; Deep TS is **stateless** (D-2, I-12). MemPalace comparison must respect this three-way ownership split.

---

## Section 4 — Subsystem Mapping

**Required decision:** MemPalace maps more naturally to **DepthFusion Python** than to CLaRa.

MemPalace is a cross-session persistent memory store with a full write/read/index lifecycle. It stores session history verbatim, builds a temporal knowledge graph, and surfaces content at query time via hybrid BM25+vector search. This is precisely DepthFusion Python's purpose — cross-session memory MCP server with persistent storage (ChromaDB + SQLite), BM25+vector hybrid retrieval, and an evolving knowledge graph.

CLaRa, by contrast, operates purely within a single session: its six subsystems manage token budgets, history compression, in-session cache tiers, and context packing. None of these persist beyond session end. MemPalace has no concept of intra-session token allocation, write-gated context packing, or per-message Mamba gate scoring. [SAIHAI_PLATFORM_CONTEXT.md §CLaRa; file:retrieval/hybrid.py:L100-L138]

There is one partial CLaRa touch: MemPalace's `knowledge_graph.py` temporal modelling (validity windows, `timeline()`) has some conceptual kinship with CLaRa's `MultiTimescaleHistoryBuffer`, but this is a surface similarity. MemPalace's KG is a cross-session persistent fact store, not a compression mechanism for an active context window. [MemPalace `knowledge_graph.py`]

The subsystem mapping is therefore: **MemPalace → DepthFusion Python** for retrieval, storage, and lifecycle management; no CLaRa subsystem owns MemPalace-class functionality.

---

## Section 5 — Comparison Matrix

| Dimension | MemPalace | DepthFusion Python | CLaRa |
|---|---|---|---|
| **Memory representation** | Hybrid: verbatim text chunks (vector + FTS5) + symbolic KG triples in SQLite [MemPalace `knowledge_graph.py`] | Hybrid: raw `.md`/`.tmp` blocks (BM25 + vector) + typed MemoryObject graph (entities + edges) [file:core/memory_object.py:L9-L17; file:graph/store.py:L104-L160] | Symbolic/scalar: ScoredMessage[], BudgetAllocation, compressed history buffers — no vector store [SAIHAI_PLATFORM_CONTEXT.md §CLaRa] |
| **Write path and consolidation** | Miner ingests sessions → embed → ChromaDB upsert; entity extraction → KG; `dedup.py` at write time [MemPalace `miner.py`, `dedup.py`] | `HeuristicExtractor`/`HaikuSummarizer` → decision/negative extractor → embedding dedup → discovery `.md` write; `MemoryConsolidator` for near-duplicate detection [file:capture/auto_learn.py:L217-L298; file:cognitive/consolidator.py:L23-L60] | Per-message scoring + compression on the active context window; no persistent write [SAIHAI_PLATFORM_CONTEXT.md §CLaRa subsystem 3] |
| **Retrieval mechanism and scoring** | BM25 (Lucene formula, FTS5) + ChromaDB cosine; `hybrid_rank()` = 0.6×vector + 0.4×BM25; closet rank boosts [MemPalace `searcher.py`] | BM25 + optional ChromaDB; RRF fusion (k=60); Haiku reranker; CognitiveScorer (8-dim weighted, feature-flagged); query-hits boost (1.0–1.5×) [file:retrieval/hybrid.py:L380-L410; file:cognitive/scorer.py:L1-L56] | SelectiveHistoryScorer: Mamba-3 B/C/Δ gates on ScoredMessage[] for in-session token allocation [SAIHAI_PLATFORM_CONTEXT.md §CLaRa subsystem 1] |
| **Forgetting / decay / eviction** | KG `invalidate()` marks triples as expired; `BaseCollection.delete()` for hard delete; no automatic salience decay in vector store [MemPalace `knowledge_graph.py`] | Bucketed salience decay: 1%/2%/5%/day by importance tier; hard-archive at salience < 0.05; pinned items immune; prune by age (90d default) or `.superseded` suffix [file:capture/decay.py:L1-L33; file:capture/pruner.py:L39-L56] | MultiTimescaleHistoryBuffer compresses Fine→Medium→Coarse by age/recency; pinned messages protected [SAIHAI_PLATFORM_CONTEXT.md §CLaRa subsystem 6] |
| **Temporal modelling** | Full KG temporal: `valid_from/valid_to` on every triple; `timeline()` query; `invalidate()` to close validity windows; `as_of=` point-in-time queries [MemPalace `knowledge_graph.py`] | MemoryValidity on MemoryObject (`valid_from/valid_until`); temporal session linking via `PRECEDED_BY` edges; `first_seen` on entities; recency field in ScoringContext; HitTracker 30-day window [file:core/memory_object.py:L78-L101; file:capture/auto_learn.py:L347-L465] | Session-local only; Fine/Medium/Coarse time horizon in history buffer; no cross-session temporal state [SAIHAI_PLATFORM_CONTEXT.md §CLaRa subsystem 6] |
| **Context-awareness of retrieval** | Palace-scoped (Wing/Room/Drawer) filters queries; closet boost provides hierarchical scoping signal [MemPalace README §Structural Organization; `searcher.py`] | Project-scoped recall filter (YAML frontmatter `project:`); query-detected cross-project mention widening; graph-based query expansion; `DEPTHFUSION_SET_SCOPE` tool [file:retrieval/hybrid.py:L540-L590, L676-L697] | Budget-aware packing per query; AdaptiveTokenAllocator ensures system ≥ 15%; slice assignment respects CLaRa→Deep boundary [SAIHAI_PLATFORM_CONTEXT.md §CLaRa subsystem 2] |
| **Token budget management** | No token budget awareness — returns `top_k` results, no slice management [MemPalace README] | Returns `top_k` blocks with `snippet_len` truncation; `depthfusion_status` reports corpus size; no formal budget slice contract [file:mcp/server.py:L22-L35] | Formal: AdaptiveTokenAllocator + D-7 invariant (never requests more than allocated slice) [SAIHAI_PLATFORM_CONTEXT.md §CLaRa subsystem 2; DEPTHFUSION_ARCHITECTURE.md §13 D-7] |
| **Self-improvement / learning from use** | No explicit feedback loop — retrieval history not persisted for future ranking influence [MemPalace README, `searcher.py`] | `HitTracker`: 30-day query-feedback boost (1.0–1.5× per chunk); `recall_feedback` MCP tool: +0.1/−0.05 salience delta on used/ignored chunks; contradiction detection [file:core/hit_tracker.py; file:mcp/server.py:L75-L82] | TrajectoryAnalyser → TrajectoryFeedback fed back to TS Core's strategy selection; no persistent cross-session learning [DEPTHFUSION_ARCHITECTURE.md §5] |
| **Model-agnosticism (Skill IR firewall)** | No Skill IR concept — directly calls LLM APIs for reranking (opt-in); backs ChromaDB and sentence-transformers; no provider abstraction required by design [MemPalace README] | D-1 invariant: zero knowledge of model identities at the library level; `get_backend("embedding")` / `get_backend("summariser")` abstract provider; `DEPTHFUSION_API_KEY` over `ANTHROPIC_API_KEY` [DEPTHFUSION_ARCHITECTURE.md §13 D-1; file:capture/auto_learn.py:L1-L13] | Full I-1 Skill IR firewall: nothing above Layer 2 knows about models; nothing below knows about org structure [SAIHAI_PLATFORM_CONTEXT.md §I-1] |
| **Statefulness and persistence substrate** | ChromaDB (HNSW vector index) + SQLite (KG triples, FTS5) — persistent local files [MemPalace README §Core Architecture] | ChromaDB (HNSW, `~/.claude/.depthfusion_vectors`) + SQLite (graph, `~/.claude/depthfusion-graph.db`) + flat `.md`/`.tmp` files in `~/.claude/` [file:storage/vector_store.py:L11; file:graph/store.py:L13, L167-L168] | Entirely in-memory (CLaRa is session-scoped); TypeScript Core is stateless (D-2, I-12) [DEPTHFUSION_ARCHITECTURE.md §13 D-2; SAIHAI_PLATFORM_CONTEXT.md §I-12] |
| **Observability and introspection** | `stats()` on KG (entity/triple counts); MCP tools report palace structure; no structured gate logs [MemPalace `knowledge_graph.py`] | `GateLog` per query (D-3, I-8); `GateConfig.version_id()` for audit hash; `depthfusion_tier_status`; `depthfusion_graph_status`; `MetricsCollector.record_gate_log()`; hit log at `~/.claude/.depthfusion_hits.jsonl` [file:fusion/gates.py:L181-L203; file:retrieval/hybrid.py:L227-L240] | All CLaRa + Deep gate decisions logged, none silent (I-8); gate log mandatory (D-3) [SAIHAI_PLATFORM_CONTEXT.md §I-8; DEPTHFUSION_ARCHITECTURE.md §13 D-3] |

---

## Section 6 — Gap Analysis

For each matrix dimension, the analysis asks: (1) does MemPalace do something DF/CLaRa doesn't? (2) is it valuable to Saihai's use cases? (3) invariant impact?

### Memory representation
(1) MemPalace uses ChromaDB + SQLite KG in the same project (same architectural choice as DepthFusion). There is no novel representation. MemPalace's KG stores named triples with provenance (`source_closet`, `source_file`, `source_drawer_id`, `adapter_name`) — richer provenance than DepthFusion's `MemorySource` (which has `session_id` + `file_path` but no adapter_name). [MemPalace `knowledge_graph.py`; file:core/memory_object.py:L28-L43]
(2) Richer provenance could improve auditability of cross-session claims.
(3) No invariant impact.

### Write path and consolidation
(1) MemPalace does not expose an explicit consolidation pass analogous to DepthFusion's `MemoryConsolidator` (near-duplicate detection at 0.92 token similarity). MemPalace's `dedup.py` exists but is at-write only. DepthFusion additionally has: semantic embedding-based dedup, salience-weighted consolidation, `.superseded` suffix lifecycle management. DepthFusion is strictly more capable here.
(2) N/A — DF leads.
(3) No invariant impact.

### Retrieval mechanism and scoring
(1) MemPalace's fixed `0.6 × vector + 0.4 × BM25` weight is **simpler and well-calibrated against the LongMemEval benchmark** (96.6% R@5). DepthFusion uses RRF (k=60) rather than a fixed linear blend. The MemPalace approach of normalising BM25 within the candidate set while using absolute vector similarity prevents score-collapse when adding unrelated candidates — this is a property RRF does not guarantee. [MemPalace `searcher.py`; file:retrieval/hybrid.py:L380-L410]
(2) The BM25-relative / vector-absolute split is a calibration insight worth noting — DepthFusion's RRF approach is theoretically sound but the MemPalace team has empirically tuned these weights on a specific retrieval benchmark. This is informative but not a direct gap.
(3) No invariant impact.

### Forgetting / decay / eviction
(1) MemPalace uses explicit triple `invalidate()` with temporal closure (`valid_to`) rather than soft salience decay. This is a **semantically distinct** forgetting model — MemPalace's KG facts can be "closed" without being deleted, which allows point-in-time reconstruction. DepthFusion's salience decay and hard-archive do not provide point-in-time reconstruction: once salience decays below 0.05, the file is moved to `.archive/` and removed from active retrieval, with no preserved prior state.
(2) For Saihai's use case — an AI COO needing accurate historical task/decision records — the point-in-time reconstruction capability of MemPalace's KG `as_of=` query is genuinely valuable. A query like "what decisions were active in the project in week 3 of the sprint" is not expressible in DepthFusion Python today.
(3) No invariant conflict. Strengthens Skill IR (richer historical context without model exposure).

### Temporal modelling
(1) MemPalace has first-class `valid_from/valid_to` on every triple, `as_of=` point-in-time filtering, and `timeline()` ordered fact retrieval. DepthFusion's `MemoryValidity` dataclass has `valid_from/valid_until` fields [file:core/memory_object.py:L79-L83] but they are **not plumbed into the retrieval pipeline** — the BM25 pipeline has no `as_of=` parameter, and `timeline_pass()` in `retrieval/hybrid.py` orders by mtime but does not honour `valid_from/valid_until` filtering [file:retrieval/hybrid.py:L442-L463].
(2) High value for Saihai — the `MemoryValidity` infrastructure is already present in the data model but inactive in retrieval. This is a gap where MemPalace's implementation provides a proof-of-concept path.
(3) No invariant conflict.

### Context-awareness of retrieval
(1) MemPalace's palace scoping (Wing/Room/Drawer hierarchy) is more expressive than DepthFusion's flat project-tag filter. A Wing can contain multiple rooms; a closet boost signals topical adjacency within a room. DepthFusion has only project-level scoping. [MemPalace README §Structural Organization; file:retrieval/hybrid.py:L540-L590]
(2) For Saihai multi-agent scenarios — where different agents work on different features within a project — sub-project scoping would reduce cross-contamination. However, DepthFusion's `detect_mentioned_projects()` provides dynamic widening, which partially compensates.
(3) No invariant conflict. Could strengthen Skill IR isolation if scoping is by Skill IR layer rather than by project.

### Token budget management
(1) MemPalace has no token budget awareness. DepthFusion has `snippet_len` truncation and corpus size reporting. Neither DepthFusion Python nor MemPalace has a formal D-7-compliant budget slice contract — that belongs to CLaRa + Deep TS exclusively.
(2) Not a gap in MemPalace relative to DepthFusion Python; both are pre-CLaRa in the token budget hierarchy.
(3) No invariant impact.

### Self-improvement / learning from use
(1) MemPalace has **no retrieval feedback loop**. DepthFusion's `HitTracker` (query-frequency boost) and `recall_feedback` (explicit salience delta) are unique to DepthFusion and not present in MemPalace. DepthFusion leads significantly here.
(2) N/A — DF leads.
(3) No invariant impact.

### Model-agnosticism (Skill IR firewall compliance)
(1) MemPalace's design assumes direct LLM access (opt-in but structurally unconstrained). `llm_client.py` and `llm_refine.py` are core modules with no provider abstraction requirement. DepthFusion enforces D-1 (zero model knowledge in the library) via the `get_backend()` factory. [file:capture/auto_learn.py:L1-L13]
(2) MemPalace's architecture would violate D-1/I-1 if integrated above Layer 2 of Saihai without a shim. This is the strongest architectural incompatibility.
(3) **Weakens** Skill IR firewall if MemPalace code or patterns are adopted without wrapping behind the `get_backend()` factory. Requires explicit isolation.

### Statefulness and persistence substrate
(1) Identical storage stack (ChromaDB + SQLite). No gap.
(2) N/A.
(3) No invariant impact.

### Observability and introspection
(1) MemPalace has no structured gate logs and no audit hash. DepthFusion's `GateLog` / `GateConfig.version_id()` / `MetricsCollector.record_gate_log()` fully implement D-3/I-8 with reproducible audit trails. DepthFusion leads.
(2) N/A — DF leads.
(3) No invariant impact.

---

## Section 7 — Verdict

**Classification: Partial overlap, net-positive**

MemPalace is not superseded — it has a significantly larger user base (52K stars vs DepthFusion's private deployment), active community maintenance, a well-validated retrieval benchmark (LongMemEval 96.6% raw), and two concrete technical gaps DepthFusion does not fully address: **first-class point-in-time temporal retrieval** and **hierarchical sub-project scoping**. These are net-positive relative to Saihai's needs.

MemPalace is not orthogonal — both systems are cross-session persistent memory MCP servers with near-identical storage stacks (ChromaDB + SQLite) serving the same use case (augmenting Claude Code with long-term memory). The overlap in retrieval architecture is substantial: both use BM25 + vector hybrid search, both use ChromaDB for vectors, both use SQLite for structured data. The surface metaphors differ (palace hierarchy vs flat project scoping) but the underlying data operations are the same.

The net-positive areas are specific and bounded. The MemPalace KG's `valid_from/valid_to`/`as_of=` pattern provides a proof-of-concept for a gap that already exists in DepthFusion's data model (`MemoryValidity` is defined in `core/memory_object.py` but not plumbed into the retrieval pipeline). DepthFusion already has the data model scaffolding; what's missing is wiring `valid_from/valid_until` into `filter_blocks_by_project()` and the BM25 scoring path to enable point-in-time queries. MemPalace's implementation provides a concrete reference for what this looks like in SQLite (parameterised `_temporal_filter_sql()` helpers).

The hierarchical scoping gap (Wing/Room/Drawer vs flat project tag) is lower priority for Saihai's current phase, as DepthFusion's dynamic project mention detection partially compensates.

One architectural concern must be noted: MemPalace's `llm_client.py` / `llm_refine.py` modules assume direct LLM API access with no Skill IR firewall. Any adoption of MemPalace patterns that touches LLM calls must be wrapped behind DepthFusion's `get_backend()` factory to maintain D-1 / I-1 compliance. This is a manageable constraint, not a blocker, but it is non-trivial.

---

## Section 8 — Integration Proposal

### Tier 1 — Build-now: clear value, no sub-documents needed

**T1-A: Wire `MemoryValidity` into the BM25 recall pipeline**
- **Description:** DepthFusion's `MemoryObject.validity.valid_from/valid_until` are defined but not used in retrieval. Add an optional `as_of: datetime` parameter to `filter_blocks_by_project()` (or a sibling function) that excludes blocks whose `valid_until < as_of` or `valid_from > as_of`. Reference: MemPalace's `_temporal_filter_sql()` in `knowledge_graph.py`.
- **Target subsystem:** `retrieval/hybrid.py` + `capture/decision_extractor.py` (to write `valid_from` into discovery file frontmatter)
- **Expected benefit:** Enables point-in-time recall — "what was known about X on date Y". Closes the gap between the as-defined `MemoryValidity` type and its actual use.
- **Risk:** Low. Additive — existing callers without `as_of` continue to behave identically.
- **Dependencies:** None beyond existing `MemoryValidity` dataclass.
- **Invariant impact:** None. Strengthens historical fidelity without touching the Skill IR boundary.
- **Status:** Proposed

**T1-B: Adopt MemPalace's BM25-relative / vector-absolute normalisation approach**
- **Description:** DepthFusion's RRF uses rank-based fusion that does not distinguish the relative vs absolute nature of the two signals. MemPalace normalises BM25 within the candidate set (min-max, relative) and uses absolute cosine similarity. DepthFusion's existing `_bm25_percentile()` in `fusion/gates.py` is already a relative normalisation helper — extend this pattern to the BM25+vector blend weight in `VPS_TIER2` mode, documenting the rationale against the LongMemEval evidence.
- **Target subsystem:** `retrieval/hybrid.py` `rrf_fuse()` or a new `linear_blend()` function
- **Expected benefit:** Prevents score-collapse when adding unrelated candidates to the retrieval pool; aligns with benchmark-validated weights.
- **Risk:** Low-medium. Changes score distributions; existing tests should be updated with expected outputs.
- **Dependencies:** None.
- **Invariant impact:** None.
- **Status:** Proposed

**T1-C: Add triple provenance fields to DepthFusion's KG edge schema**
- **Description:** MemPalace's KG triples carry `source_closet`, `source_file`, `source_drawer_id`, `adapter_name`. DepthFusion's `Edge` type has a `metadata` dict that can absorb these, but they are not standardised. Add `adapter_name` and `source_type` as first-class fields to `graph/types.py`'s `Edge` dataclass, populated by decision/negative extractors.
- **Target subsystem:** `graph/types.py`, `graph/store.py`, `capture/decision_extractor.py`
- **Expected benefit:** Improved auditability of which capture path produced a given graph edge.
- **Risk:** Low. Additive schema change; existing edges remain valid with empty fields.
- **Dependencies:** None.
- **Invariant impact:** None.
- **Status:** Proposed

### Tier 2 — Requires sub-document first

**T2-A: Sub-project scoping (Wing/Room metaphor port)**
- **Description:** MemPalace's Wing/Room hierarchy enables scoped retrieval below the project level. DepthFusion currently has project-level scoping only. Porting this would require: (1) a sub-project scope concept in frontmatter, (2) extending `filter_blocks_by_project()` with an optional `sub_scope` parameter, (3) a `depthfusion_set_scope` extension to accept Wing/Room-style namespaces.
- **Target subsystem:** `retrieval/hybrid.py`, `mcp/server.py`
- **Expected benefit:** Reduces cross-contamination in multi-agent scenarios where multiple agents work on different subsystems of the same project.
- **Risk:** Medium. Scope taxonomy must be agreed before implementation; risk of over-engineering.
- **Dependencies:** Requires an ADR on scope taxonomy (project / sub-project / global). Blocks on OD-3 (Python ↔ TS coordination).
- **Invariant impact:** None if scoping only applies within the Python layer.
- **Status:** Proposed — requires ADR first

**T2-B: KG `invalidate()` for supersession (complement to `.superseded` suffix)**
- **Description:** DepthFusion marks superseded discoveries with a `.superseded` file suffix. MemPalace's KG `invalidate()` closes the `valid_to` window instead, preserving the original fact for point-in-time queries. Introducing a KG-level invalidation path (complementing the file suffix) would allow retrieval to reconstruct "what was believed about X before supersession".
- **Target subsystem:** `graph/store.py`, `capture/dedup.py`
- **Expected benefit:** Full point-in-time KG consistency. Superseded discoveries remain queryable via `as_of=` without being physically present in the active discovery directory.
- **Risk:** Medium. Requires careful state machine design — the KG invalidation and file-system supersession paths must stay consistent.
- **Dependencies:** T1-A (wire MemoryValidity into retrieval) must be in place first.
- **Invariant impact:** None.
- **Status:** Proposed — requires design doc

### Tier 3 — Speculative / high-upside

**T3-A: Benchmark DepthFusion against LongMemEval**
- **Description:** MemPalace publishes 96.6% R@5 on LongMemEval (500 questions), 98.4% hybrid v4, ≥99% with LLM reranking. DepthFusion's architecture doc claims 96.6% / 100% but the benchmark methodology is not documented in the Python source. Running a reproducible LongMemEval evaluation would either validate the existing claim or surface specific retrieval gaps that Tier 1 proposals should address.
- **Target subsystem:** `docs/benchmarks/` + test harness
- **Expected benefit:** Objective performance baseline; direct comparison against MemPalace's published numbers.
- **Risk:** High effort (evaluation harness construction); low risk of breakage.
- **Dependencies:** LongMemEval dataset access; standardised evaluation script.
- **Invariant impact:** None.
- **Status:** Proposed

**T3-A: MemPalace community as external benchmark signal**
- **Description:** MemPalace's 551 open issues and active community represent a large empirical signal about what fails in production memory systems. Periodically reviewing MemPalace's issue tracker for failure modes (embedder identity mismatch, corrupt vector segments, UTF-8 lock holder issues, None metadata cells) could surface pre-emptive hardening opportunities for DepthFusion.
- **Target subsystem:** Process / engineering hygiene, not code
- **Expected benefit:** Early warning on failure modes that affect the shared ChromaDB + SQLite architecture.
- **Risk:** None.
- **Dependencies:** None.
- **Invariant impact:** None.
- **Status:** Proposed

---

## Section 9 — Open Questions for Greg

1. **OD-3 resolution affects scoping:** The as-built Python standalone does not implement the Wing/Room/Closet/Drawer hierarchy described in the architecture doc. Should the Python MCP server adopt hierarchical scoping aligned with the architecture doc, or is flat project-tag scoping the intended long-term design? This directly affects whether T2-A is in scope.

2. **LongMemEval benchmark provenance:** The architecture doc asserts "96.6% R@5 raw" and "100% with Haiku rerank" for the Python standalone. What corpus and evaluation script produced these numbers? Are they reproducible from the current codebase, or were they produced from an earlier version?

3. **MemoryValidity wiring intent:** `MemoryValidity` (with `valid_from`/`valid_until`) exists in `core/memory_object.py` but is not wired into any retrieval path. Was this designed as future infrastructure or is it actively used somewhere not observed in the read files? If future infrastructure, T1-A formalises the intent.

4. **KG temporal modelling scope:** DepthFusion has `PRECEDED_BY` temporal edges between sessions and `MemoryValidity` on MemoryObject, but no `as_of=` query capability. Is the intent for the Python KG to eventually support point-in-time querying, or is the KG used only for current-state entity lookups?

5. **MemPalace licence:** The MemPalace licence was not confirmed in the data read. Before adopting any MemPalace implementation patterns (especially `searcher.py`'s hybrid scoring or `knowledge_graph.py`'s temporal SQL helpers), confirm the licence permits commercial derivative use.

6. **Model-agnosticism in MemPalace integration:** If MemPalace's `llm_client.py` / `llm_refine.py` patterns are ever evaluated for adoption, how should they be wrapped to satisfy D-1 (zero model knowledge in the retrieval library)? Is the `get_backend()` factory the intended shim layer, or would a separate provider-agnostic adapter be required?

7. **CIQS Cat A implications:** The recent CIQS benchmark work (commits `33d0d54`, `11081ef`) improved Cat A retrieval from 18.3% → 40.0%. MemPalace's LongMemEval benchmark tests a different retrieval scenario (long conversation history). Is there a mapping between MemPalace's benchmark dimensions and DepthFusion's CIQS categories, and would Tier 1 proposals affect the CIQS scores?

8. **Contradictions between DEPTHFUSION_ARCHITECTURE.md and as-built state:** The architecture doc describes a Wing/Room/Closet/Drawer hierarchy in §6 that is absent from the Python source. Should the architecture doc be updated to reflect the as-built flat-file design, or is the hierarchy a planned addition not yet implemented?
