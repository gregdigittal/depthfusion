# DEPTHFUSION — NEXT PHASE BUILD PLAN
## v0.3.1 (High-Leverage Fixes) → v0.4.0 (Knowledge Graph)

> **Date**: 2026-04-08 | **Owner**: Greg Morris
> **Repo**: github.com/gregdigittal/depthfusion
> **Baseline**: v0.3.0 — 328 tests GREEN, CIQS ~85 (local), C1-C11 10 GREEN / 1 YELLOW

---

## EXECUTIVE SUMMARY

This plan covers two releases delivering the highest-impact improvements identified in the honest assessment (2026-03-28):

**v0.3.1 — "Close the Data Gap"** (1-2 weeks)
Target: CIQS 76.8 → 88-90. Category D: 25% → 55-65%. Six surgical fixes that address the retrieval scoring bugs and the Category D data availability bottleneck. Zero new architecture — just wiring what's already built.

**v0.4.0 — "Knowledge Graph"** (3-4 weeks)
Target: CIQS 90 → 93+. Adds entity extraction, graph linking, and graph-augmented retrieval. New MCP tools. Full spec exists in `docs/superpowers/specs/2026-03-28-depthfusion-v0.4.0-design.md`.

**Total estimated effort**: 5-6 weeks of focused sprints (parallelisable across 2-3 Claude Code sessions).

---

## PHASE 1: v0.3.1 — CLOSE THE DATA GAP

### Rationale

The honest assessment proved that DepthFusion's retrieval algorithm works correctly — the bottleneck is that critical facts are never written to `~/.claude/`. Six fixes address this with the highest CIQS delta per unit of effort.

### Task Breakdown

#### TASK 1.1: BM25 Length Normalization
**Priority**: HIGH | **Effort**: 2-3 hours | **CIQS Delta**: +5-8% Category A
**Problem**: `review-gate-patterns.md` (19KB) dominates ALL queries because BM25 doesn't normalize by document length.
**Input**: `src/depthfusion/retrieval/bm25.py`
**Output**: Modified BM25 scorer with length normalization (standard BM25 k1=1.2, b=0.75)
**Test**:
```bash
pytest tests/test_retrieval/test_bm25.py -v
# New test: assert score(short_relevant_doc) > score(long_irrelevant_doc)
# New test: assert top-1 result for "VPS setup" is NOT review-gate-patterns.md
```
**Rollback**: `git reset --soft HEAD~1`

#### TASK 1.2: Extended Snippets (500 → 1500 chars)
**Priority**: HIGH | **Effort**: 1 hour | **CIQS Delta**: +3-5% Category A
**Problem**: 500-char snippet cuts off content mid-sentence, losing critical context.
**Input**: `src/depthfusion/fusion/block_retrieval.py` (or wherever snippet extraction lives)
**Output**: `SNIPPET_MAX_CHARS` config (default 1500), respects sentence boundaries
**Test**:
```bash
pytest tests/test_fusion/test_block_retrieval.py -v
# New test: snippet ends at sentence boundary, not mid-word
# New test: snippet length ≤ SNIPPET_MAX_CHARS
```

#### TASK 1.3: Wire RRF Fusion (Implemented But Never Called)
**Priority**: HIGH | **Effort**: 2-3 hours | **CIQS Delta**: +3-5% Category A
**Problem**: RRF fusion code exists in `src/depthfusion/fusion/rrf.py` but is never invoked in the retrieval pipeline.
**Input**: `src/depthfusion/fusion/rrf.py`, `src/depthfusion/retrieval/hybrid.py`, `src/depthfusion/router/dispatcher.py`
**Output**: RRF wired into the scoring path for VPS Tier 1+ modes
**Test**:
```bash
pytest tests/test_fusion/test_rrf.py tests/test_retrieval/test_hybrid.py -v
# New integration test: end-to-end query through hybrid pipeline returns RRF-fused results
# Verify: DEPTHFUSION_FUSION_ENABLED=false bypasses RRF (identical output to v0.3.0)
```

#### TASK 1.4: Git Log in SessionStart Hook
**Priority**: CRITICAL | **Effort**: 3-4 hours | **CIQS Delta**: +25-35% Category D
**Problem**: Category D scores 25% because session continuity facts (what was built, what branch, recent commits) are never injected. The SessionStart hook doesn't call DepthFusion.
**Input**: Hook configuration (bash hooks in `~/.claude/`)
**Output**:
  - SessionStart hook that calls `depthfusion_recall_relevant` with auto-generated query from `git log --oneline -5` + `git branch --show-current` + `cat BACKLOG.md | head -20`
  - Injected context includes: recent commits, current branch, active backlog items
