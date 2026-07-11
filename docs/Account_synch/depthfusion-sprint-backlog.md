# DEPTHFUSION — SPRINT BACKLOG

> **Generated**: 2026-04-08 | **Tracks**: v0.3.1 + v0.4.0
> **Parallel Sessions**: 2-3 Claude Code tmux sessions via Ruflo

---

## SPRINT 1: Scoring Fixes (Days 1-3)

| ID | Task | Priority | Est | Assignable | Status | Dependencies | Acceptance Criteria |
|----|------|----------|-----|------------|--------|-------------|-------------------|
| 1.1 | BM25 length normalization (k1=1.2, b=0.75) | HIGH | 3h | Session A | TODO | None | `review-gate-patterns.md` no longer dominates all queries. New test: `score(short_relevant) > score(long_irrelevant)` |
| 1.2 | Extend snippet max chars 500→1500 | HIGH | 1h | Session A | TODO | None | Snippets end at sentence boundaries. Config `SNIPPET_MAX_CHARS`. |
| 1.3 | Wire RRF fusion into retrieval pipeline | HIGH | 3h | Session B | TODO | None | End-to-end query through hybrid pipeline returns RRF-fused results. `FUSION_ENABLED=false` bypasses. |
| 1.6 | Source classification fix (track dir at load time) | MEDIUM | 2h | Session B | TODO | None | Source type derived from directory metadata, not filename pattern. |

**Sprint 1 Gate**: `pytest` all GREEN. `ruff check` clean. `mypy src/` clean.

---

## SPRINT 2: Data Gap Fixes (Days 4-6)

| ID | Task | Priority | Est | Assignable | Status | Dependencies | Acceptance Criteria |
|----|------|----------|-----|------------|--------|-------------|-------------------|
| 1.4 | Git log in SessionStart hook | CRITICAL | 4h | Session A | TODO | None | New session auto-injects: recent commits, current branch, backlog items. Hook completes <2s. Graceful timeout. |
| 1.5 | Automated discovery write-back | CRITICAL | 6h | Session B | TODO | None | PostCompact → extracts key facts → writes `~/.claude/shared/discoveries/{date}-autocapture.md`. Heuristic works without API key. |
| 1.5a | Haiku enrichment for discovery write-back | MEDIUM | 2h | Session B | TODO | 1.5 | VPS mode: Haiku summarization produces richer discovery files. Feature-flagged behind `HAIKU_ENABLED`. |

**Sprint 2 Gate**: `pytest` all GREEN. New tests for hooks. Manual test: start new session, verify context injected.

---

## SPRINT 3: Benchmark & Release v0.3.1 (Days 7-8)

| ID | Task | Priority | Est | Assignable | Status | Dependencies | Acceptance Criteria |
|----|------|----------|-----|------------|--------|-------------|-------------------|
| 1.7a | CIQS baseline Run 2 | HIGH | 1h | Session A | TODO | S1+S2 | Complete CIQS run, document in `docs/benchmarks/` |
| 1.7b | CIQS baseline Run 3 | HIGH | 1h | Session A | TODO | S1+S2 | Complete CIQS run, document in `docs/benchmarks/` |
| 1.7c | CIQS post-fix comparison | HIGH | 1h | Session A | TODO | 1.7a, 1.7b | 3 runs post-fix all ≥88. Category D ≥55%. |
| REL1 | Bump version to 0.3.1 in pyproject.toml | LOW | 15m | Session A | TODO | 1.7c | Version string updated. |
| REL2 | Update README.md with hook setup instructions | MEDIUM | 1h | Session A | TODO | 1.4, 1.5 | README documents SessionStart hook, PostCompact hook, discovery file format. |
| REL3 | Tag v0.3.1 and push | LOW | 15m | Session A | TODO | REL1, REL2 | `git tag v0.3.1 && git push --tags` |

**Sprint 3 Gate**: v0.3.1 tagged. CIQS ≥88. All tests GREEN. README updated.

---

## SPRINT 4: Graph Foundation (Days 9-12)

| ID | Task | Priority | Est | Assignable | Status | Dependencies | Acceptance Criteria |
|----|------|----------|-----|------------|--------|-------------|-------------------|
| 2.1 | Entity types & regex extraction | HIGH | 4h | Session A | TODO | v0.3.1 | 8 entity types. Regex extracts `class`, `function`, `file` with confidence 1.0. |
| 2.1a | Haiku entity enrichment | HIGH | 4h | Session A | TODO | 2.1 | Haiku extracts `concept`, `decision`, `error_pattern` at 0.70-0.95 confidence. Feature-flagged. |
| 2.2 | Graph types (Entity, Edge, GraphScope, TraversalResult) | HIGH | 2h | Session B | TODO | v0.3.1 | Dataclasses with full type hints. Serializable to JSON. |
| 2.2a | Graph store backends (JSON/SQLite/ChromaDB) | HIGH | 3h | Session B | TODO | 2.2 | CRUD ops. Local→JSON, VPS Tier 1→SQLite, VPS Tier 2→ChromaDB entity collection. |

