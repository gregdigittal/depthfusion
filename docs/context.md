# DepthFusion — Project Context Document

> **Generated:** 2026-04-16 | **Version:** v0.3.1 (in progress) / v0.4.0 (knowledge graph shipped)
> **Owner:** Greg Morris (gregm@tonracein.com) | **Repo:** github.com/gregdigittal/depthfusion
> **Purpose:** Drop this into any Claude session (Desktop, Code, or Max) for full project orientation.

---

## 1. What DepthFusion Is

**Cross-session memory for Claude Code.** A Python library that gives Claude Code persistent, retrievable memory across sessions using tiered retrieval:

```
Local mode:    BM25 keyword scoring (zero deps, offline)
VPS Tier 1:    BM25 top-10 → Haiku semantic reranker → top-k
VPS Tier 2:    ChromaDB vectors (top-20) + BM25 (top-10) → RRF fusion → Haiku reranker → top-k
```

It solves the problem that Claude Code forgets everything between sessions — architecture decisions, error patterns, project-specific conventions, and what was built yesterday.

**Measured impact (CIQS benchmark):**

| Version | CIQS Overall | Category A (retrieval) | Category D (continuity) |
|---------|-------------|----------------------|------------------------|
| Vanilla Claude Code | ~76.5 | — | — |
| v0.3.0 local | ~85 | BM25 keyword | 42% |
| v0.3.1 (projected) | 88–90 | BM25 + RRF + recency | 55–65% |
| v0.4.0 + graph | 93+ (projected) | + entity expansion | + graph-augmented |

---

## 2. Current State

**What's shipped:**
- Core retrieval (BM25, RRF fusion, weighted fusion, Haiku reranker)
- Session processing (tagger, loader, scorer, compactor)
- Context routing (pub/sub bus with cost ceilings)
- Knowledge graph (entity extraction, edge linking, traversal — 3 linker types)
- MCP server with 10 tools (feature-flag gated) — registered in Claude Desktop
- Authentication isolation (DEPTHFUSION_API_KEY, not ANTHROPIC_API_KEY)
- Local↔VPS discovery sync (`sync.sh`)
- v0.3.1 scoring fixes (BM25 normalization, 1500-char snippets, RRF wiring, SessionStart hook, PostCompact hook, source classification)

**What's pending (v0.3.1 release):**
- Sentence-boundary snippet trimming (S-25 AC-2)
- CIQS 3-run post-fix validation (S-30) — target ≥88 overall, Category D ≥55%
- mypy + ruff clean pass
- Git tag `v0.3.1`

**Test suite:** 439 test functions across 41 files. 412+ GREEN, 2 skipped (ChromaDB not installed).

---

## 3. Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python ≥3.10 |
| Core deps | numpy ≥1.24, pyyaml ≥6.0, structlog ≥24.0 |
| Optional (VPS) | anthropic ≥0.40 (Haiku reranker), chromadb ≥0.4 (Tier 2 vectors) |
| Optional (recursive) | rlms (from `~/Development/Projects/rlm/`) |
| Dev tools | pytest ≥8.0, pytest-cov ≥5.0, mypy ≥1.0, ruff ≥0.4 |
| Integration | Claude Desktop MCP (stdio), Claude Code hooks (SessionStart, PostCompact) |
| Deployment | Hetzner VPS (gregmorris), Tailscale SSH, tmux sessions |

---

## 4. File Structure with Paths

### Root

```
/home/gregmorris/projects/depthfusion/
├── README.md                              # Main docs (architecture, install, MCP tools, feature flags)
├── BACKLOG.md                             # Agile backlog (17 epics, 40+ stories, 114 tasks)
├── AGENTS.md                              # Agent config for Antigravity/Claude Code sessions
├── pyproject.toml                         # Package config (v0.3.0), Python ≥3.10
├── setup.sh                               # Installation script (local/VPS mode)
└── sync.sh                                # Bidirectional local↔VPS discovery sync
```

### Source Code — `src/depthfusion/`

