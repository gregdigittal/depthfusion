# Changelog

All notable changes to DepthFusion are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/) with project-specific adjustments (inline T-/S-/E- backlog references).

Conventions:
- Dates in ISO (YYYY-MM-DD)
- Version anchors: `## [Unreleased]`, `## [v0.5.0] — YYYY-MM-DD`
- Sections per release: Added / Changed / Deprecated / Removed / Fixed / Security
- Backlog cross-references in parentheses: `(T-115)`, `(S-41, S-42)`, `(E-18)`

---

## [Unreleased] — v0.5 Planning

### Planning artefacts only — no source changes in this release yet

- `docs/plans/v0.5/01-assessment.md` — feature assessment, 15-feature ranked list, proceed-with-caveats verdict.
- `docs/plans/v0.5/02-build-plan.md` — 15 task groups (TG-01 through TG-15), dependency-ordered merge plan, acceptance criteria per TG. AC-01-8 added post DR-018 ratification for quality-ranked fallback order.
- `docs/plans/v0.5/03-skillforge-integration.md` — Adapter B specification, DR-017 §6-compliant invariant-compliance table, failure-mode matrix, evolution path to Saihai core module. Invariant rows I-8/I-9/I-10/I-11 ratified per DR-018 §4.
- `docs/plans/v0.5/04-rollout-runbook.md` — 5-step rollout sequence with concrete shell commands and ≤ 10 min rollback paths.
- `docs/plans/v0.5/README.md` — index, per-file TL;DR, Greg-decision checklist.
- `docs/plans/v0.5/commit-drafts.md` — commit-strategy options.
- `docs/plans/v0.5/backlog-addition-proposal.md` — 5-epic v0.5 BACKLOG.md addition (E-18..E-22, S-41..S-55, T-115..T-171).

### Upstream dependency (SkillForge repo)

- `docs/research/DR-018_LEGACY_INVARIANT_REINSTATEMENT.md` — ratified 2026-04-18 per `/goal --autonomous` delegation. Locks 5 per-legacy-invariant verdicts; cascaded amendments applied to v0.5 planning docs.

---

## [v0.4.0] — TBD

> **Note to maintainer:** backfill this entry from git log on the `feat/v0.4.0-knowledge-graph` branch. Key landmarks expected:
> - 8-entity knowledge-graph types (`class`, `function`, `file`, `concept`, `project`, `decision`, `error_pattern`, `config_key`)
> - 7-edge relationship model (CO_OCCURS / CO_WORKED_ON / CAUSES / FIXES / DEPENDS_ON / REPLACES / CONFLICTS_WITH)
> - RegexExtractor + HaikuExtractor pipeline with confidence merging
> - `JSONGraphStore` + `SQLiteGraphStore` tier-aware factory
> - CoOccurrenceLinker / TemporalLinker / HaikuLinker chain
> - `depthfusion_graph_traverse`, `depthfusion_graph_status`, `depthfusion_set_scope` MCP tools
> - `DEPTHFUSION_GRAPH_ENABLED`, `DEPTHFUSION_GRAPH_MIN_CONFIDENCE` flags
> - Query expansion integration in `RecallPipeline`; entity extraction in `auto_learn`

Reference the backlog: E-11 (Knowledge Graph), S-28 to S-36, T-87 onwards.

---

## [v0.3.1] — TBD

> **Note to maintainer:** backfill from git log (tag applied retroactively per `docs/release-process.md` recommendation). Key landmarks:
> - BM25 scoring wired into `_tool_recall` (`mcp/server.py`) — fixes Issue 1 from honest-assessment
> - Snippet length extended from 500 → 1500 chars — fixes Issue 2
> - Source classification tracked at read time, weights {memory: 1.0, discovery: 0.85, session: 0.70} — fixes Issue 3
> - RRF fusion wired into recall pipeline — fixes Issue 4
> - Block chunking on `\n## ` H2 headers — fixes Issue 5
> - Sentence-boundary snippet trimming (60% min threshold) — T-73
> - Confidence threshold at graph store write — T-114
> - `DEPTHFUSION_API_KEY` auth isolation from `ANTHROPIC_API_KEY` — commit `3052c2b`
> - C4 compatibility YELLOW → GREEN — T-110
> - 439 tests passing

Reference the backlog: E-14 (CIQS Data-Gap Closure), S-32 to S-36.

---

## [v0.3.0] — baseline release

> **Note to maintainer:** this was the initial baseline. If the original release notes live in a project knowledge base, link them here; otherwise backfill from the earliest git history.

Key modules shipped:
- Core: `core/types.py`, `core/scoring.py`, `core/config.py`, `core/feedback.py`
- Retrieval: `retrieval/bm25.py`, `retrieval/hybrid.py`, `retrieval/reranker.py`
- Fusion: `fusion/rrf.py`, `fusion/weighted.py`, `fusion/block_retrieval.py`, `fusion/reranker.py`
- Session: `session/tagger.py`, `session/scorer.py`, `session/loader.py`, `session/compactor.py`
- Router: `router/bus.py`, `router/dispatcher.py`, `router/publisher.py`, `router/subscriber.py`, `router/cost_estimator.py`
- Recursive: `recursive/client.py`, `recursive/sandbox.py`, `recursive/strategies.py`, `recursive/trajectory.py`
- Storage: `storage/tier_manager.py`, `storage/vector_store.py`
- MCP: `mcp/server.py` (11 tools)
- Analyzer: `analyzer/scanner.py`, `analyzer/compatibility.py` (C1-C11), `analyzer/recommender.py`, `analyzer/installer.py`
- Install: `install/install.py`, `install/migrate.py`
- Metrics: `metrics/collector.py`, `metrics/aggregator.py`

Reference the backlog: E-01 through E-10, E-12, E-13.