**Sprint 4 Gate**: `pytest tests/test_graph/test_extractor.py tests/test_graph/test_types.py tests/test_graph/test_store.py` all GREEN.

---

## SPRINT 5: Graph Intelligence (Days 13-16)

| ID | Task | Priority | Est | Assignable | Status | Dependencies | Acceptance Criteria |
|----|------|----------|-----|------------|--------|-------------|-------------------|
| 2.3 | Edge linking (co-occurrence + haiku + temporal) | HIGH | 8h | Session A | TODO | 2.1, 2.2 | 3 signal types. 6 edge types. Weight = signal count (1-3). |
| 2.4 | Graph traversal + query expansion + score boost | HIGH | 6h | Session B | TODO | 2.2, 2.3 | `traverse("BM25")` returns related entities. `expand_query` adds linked terms. `boost_scores` +0.15-0.30. |
| 2.5 | Session scope management | MEDIUM | 4h | Session C | TODO | 2.2 | per-project (default), cross-project, custom. Persisted in .meta.yaml. |

**Sprint 5 Gate**: `pytest tests/test_graph/` all GREEN. Traversal <100ms depth≤3.

---

## SPRINT 6: Integration (Days 17-19)

| ID | Task | Priority | Est | Assignable | Status | Dependencies | Acceptance Criteria |
|----|------|----------|-----|------------|--------|-------------|-------------------|
| 2.6 | MCP tools: graph_traverse, graph_status, set_scope | HIGH | 5h | Session A | TODO | 2.4, 2.5 | 11 total MCP tools. `GRAPH_ENABLED=false` hides graph tools. |
| 2.7 | Pipeline integration (query expansion + rerank boost) | HIGH | 6h | Session A | TODO | 2.4, 2.6 | End-to-end: query→expand→BM25→RRF→rerank→boost→results. Flag regression: `GRAPH_ENABLED=false` identical to v0.3.1. |
| 2.8 | PostCompact graph extraction pipeline | MEDIUM | 4h | Session B | TODO | 2.1, 2.3 | PostCompact→extract entities→link edges→update store. Silent (no user prompt). |

**Sprint 6 Gate**: `pytest` all GREEN. MCP server starts cleanly with all 11 tools. C1-C11 all GREEN.

---

## SPRINT 7: Test, Benchmark, Release v0.4.0 (Days 20-21)

| ID | Task | Priority | Est | Assignable | Status | Dependencies | Acceptance Criteria |
|----|------|----------|-----|------------|--------|-------------|-------------------|
| 2.9a | Graph module test suite (~80 new tests) | HIGH | 4h | Session A | TODO | S6 | 408+ total tests GREEN. ≥80% coverage on new code. 100% on graph core. |
| 2.9b | Feature flag regression matrix | HIGH | 2h | Session A | TODO | 2.9a | All flag combinations pass. `GRAPH_ENABLED=false` produces v0.3.1-identical output. |
| 2.9c | CIQS benchmark (3 runs) | HIGH | 2h | Session A | TODO | 2.9b | All ≥93. Category D ≥65%. |
| 2.9d | Graph performance benchmarks | MEDIUM | 2h | Session B | TODO | 2.9a | traverse <100ms (depth≤3), extraction <500ms/file, store CRUD <10ms. |
| REL4 | Bump version to 0.4.0 | LOW | 15m | Session A | TODO | 2.9c | Version updated. |
| REL5 | Update README.md (3 new tools, GRAPH_ENABLED flag, graph install) | MEDIUM | 1h | Session A | TODO | 2.9c | Fully documented. |
| REL6 | Write implementation summary doc | MEDIUM | 1h | Session A | TODO | 2.9c | `docs/superpowers/plans/2026-04-08-depthfusion-v0.4.0-implementation.md` |
| REL7 | Tag v0.4.0 and push | LOW | 15m | Session A | TODO | REL4-6 | `git tag v0.4.0 && git push --tags` |

**Sprint 7 Gate**: v0.4.0 tagged. 408+ tests. CIQS ≥93. All docs updated. C1-C11 all GREEN.

---

## SUMMARY METRICS

| Metric | v0.3.0 (Current) | v0.3.1 (Target) | v0.4.0 (Target) |
|--------|-----------------|----------------|----------------|
| Tests | 328 | 350+ | 408+ |
| CIQS Overall | ~85 | ≥88 | ≥93 |
| Category A | ~87.5% | ~93% | ~95% |
| Category D | ~25% | ≥55% | ≥65% |
| MCP Tools | 8 | 8 | 11 |
| Feature Flags | 10 | 10 | 11 |
| C1-C11 | 10G/1Y | 10G/1Y | 11G |
| Est. Calendar Time | — | 8 days | 13 days |