```
src/depthfusion/
│
├── core/                                  # Typed primitives — shared vocabulary
│   ├── __init__.py
│   ├── types.py                           # RetrievedChunk, SessionBlock, ContextItem, FeedbackEntry
│   ├── config.py                          # 11 env-var feature flags (from ~/.claude/depthfusion.env)
│   ├── scoring.py                         # cosine_sim(), softmax(), weighted_combine()
│   └── feedback.py                        # JSONL relevance feedback persistence
│
├── retrieval/                             # Search — query → candidates
│   ├── __init__.py
│   ├── bm25.py                            # BM25 scorer (k1=1.5, b=0.75, Robertson IDF, offline)
│   ├── reranker.py                        # Haiku semantic reranker (VPS Tier 1+ only)
│   └── hybrid.py                          # Pipeline orchestrator: LOCAL | TIER1 | TIER2 paths
│
├── fusion/                                # Ranking — candidates → ranked results
│   ├── __init__.py
│   ├── rrf.py                             # Reciprocal Rank Fusion (k=60, pure function)
│   ├── weighted.py                        # AttnRes attention-weighted fusion
│   ├── block_retrieval.py                 # K-means cluster-aware block ranking
│   └── reranker.py                        # Score combiner protocol
│
├── session/                               # Session file processing
│   ├── __init__.py
│   ├── loader.py                          # Reads SessionBlocks from .tmp + .meta.yaml
│   ├── tagger.py                          # Writes .meta.yaml sidecars (C1-safe, never modifies source)
│   ├── scorer.py                          # Scores SessionBlocks by relevance
│   └── compactor.py                       # Task-aware session compaction
│
├── router/                                # Context routing — pub/sub bus
│   ├── __init__.py
│   ├── bus.py                             # InMemoryBus + FileBus implementations
│   ├── publisher.py                       # Publishes ContextItems
│   ├── subscriber.py                      # Subscribes to ContextItems
│   ├── dispatcher.py                      # Routes items with cost ceiling enforcement
│   └── cost_estimator.py                  # Token cost tracking for routing budget
│
├── recursive/                             # Recursive LLM integration (optional)
│   ├── __init__.py
│   ├── client.py                          # rlm client wrapper
│   ├── sandbox.py                         # Sandboxed execution environment
│   ├── strategies.py                      # 4 presets: peek, summarize, grep, full
│   └── trajectory.py                      # Execution trajectory tracking
│
├── mcp/                                   # Claude Desktop MCP server
│   ├── __init__.py
│   └── server.py                          # 637 lines, 10 tools (feature-flag gated)
│       # Always: depthfusion_status, recall_relevant, tag_session,
│       #         tier_status, auto_learn, compress_session
│       # Gated:  publish_context (router), run_recursive (rlm),
│       #         graph_traverse, graph_status, set_scope (graph)
│
├── capture/                               # Auto-learning from sessions
│   ├── __init__.py
│   ├── auto_learn.py                      # HeuristicExtractor (6 regex patterns) + HaikuExtractor
│   └── compressor.py                      # .tmp → discovery markdown files (idempotent)
│
├── storage/                               # Vector storage (Tier 2)
│   ├── __init__.py
│   ├── vector_store.py                    # ChromaDB wrapper
│   └── tier_manager.py                    # Corpus size tracking + auto-promotion
│
├── graph/                                 # Knowledge graph (v0.4.0)
│   ├── __init__.py
│   ├── types.py                           # Entity, Edge, GraphScope, TraversalResult
│   │                                      # 8 entity types: class, function, file, concept,
│   │                                      #   project, decision, error_pattern, config_key
│   ├── scope.py                           # Session scope: project | cross_project | global
│   ├── store.py                           # JSONGraphStore (local) + SQLiteGraphStore (VPS) + factory
│   ├── extractor.py                       # RegexExtractor (1.0) + HaikuExtractor (0.70–0.95)
│   ├── linker.py                          # CoOccurrenceLinker + TemporalLinker (48h) + HaikuLinker
│   └── traverser.py                       # traverse(entity, depth), expand_query(), boost_scores()
│
├── metrics/                               # Observability
│   ├── __init__.py
│   ├── collector.py                       # JSONL metrics writer
│   └── aggregator.py                      # Human-readable digest
│
├── install/                               # Installation & migration
│   ├── __init__.py
│   ├── install.py                         # CLI installer (local/VPS mode)
│   └── migrate.py                         # Tier 1 → Tier 2 (indexes into ChromaDB)
│
└── analyzer/                              # Compatibility checking
    ├── __init__.py
    ├── compatibility.py                   # C1-C11 constraint checker (10 GREEN, 1 YELLOW)
    ├── scanner.py                         # Scans ~/.claude/ installation
    ├── recommender.py                     # Checks → actionable steps
    └── installer.py                       # Executes or simulates install
```