**Test**:
```bash
# Manual: start new Claude Code session, verify DepthFusion context is injected
# Automated: pytest test for hook output format
pytest tests/test_session/test_loader.py -v
```
**Constraint**: Hook must complete in <2 seconds. Timeout and degrade gracefully.

#### TASK 1.5: Automated Discovery Write-Back
**Priority**: CRITICAL | **Effort**: 4-6 hours | **CIQS Delta**: +30-50% Category D
**Problem**: Facts discovered during sessions (SkillForge integration status, specific errors, architecture decisions) are never written to `~/.claude/`. They exist only in conversation memory.
**Input**: `src/depthfusion/capture/auto_learn.py`, `src/depthfusion/capture/compressor.py`
**Output**:
  - PostCompact hook that extracts key facts from the compacted session via heuristics (local) or Haiku (VPS)
  - Writes structured discovery files to `~/.claude/shared/discoveries/`
  - Discovery format: YAML frontmatter + markdown body, tagged by project/topic
**Test**:
```bash
pytest tests/test_capture/test_auto_learn.py tests/test_capture/test_compressor.py -v
# New test: mock compacted session → verify discovery file created with correct tags
# New test: verify heuristic extraction captures decisions, errors, architecture choices
```
**Constraint**: Heuristic extraction must work without API key (local mode). Haiku enrichment is optional VPS enhancement.

#### TASK 1.6: Source Classification Fix
**Priority**: MEDIUM | **Effort**: 1-2 hours | **CIQS Delta**: +1-2% Category A
**Problem**: Source classification (memory vs discovery vs rule) is fragile — based on filename patterns rather than tracked at read time.
**Input**: `src/depthfusion/core/scoring.py`, `src/depthfusion/session/loader.py`
**Output**: Source directory tracked as metadata at file load time, used in scoring
**Test**:
```bash
pytest tests/test_core/test_scoring.py tests/test_session/test_loader.py -v
```

#### TASK 1.7: Complete CIQS Baseline (Runs 2 & 3)
**Priority**: HIGH | **Effort**: 2-3 hours | **CIQS Delta**: Measurement, not improvement
**Problem**: Only 1 of 3 baseline runs completed. Need statistical confidence before claiming improvement.
**Input**: `docs/performance-measurement-prompt.md`
**Output**: 3 complete CIQS runs (pre-fix and post-fix), documented in `docs/benchmarks/`
**Test**: Compare pre-fix vs post-fix CIQS. Target: ≥88 overall, ≥55% Category D.

### v0.3.1 Definition of Done

- [ ] All 6 fixes implemented and committed
- [ ] 328+ existing tests still GREEN (zero regressions)
- [ ] New tests added for each fix (target: 350+ total)
- [ ] `mypy src/` clean
- [ ] `ruff check src/ tests/` clean
- [ ] C1-C11 compatibility: 10 GREEN / 1 YELLOW (unchanged)
- [ ] CIQS benchmark: 3 runs post-fix, all ≥88 overall
- [ ] Category D: ≥55% (up from 25%)
- [ ] `DEPTHFUSION_FUSION_ENABLED=false` produces identical output to v0.3.0
- [ ] README.md updated with new hook setup instructions
- [ ] Git tag: `v0.3.1`

---

## PHASE 2: v0.4.0 — KNOWLEDGE GRAPH

### Rationale

With retrieval quality maximised (v0.3.1), the next frontier is **structural understanding** — linking entities across sessions so DepthFusion can answer "what depends on X?" and expand queries with related concepts. Full spec: `docs/superpowers/specs/2026-03-28-depthfusion-v0.4.0-design.md`.

### Task Breakdown

#### TASK 2.1: Entity Types & Extraction
**Priority**: HIGH | **Effort**: 6-8 hours | **Parallel**: Can run alongside 2.2
**Output**: `src/depthfusion/graph/extractor.py`
**Spec**:
  - 8 entity types: `class`, `function`, `file`, `concept`, `project`, `decision`, `error_pattern`, `config_key`
  - Regex extraction (confidence 1.0) for code entities
  - Haiku enrichment (confidence 0.70-0.95) for semantic entities (decisions, concepts)
  - Input: discovery files, memory files, session blocks
**Test**:
```bash
pytest tests/test_graph/test_extractor.py -v
# Test: extract entities from sample discovery file
# Test: regex extraction has confidence 1.0
# Test: haiku enrichment respects DEPTHFUSION_HAIKU_ENABLED flag
# Test: GRAPH_ENABLED=false skips extraction entirely
```

#### TASK 2.2: Graph Types & Store
**Priority**: HIGH | **Effort**: 4-5 hours | **Parallel**: Can run alongside 2.1
**Output**: `src/depthfusion/graph/types.py`, `src/depthfusion/graph/store.py`
**Spec**:
  - Entity, Edge, GraphScope, TraversalResult dataclasses
  - Store backends: JSON sidecars (local), SQLite (VPS Tier 1), ChromaDB entity collection (VPS Tier 2)
  - CRUD operations: add_entity, add_edge, get_entity, get_edges, delete_entity