### Tests — `tests/`

```
tests/
├── test_core/                             # types, config, scoring, feedback
├── test_retrieval/                        # bm25 (incl. length-norm regression), reranker, hybrid, snippet_trimming
├── test_fusion/                           # rrf, weighted, block_retrieval, reranker
├── test_session/                          # loader, tagger, scorer, compactor
├── test_router/                           # bus, publisher_subscriber, dispatcher, cost_estimator
├── test_recursive/                        # client, sandbox, strategies, trajectory
├── test_analyzer/                         # compatibility, mcp_server, metrics, recommender_installer, scanner
├── test_capture/                          # auto_learn, compressor
├── test_install/                          # install
├── test_storage/                          # tier_manager, vector_store
├── test_graph/                            # conftest, types, scope, store, extractor, linker, traverser
├── test_benchmarks/                       # fusion_benchmark
└── test_integration/                      # (placeholder)
```

### Documentation — `docs/`

```
docs/
├── context.md                             # ← THIS FILE
├── honest-assessment-2026-03-28.md        # CIQS benchmarking, corpus analysis, bottleneck identification
├── performance-measurement-prompt.md      # 5-category CIQS benchmark protocol (17KB)
├── skillforge-integration-plan.md         # 5 seams, 28 modules assessed (14 PORT / 10 SIDECAR / 2 WRAP)
├── release-process.md                     # Pre-tag checklist and tagging workflow
├── sync-guide.md                          # Local ↔ VPS sync instructions
├── depthfusion-vs-alternatives-2026-03-28.md  # Competitive comparison
├── power-user-research-2026-03-28.md      # User research findings
├── MEMPALACE DEPTHFUSION ANALYSIS PROMPT.pdf  # Untracked analysis prompt (needs triage)
├── superpowers/plans/
│   ├── 2026-03-28-depthfusion-v0.3.0.md
│   └── 2026-03-28-depthfusion-v0.4.0-knowledge-graph.md
├── superpowers/specs/
│   ├── 2026-03-28-depthfusion-v0.3.0-design.md
│   └── 2026-03-28-depthfusion-v0.4.0-design.md
└── Account_synch/                         # Canonical planning & continuity docs
    ├── CLAUDE.md                          # Sync-target CLAUDE.md for fresh sessions
    ├── AGENTS.md                          # Sync-target AGENTS.md
    ├── depthfusion-build-plan.md          # Master build plan (v0.3.1 → v0.4.0)
    ├── depthfusion-mega-prompt.md         # Enterprise build sprint bootstrapper
    ├── depthfusion-sprint-backlog.md      # Sprint tracking
    ├── master-continuity-document.md      # Cross-project continuity (all Greg's projects)
    └── depthfusion-scaffold.sh            # Project scaffolding script
```

---

## 5. Architecture Deep Dive

### Feature Flags (11 total, from `~/.claude/depthfusion.env`)