**Test**:
```bash
pytest tests/test_graph/test_types.py tests/test_graph/test_store.py -v
```

#### TASK 2.3: Edge Linking (3 Signals)
**Priority**: HIGH | **Effort**: 6-8 hours | **Depends on**: 2.1, 2.2
**Output**: `src/depthfusion/graph/linker.py`
**Spec**:
  - **Co-occurrence** → `CO_OCCURS` edge (entities in same block)
  - **Haiku-inferred** → `CAUSES`, `FIXES`, `DEPENDS_ON`, `REPLACES`, `CONFLICTS_WITH`
  - **Temporal proximity** (48h window) → `CO_WORKED_ON`
  - Edge weight = count of signals (1-3)
**Test**:
```bash
pytest tests/test_graph/test_linker.py -v
# Test: co-occurrence linking from 2 entities in same block
# Test: temporal linking from entities in sessions within 48h
# Test: edge weight accumulates correctly (additive)
```

#### TASK 2.4: Graph Traversal & Query Expansion
**Priority**: HIGH | **Effort**: 5-6 hours | **Depends on**: 2.2, 2.3
**Output**: `src/depthfusion/graph/traverser.py`
**Spec**:
  - `traverse(entity_name, depth, relationship_filter)` → linked entities + edges
  - `expand_query(query_terms)` → original terms + linked entity names (for BM25 expansion)
  - `boost_scores(ranked_results, query_entities)` → +0.15-0.30 for results containing linked entities
**Test**:
```bash
pytest tests/test_graph/test_traverser.py -v
# Test: traverse("BM25") returns TierManager, RecallPipeline, rrf_fuse()
# Test: expand_query(["SQLite"]) includes "tier", "storage"
# Test: boost_scores increases rank of linked results
# Test: depth=0 returns only direct edges
```

#### TASK 2.5: Session Scope Management
**Priority**: MEDIUM | **Effort**: 3-4 hours | **Depends on**: 2.2
**Output**: `src/depthfusion/graph/scope.py`
**Spec**:
  - Scope modes: per-project (default), cross-project, custom (user-selectable)
  - Scope persists in `.meta.yaml` sidecar
  - Graph queries respect scope boundaries
**Test**:
```bash
pytest tests/test_graph/test_scope.py -v
```

#### TASK 2.6: MCP Tool Integration
**Priority**: HIGH | **Effort**: 4-5 hours | **Depends on**: 2.4, 2.5
**Output**: Updates to `src/depthfusion/mcp/server.py`
**Spec**: 3 new MCP tools:
  - `depthfusion_graph_traverse(entity_name, depth, relationship_filter, include_memories)`
  - `depthfusion_graph_status()` — node count, edge count, coverage %, tier
  - `depthfusion_set_scope(mode, projects)` — set session scope programmatically
**Test**:
```bash
pytest tests/test_analyzer/test_mcp_server.py -v
# Verify all 11 MCP tools register correctly
# Verify GRAPH_ENABLED=false hides graph tools
```

#### TASK 2.7: Pipeline Integration (Query Expansion + Rerank Boost)
**Priority**: HIGH | **Effort**: 4-6 hours | **Depends on**: 2.4, 2.6
**Output**: Updates to `src/depthfusion/retrieval/hybrid.py`, `src/depthfusion/fusion/reranker.py`
**Spec**:
  - Query expansion: Before BM25, expand query terms via graph traversal (depth=1)
  - Rerank boost: After Haiku scores, linked entities get +0.15-0.30 score boost
  - Feature-flagged: `DEPTHFUSION_GRAPH_ENABLED=false` skips both enhancements
**Test**:
```bash
pytest tests/test_retrieval/ tests/test_fusion/ -v
# Integration test: end-to-end query with graph expansion returns better results
# Regression test: GRAPH_ENABLED=false produces identical output to v0.3.1
```

#### TASK 2.8: Graph Extraction Pipeline (PostCompact)
**Priority**: MEDIUM | **Effort**: 3-4 hours | **Depends on**: 2.1, 2.3
**Output**: Hook integration for graph extraction on PostCompact
**Spec**:
  - PostCompact hook triggers entity extraction + edge linking on newly compacted content
  - Extraction runs silently (no user prompt)
  - Results merge into existing graph store
**Test**:
```bash
pytest tests/test_capture/ -v
# Test: PostCompact trigger → entities extracted → edges linked → store updated
```