| Flag | Values | Controls |
|------|--------|----------|
| `DEPTHFUSION_MODE` | `local` \| `vps` | Pipeline mode selection |
| `DEPTHFUSION_FUSION_ENABLED` | bool | RRF fusion in retrieval |
| `DEPTHFUSION_SESSION_ENABLED` | bool | Session processing subsystem |
| `DEPTHFUSION_RLM_ENABLED` | bool | Recursive LLM tools |
| `DEPTHFUSION_ROUTER_ENABLED` | bool | Pub/sub context routing |
| `DEPTHFUSION_GRAPH_ENABLED` | bool | Knowledge graph tools (default true since v0.4.0) |
| `DEPTHFUSION_HAIKU_ENABLED` | bool | Haiku reranker/extractor |
| `DEPTHFUSION_TIER_THRESHOLD` | int | Session count triggering Tier 2 (default 500) |
| `DEPTHFUSION_TIER_AUTOPROMOTE` | bool | Auto-migrate to Tier 2 at threshold |
| `DEPTHFUSION_METRICS_ENABLED` | bool | JSONL metrics collection |
| `DEPTHFUSION_API_KEY` | string | Anthropic key for Haiku (NOT `ANTHROPIC_API_KEY`) |

### MCP Tools (Claude Desktop Integration)

| Tool | Gate | Purpose |
|------|------|---------|
| `depthfusion_status` | always | System status and configuration |
| `depthfusion_recall_relevant` | always | BM25/fusion retrieval against memory corpus |
| `depthfusion_tag_session` | always | Write .meta.yaml sidecar for current session |
| `depthfusion_tier_status` | always | Current tier + corpus stats |
| `depthfusion_auto_learn` | always | Extract facts from session via heuristics/Haiku |
| `depthfusion_compress_session` | always | Compact session content |
| `depthfusion_publish_context` | router | Publish ContextItem to bus |
| `depthfusion_run_recursive` | rlm | Execute recursive reasoning step |
| `depthfusion_graph_traverse` | graph | Traverse entity graph from a starting point |
| `depthfusion_graph_status` | graph | Graph stats (entities, edges, stores) |
| `depthfusion_set_scope` | graph | Set graph scope (project/cross-project/global) |

### Claude Code Hooks

| Hook | Trigger | What it does |
|------|---------|-------------|
| `depthfusion-session-init.sh` | SessionStart | Injects git log, branch, BACKLOG head, BM25 memory recall, graph status |
| `depthfusion-pre-compact.sh` | PreCompact | Snapshots active plan path for post-compact extraction |
| `depthfusion-post-compact.sh` | PostCompact | Extracts facts → `~/.claude/shared/discoveries/{stem}-autocapture.md` |

### Scoring Pipeline

```
1. BM25 retrieves candidates (k1=1.5, b=0.75 Robertson IDF, length-normalized)
2. Source weights applied: memory=1.0, discovery=0.85, session=0.70
3. Recency tie-breaking for LOCAL/TIER1 modes
4. [Tier 2 only] RRF fusion when ChromaDB + BM25 signals available
5. [VPS only] Haiku semantic reranker produces final top-k
```

### Authentication Safety

DepthFusion's Haiku features use `DEPTHFUSION_API_KEY` loaded from `~/.claude/depthfusion.env` — **never** `ANTHROPIC_API_KEY`. This prevents Pro/Max subscribers from accidentally flipping Claude Code to pay-per-token billing.

---

## 6. Backlog Summary — Epics

| Epic | Status | Description |
|------|--------|-------------|
| E-01: Core Retrieval Foundation | **done** | BM25, RRF, weighted fusion, block retrieval |
| E-02: Session Processing | **done** | Tagger, loader, scorer, compactor |
| E-03: Context Routing | **done** | Pub/sub bus, dispatcher, cost estimator |
| E-04: Recursive LLM | **done** | rlm client, sandbox, strategies, trajectory |
| E-05: MCP Server | **done** | 10 tools, feature-flag gated, Claude Desktop registered |
| E-06: Auto-Capture | **done** | Heuristic + Haiku extraction, compressor |
| E-07: Metrics | **done** | JSONL collector + aggregator |
| E-08: Tiered Storage | **done** | ChromaDB Tier 2, tier manager, auto-promote |
| E-09: Installation | **done** | CLI installer, compatibility checker (C1-C11) |
| E-10: Haiku Reranker | **done** | Semantic reranking (VPS only) |
| E-11: Knowledge Graph (v0.4.0) | **done** | Entity extraction, 3 linkers, traverser, 3 store backends |
| E-12: Auth & Billing Safety | **done** | DEPTHFUSION_API_KEY isolation |
| E-13: Local↔VPS Sync | **done** | Bidirectional discovery sync |
| E-14: CIQS Data-Gap Closure (v0.3.1) | **active** | 6/6 fixes implemented, CIQS validation pending |
| E-15: Performance Measurement | **active** | CIQS protocol documented, automation pending |
| E-16: SkillForge Integration | **backlog** | 5 seams, 28 modules assessed, awaiting approval |
| E-17: Tech Debt | **backlog** | ChromaDB graph backend, confidence thresholding |

---

## 7. Future Feature Set — Claude Desktop Focus

These are the features planned for implementation via **Claude Desktop's MCP integration**, extending DepthFusion's capabilities beyond the current CLI-only hooks.

### 7a. Planned MCP Tool Enhancements

**Interactive recall tuning** — Currently `depthfusion_recall_relevant` returns top-k results with no feedback loop. Planned: add a `depthfusion_rate_result` tool that accepts relevance feedback (thumbs up/down on returned chunks) and feeds it back into `core/feedback.py` to adjust future scoring weights. This closes the learning loop directly from Claude Desktop conversations.

**Graph-aware recall** — The `depthfusion_graph_traverse` tool currently operates independently from `depthfusion_recall_relevant`. Planned: a unified `depthfusion_smart_recall` tool that combines BM25 keyword retrieval with graph entity expansion in a single call. Query "authentication" → graph expands to `JWT`, `session`, `middleware` → BM25 searches all terms → RRF fuses results. This is the Tier 2+ graph-vector search described in E-17/S-39.

**Session lifecycle tools** — New tools for Claude Desktop to manage session context proactively:
- `depthfusion_begin_task` — registers a task with metadata (project, goal, branch) for richer tagging
- `depthfusion_end_task` — triggers auto-learn extraction + discovery write-back without waiting for compaction
- `depthfusion_recall_timeline` — returns a chronological view of work on a project (not just relevance-ranked)

### 7b. SkillForge Integration (E-16) — 5 Seams

When approved, DepthFusion's retrieval primitives will be injected into SkillForge via additive seams (no SkillForge refactoring required):

| Seam | SkillForge Attachment Point | DepthFusion Modules | What It Enables |
|------|-----------------------------|---------------------|-----------------|
| **A** | `runtime/router/phases.ts:83-100` | rrf, weighted, reranker | RRF × attention scoring replaces flat scoring |
| **B** | Validator cache | dispatcher | Semantic recall fallback (similarity, not just hash) |
| **C** | `vector-store.ts:165` | scoring, weighted | AttnRes layer for session block weighting |
| **D** | Phase 4 RL stub | trajectory, strategies | Trajectory-level reward accumulation |
| **E** | `types.ts:23` | cost_estimator | Configurable context budget allocation |

**Module strategy:** 14 PORT to TypeScript, 10 SIDECAR (keep as Python), 2 WRAP (HTTP endpoint).

### 7c. ChromaDB Graph Backend (E-17/S-39)

Currently the knowledge graph uses JSONGraphStore (local) or SQLiteGraphStore (VPS Tier 1). Planned: a third backend using ChromaDB entity collections, enabling vector similarity search over entity embeddings. This would allow "find entities similar to X" queries rather than exact-match-only graph traversal.

**Files to create/modify:**
- `src/depthfusion/graph/store.py` — add `ChromaDBGraphStore` class
- `src/depthfusion/graph/store.py:get_store()` — extend factory for Tier 2
- `tests/test_graph/test_store.py` — add ChromaDB backend tests

### 7d. CIQS Automation (E-15/T-93)

A scripted harness that drives CIQS benchmark prompts through Claude Code/Desktop and logs scores automatically. Currently benchmarking is manual (copy prompt → run → score). Automation enables:
- Pre/post-fix statistical comparison (3-run minimum)
- Regression detection on every release
- Category-level tracking (A through E)