#### TASK 2.9: Comprehensive Testing & Benchmarking
**Priority**: HIGH | **Effort**: 4-6 hours | **Depends on**: All above
**Output**: ~80 new tests (408+ total), CIQS benchmark runs
**Spec**:
  - All 328 existing tests pass unchanged
  - ~80 new tests for graph module
  - CIQS benchmark: 3 runs, target ≥93 overall
  - Graph-specific benchmarks: traverse <100ms depth≤3, extraction <500ms per file
  - `DEPTHFUSION_GRAPH_ENABLED=false` regression: identical to v0.3.1
**Test**:
```bash
pytest                        # 408+ tests GREEN
pytest --cov=depthfusion      # ≥80% new code, 100% graph core
mypy src/                     # clean
ruff check src/ tests/        # clean
python -m depthfusion.analyzer.compatibility  # C1-C11 GREEN
```

### v0.4.0 Definition of Done

- [ ] All 9 tasks implemented and committed
- [ ] 408+ tests GREEN (328 existing + ~80 new)
- [ ] `mypy src/` clean
- [ ] `ruff check src/ tests/` clean
- [ ] C1-C11 compatibility: all GREEN
- [ ] CIQS benchmark: 3 runs, all ≥93 overall
- [ ] `DEPTHFUSION_GRAPH_ENABLED=false` identical to v0.3.1 output
- [ ] 3 new MCP tools documented in README.md
- [ ] New feature flag (`DEPTHFUSION_GRAPH_ENABLED`) documented
- [ ] `docs/superpowers/plans/2026-04-08-depthfusion-v0.4.0-implementation.md` produced
- [ ] Git tag: `v0.4.0`

---

## DEPENDENCY GRAPH

```
v0.3.1 Tasks (can parallelise heavily):
  1.1 (BM25 norm) ──────────────────┐
  1.2 (Snippets) ───────────────────┤
  1.3 (RRF wiring) ─────────────────┼── 1.7 (CIQS Baseline)
  1.4 (Git log hook) ───────────────┤
  1.5 (Discovery write-back) ───────┤
  1.6 (Source classification) ──────┘

v0.4.0 Tasks:
  2.1 (Extraction) ─┬── 2.3 (Linking) ─── 2.4 (Traversal) ─┬── 2.6 (MCP Tools) ─── 2.7 (Pipeline) ─── 2.9 (Testing)
  2.2 (Types/Store) ┘                     2.5 (Scope) ──────┘                       2.8 (PostCompact)─┘
```

---

## RISK REGISTER

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| Haiku API latency exceeds 600ms budget | MEDIUM | MEDIUM | Timeout + fallback to BM25-only. Cache frequent queries. |
| Graph extraction produces noisy entities | HIGH | MEDIUM | Confidence threshold (≥0.7). Review first 100 extractions manually. |
| Category D improvement less than projected | HIGH | LOW | Git log hook alone delivers +25%. Discovery write-back is additive. |
| C1-C11 regression from new hooks | HIGH | LOW | Run compatibility check in CI. Hook installation is idempotent. |
| ChromaDB not available on VPS | MEDIUM | LOW | Tier 2 is optional. Tier 1 (SQLite) is fallback. |
| SkillForge integration blocked by SF-4 dependency | LOW | HIGH | SF-1 and SF-2 are independent. Park SF-4/SF-5 until RL Phase 4 ships. |

---

## SPRINT ALLOCATION (Suggested)

| Sprint | Duration | Focus | Sessions | Deliverable |
|--------|----------|-------|----------|-------------|
| S1 | 3 days | v0.3.1 Tasks 1.1-1.3 (scoring fixes) | 2-3 parallel | BM25 norm + snippets + RRF wired |
| S2 | 3 days | v0.3.1 Tasks 1.4-1.6 (data gap fixes) | 2-3 parallel | Hooks + discovery write-back + source fix |
| S3 | 2 days | v0.3.1 Task 1.7 (benchmark) + release | 1 focused | CIQS runs, tag v0.3.1 |
| S4 | 4 days | v0.4.0 Tasks 2.1-2.2 (types + extraction) | 2 parallel | Entity types, graph store, extractor |
| S5 | 4 days | v0.4.0 Tasks 2.3-2.5 (linking + traversal + scope) | 2-3 parallel | Edge linking, traversal, scope management |
| S6 | 3 days | v0.4.0 Tasks 2.6-2.8 (integration) | 2 parallel | MCP tools, pipeline integration, PostCompact |
| S7 | 2 days | v0.4.0 Task 2.9 (test + benchmark + release) | 1 focused | 408+ tests, CIQS ≥93, tag v0.4.0 |

**Total: ~21 working days** (parallelised across 2-3 Claude Code tmux sessions, effective calendar time ~3-4 weeks)

---

*End of build plan. Reference alongside `depthfusion-mega-prompt.md` for session bootstrapping.*