**Files to create:**
- `scripts/ciqs_runner.py` — drives prompts through MCP or CLI
- `scripts/ciqs_scorer.py` — parses responses and computes dimension scores
- `docs/benchmarks/` — directory for stored run results

### 7e. Recursive LLM Step in Skill IR (E-16/S-35)

Add `recursive_llm_call` and `weighted_retrieval` step types to SkillForge's Skill IR (Zod discriminatedUnion), enabling skills to express recursive reasoning as first-class IR operations. Requires:
- Extending the Skill IR Zod schema (SkillForge side)
- HTTP sidecar wrapping `recursive/client.py` for cross-language calls
- `routeSubCall()` method on SkillForge's `CapabilityRouter`
- **Blocked until SkillForge SF-2 stabilizes** (ordering constraint)

### 7f. Confidence Thresholding (shipped in v0.4.0)

Already implemented: entities below 0.7 confidence are filtered at the graph store write boundary. Future: extend to retrieval results — suppress chunks below a configurable BM25 score floor to reduce noise in recall results.

---

## 8. Known Limitations & Bottlenecks

1. **Corpus sparsity** — DepthFusion can only retrieve what's been written to `~/.claude/`. The SessionStart and PostCompact hooks close this gap automatically, but manual `/learn` still produces the highest-quality memory files.

2. **Category D ceiling** — Cross-session continuity depends on facts being written during or after sessions. If a session ends without compaction (e.g., Claude Code crashes), no discovery is extracted.

3. **Graph-vector gap** — Graph traversal and vector search operate independently. The planned unified `smart_recall` tool (7a) addresses this.

4. **Single-machine corpus** — `sync.sh` bridges local↔VPS, but there's no multi-user or cloud-hosted corpus yet. DepthFusion is a personal memory system.

5. **MCP stdio transport** — Claude Desktop communicates via stdio, which limits concurrent tool calls. No WebSocket or HTTP transport yet.

---

## 9. Key Commands

```bash
# Tests
pytest                                       # Full suite (439 tests)
pytest --cov=depthfusion                    # With coverage
pytest tests/test_graph/ -v                 # Graph subsystem only

# Quality
mypy src/                                    # Type check
ruff check src/ tests/                       # Lint

# Compatibility
python -m depthfusion.analyzer.compatibility # C1-C11 constraints

# Installation
python -m depthfusion.install.install --mode local   # Local mode
python -m depthfusion.install.install --mode vps     # VPS mode
python -m depthfusion.install.migrate                # Tier 1 → Tier 2

# Sync
bash sync.sh --dry-run                       # Preview sync changes
bash sync.sh                                 # Execute bidirectional sync

# MCP (Claude Desktop registration)
claude mcp add depthfusion -- $(pwd)/.venv/bin/python -m depthfusion.mcp.server
```

---

## 10. Canonical References

| Document | Path | Purpose |
|----------|------|---------|
| Build plan | `docs/Account_synch/depthfusion-build-plan.md` | Phase-by-phase task breakdown |
| Mega-prompt | `docs/Account_synch/depthfusion-mega-prompt.md` | Full context bootstrap for any session |
| Honest assessment | `docs/honest-assessment-2026-03-28.md` | CIQS analysis, bottleneck identification |
| SkillForge integration | `docs/skillforge-integration-plan.md` | 5 seams, 28 module assessment |
| CIQS protocol | `docs/performance-measurement-prompt.md` | 5-category benchmark battery |
| Continuity doc | `docs/Account_synch/master-continuity-document.md` | Cross-project context (all Greg's projects) |
| v0.3.0 spec | `docs/superpowers/specs/2026-03-28-depthfusion-v0.3.0-design.md` | Original design spec |
| v0.4.0 spec | `docs/superpowers/specs/2026-03-28-depthfusion-v0.4.0-design.md` | Knowledge graph design |
| Release process | `docs/release-process.md` | Pre-tag checklist |
| Sync guide | `docs/sync-guide.md` | Local ↔ VPS sync |
| Backlog | `BACKLOG.md` | 17 epics, 40+ stories, 114 tasks |
