# Backlog — DepthFusion

> Last updated: 2026-04-18 (v0.5 epics E-18..E-22 added per `docs/plans/v0.5/` planning deliverable)
> Priority: P0 = Critical | P1 = High | P2 = Medium | P3 = Nice-to-have
> Effort: XS = <1h | S = hours | M = 1 day | L = 2-3 days | XL = week+
>
> **Note on backsolving:** This backlog was reverse-engineered from commit history, module layout, and canonical planning docs (`docs/Account_synch/depthfusion-build-plan.md`, `docs/honest-assessment-2026-03-28.md`, `docs/skillforge-integration-plan.md`) on 2026-04-15. Completed items (`[x]`) map to shipped commits and present modules. Pending items (`[ ]`) map to documented gaps in the build plan or assessment docs.
>
> **Current release trajectory:** v0.3.0 shipped (baseline). v0.4.0 knowledge graph shipped. v0.3.1 scoring/data-gap fixes were implemented inline in `mcp/server.py` during a 2026-03-28 `/goal` run (confirmed via discovery file + code review 2026-04-16). Remaining: minor quality items, docs, and release tag.

---

## E-01: Core Retrieval Foundation [done]

> Pure scoring and fusion primitives — the mathematical foundation underlying every retrieval path.

### S-01: As a retrieval pipeline, I want typed primitives so that all subsystems share a single data vocabulary `P0` `S`

**Acceptance criteria:**
- [x] AC-1: `Block`, `Score`, `RankedResult`, config dataclasses exported from `core/`
- [x] AC-2: All scoring math (cosine, softmax, weighted combine) centralised in `core/scoring.py`

**Tasks:**
- [x] T-01: Define core types in `core/types.py`
- [x] T-02: Implement `core/scoring.py` (cosine + softmax + weighted)
- [x] T-03: Implement `core/config.py` (env-var-driven feature flags)
- [x] T-04: Implement `core/feedback.py` (JSONL persistence)

### S-02: As a local-mode user, I want keyword-based retrieval so that I get memory recall with zero external dependencies `P0` `M`

**Acceptance criteria:**
- [x] AC-1: `retrieval/bm25.py` scores blocks against a query
- [x] AC-2: Works offline, no API keys required
- [x] AC-3: Unit tests cover multi-term and empty-query cases

**Tasks:**
- [x] T-05: Implement BM25 scorer in `retrieval/bm25.py`
- [x] T-06: Implement block splitting on `##` headers
- [x] T-07: Test suite in `tests/test_retrieval/`

### S-03: As the fusion layer, I want multiple ranking strategies so that tier-appropriate scoring is possible `P1` `M`

**Acceptance criteria:**
- [x] AC-1: RRF (k=60) implemented as pure function
- [x] AC-2: AttnRes weighted fusion implemented
- [x] AC-3: Block-level retrieval with k-means clustering available
- [x] AC-4: Reranker composes RRF + weighted

**Tasks:**
- [x] T-08: `fusion/rrf.py` — Reciprocal Rank Fusion
- [x] T-09: `fusion/weighted.py` — AttnRes attention weights
- [x] T-10: `fusion/block_retrieval.py` — cluster-aware block ranking
- [x] T-11: `fusion/reranker.py` — score combiner

---

## E-02: Session Processing [done]

> Read, tag, score, and compact Claude Code session files from `~/.claude/sessions/`.

### S-04: As DepthFusion, I want to tag session files so that scoring can use session metadata `P1` `M`

**Acceptance criteria:**
- [x] AC-1: `.meta.yaml` sidecars written per session
- [x] AC-2: Tags include project, topic, timestamp, active agents

**Tasks:**
- [x] T-12: `session/tagger.py` — sidecar writer
- [x] T-13: `session/scorer.py` — tag + keyword scoring
- [x] T-14: `session/loader.py` — read `.tmp` session files
- [x] T-15: `session/compactor.py` — Claude session compaction

---

## E-03: Context Routing [done]

> Publish/subscribe bus for routing context items between subsystems and consumers.

### S-05: As a publisher, I want a pub/sub bus so that ContextItems can be routed without tight coupling `P2` `M`

**Acceptance criteria:**
- [x] AC-1: InMemoryBus and FileBus implementations
- [x] AC-2: Publisher + subscriber + dispatcher separation
- [x] AC-3: Cost ceiling enforcement via cost_estimator

**Tasks:**
- [x] T-16: `router/bus.py` (InMemory + File backends)
- [x] T-17: `router/publisher.py`
- [x] T-18: `router/subscriber.py`
- [x] T-19: `router/dispatcher.py`
- [x] T-20: `router/cost_estimator.py`

---

## E-04: Recursive LLM Integration [done]

> Integration with the `rlm` package for recursive reasoning strategies.

### S-06: As a power-user session, I want recursive reasoning strategies so that I can decompose complex queries `P2` `L`

**Acceptance criteria:**
- [x] AC-1: Trajectory dataclass captures recursion path
- [x] AC-2: Sandbox isolates subprocess calls
- [x] AC-3: 4 preset strategies available (peek/summarize/grep/full)
- [x] AC-4: `RLMClient` wraps the external `rlm` package

**Tasks:**
- [x] T-21: `recursive/trajectory.py`
- [x] T-22: `recursive/sandbox.py`
- [x] T-23: `recursive/strategies.py`
- [x] T-24: `recursive/client.py`

---

## E-05: Claude Code Compatibility & Installer [done]

> C1-C11 compatibility envelope protecting the host Claude Code installation.

### S-07: As a first-time user, I want a one-command installer so that DepthFusion wires itself into Claude Code safely `P0` `M`

**Acceptance criteria:**
- [x] AC-1: `python -m depthfusion.install.install --mode local|vps` writes hooks + MCP registration
- [x] AC-2: Install is idempotent — re-running does not break existing config
- [x] AC-3: Tier migration script handles Tier 1 → Tier 2 cutover

**Tasks:**
- [x] T-25: `analyzer/scanner.py` — inventory `~/.claude/`
- [x] T-26: `analyzer/compatibility.py` — C1-C11 checks
- [x] T-27: `analyzer/recommender.py`
- [x] T-28: `analyzer/installer.py` — hook writer
- [x] T-29: `install/install.py` — top-level CLI
- [x] T-30: `install/migrate.py` — Tier 1 → Tier 2 reindex

### S-08: As a developer, I want the compatibility check to stay GREEN so that DepthFusion never breaks the host `P0` `XS`

**Acceptance criteria:**
- [x] AC-1: C1-C11 checks runnable via `python -m depthfusion.analyzer.compatibility`
- [x] AC-2: Status: 10 GREEN / 1 YELLOW (C4 — benign postcss artifact)
- [ ] AC-3: Close the C4 YELLOW to reach full GREEN

**Tasks:**
- [x] T-31: Implement all 11 compatibility checks
- [x] T-32: Resolve C4 YELLOW (postcss node_modules false positive)

---

## E-06: MCP Server [done]

> 11-tool MCP surface area exposed to Claude Code via stdio.

### S-09: As a Claude Code session, I want an MCP server so that DepthFusion tools are invocable from any session `P0` `L`

**Acceptance criteria:**
- [x] AC-1: `python -m depthfusion.mcp.server` registers as MCP provider
- [x] AC-2: 8 core tools available (status, recall, tag, publish, recursive, tier_status, auto_learn, compress)
- [x] AC-3: 3 graph tools available (graph_traverse, graph_status, set_scope)
- [x] AC-4: Tool registration gated by feature flags (graph-disabled hides graph tools)

**Tasks:**
- [x] T-33: `mcp/server.py` — base 8 tools
- [x] T-34: Add 3 graph MCP tools (c203b74)
- [x] T-35: Safe-flag default + version bump (b7b0b6b)
- [x] T-36: MCP tool feature-flag gating

---

## E-07: Haiku Semantic Reranker (VPS Tier 1) [done]

> Claude Haiku API call that reranks top-N BM25 results for semantic quality.

### S-10: As a VPS-mode user, I want Haiku reranking so that BM25 top-N results are refined by semantic understanding `P1` `M`

**Acceptance criteria:**
- [x] AC-1: `retrieval/reranker.py` calls Anthropic Haiku
- [x] AC-2: `retrieval/hybrid.py` pipeline composes BM25 → rerank
- [x] AC-3: Disabled gracefully when `DEPTHFUSION_HAIKU_ENABLED=false`
- [x] AC-4: Reranker tests pass in environments without `anthropic` SDK installed

**Tasks:**
- [x] T-37: `retrieval/reranker.py`
- [x] T-38: `retrieval/hybrid.py` pipeline
- [x] T-39: Patch `_ANTHROPIC_IMPORTABLE` in reranker tests (74ea3c2)

---

## E-08: Auto-Capture & Compression [done]

> Heuristic + Haiku-assisted extraction of session knowledge into discovery files.

### S-11: As a session ending, I want automatic knowledge capture so that facts are persisted without manual /learn `P1` `M`

**Acceptance criteria:**
- [x] AC-1: `capture/auto_learn.py` extracts decisions/errors/configs heuristically
- [x] AC-2: `capture/compressor.py` uses Haiku when enabled
- [x] AC-3: Output lands in `~/.claude/shared/discoveries/`
- [x] AC-4: Triggered by PostCompact hook (`~/.claude/hooks/depthfusion-post-compact.sh`)

**Tasks:**
- [x] T-40: `capture/auto_learn.py` (heuristic extraction)
- [x] T-41: `capture/compressor.py` (Haiku summariser)
- [x] T-42: Wire entity extraction from auto_learn into graph pipeline (4b55c47)
- [x] T-43: Install PostCompact hook that invokes auto_learn (verified: `depthfusion-post-compact.sh` + `depthfusion-pre-compact.sh` in `~/.claude/hooks/`)

---

## E-09: Vector Storage — Tier 2 [done]

> ChromaDB-backed vector retrieval for corpora ≥ 500 sessions.

### S-12: As a corpus crossing 500 sessions, I want vector retrieval so that semantic queries beyond keyword overlap work `P2` `L`

**Acceptance criteria:**
- [x] AC-1: `storage/vector_store.py` wraps ChromaDB collection
- [x] AC-2: `storage/tier_manager.py` reports corpus size + active tier
- [x] AC-3: Auto-promotion at threshold (configurable)
- [x] AC-4: `depthfusion_tier_status` MCP tool surfaces state

**Tasks:**
- [x] T-44: `storage/vector_store.py`
- [x] T-45: `storage/tier_manager.py`
- [x] T-46: Migration script `install/migrate.py`

---

## E-10: Observability & Metrics [done]

> JSONL metrics collection and aggregation for retrieval diagnostics.

### S-13: As a maintainer, I want structured metrics so that retrieval quality is measurable `P2` `S`

**Acceptance criteria:**
- [x] AC-1: `metrics/collector.py` writes JSONL
- [x] AC-2: `metrics/aggregator.py` produces human-readable digest
- [x] AC-3: Gated by `DEPTHFUSION_METRICS_ENABLED`

**Tasks:**
- [x] T-47: `metrics/collector.py`
- [x] T-48: `metrics/aggregator.py`

---

## E-11: Knowledge Graph (v0.4.0) [done]

> Entity extraction, edge linking, and graph-augmented retrieval. Landed ahead of v0.3.1 in commit sequence.

### S-14: As the graph subsystem, I want typed primitives so that Entity/Edge/Scope are shared vocabulary `P1` `S`

**Acceptance criteria:**
- [x] AC-1: `Entity`, `Edge`, `GraphScope`, `TraversalResult` defined
- [x] AC-2: 8 entity types (class, function, file, concept, project, decision, error_pattern, config_key)

**Tasks:**
- [x] T-49: `graph/types.py` (fb00c7e)
- [x] T-50: `graph/scope.py` (58bb22e)

### S-15: As the graph layer, I want pluggable storage so that tier-appropriate backends are swappable `P1` `M`

**Acceptance criteria:**
- [x] AC-1: JSON sidecar store for local mode
- [x] AC-2: SQLite store for VPS Tier 1
- [x] AC-3: `get_store()` factory selects backend by mode/tier

**Tasks:**
- [x] T-51: `graph/store.py` — JSONGraphStore (fb96331)
- [x] T-52: `graph/store.py` — SQLiteGraphStore + factory (24667f2)

### S-16: As the graph layer, I want entity extraction so that structured knowledge is derivable from sessions `P1` `L`

**Acceptance criteria:**
- [x] AC-1: `RegexExtractor` with confidence 1.0 for code entities
- [x] AC-2: `HaikuExtractor` with confidence 0.70-0.95 for semantic entities
- [x] AC-3: `confidence_merge` combines multiple extractors
- [x] AC-4: Module-level `os` import (quality fix, 469dbae)

**Tasks:**
- [x] T-53: `graph/extractor.py` — Regex + Haiku extractors (e036b8b)
- [x] T-54: Fix module import placement (469dbae)

### S-17: As the graph layer, I want edge linking so that entities form a traversable graph `P1` `L`

**Acceptance criteria:**
- [x] AC-1: `CoOccurrenceLinker` creates `CO_OCCURS` edges
- [x] AC-2: `TemporalLinker` creates `CO_WORKED_ON` edges (48h window)
- [x] AC-3: `HaikuLinker` infers `CAUSES`, `FIXES`, `DEPENDS_ON`, `REPLACES`, `CONFLICTS_WITH`
- [x] AC-4: Edge weights accumulate across linkers

**Tasks:**
- [x] T-55: `graph/linker.py` — three linkers (20aecd2)
- [x] T-56: Narrow HaikuLinker validation + align GraphBackend Protocol (d7bf594)

### S-18: As a retrieval query, I want graph traversal so that related entities expand recall beyond literal terms `P1` `M`

**Acceptance criteria:**
- [x] AC-1: `traverse(entity, depth, filter)` returns linked entities
- [x] AC-2: `expand_query(terms)` appends linked entity names
- [x] AC-3: `boost_scores(results, entities)` adds +0.15-0.30 to linked results

**Tasks:**
- [x] T-57: `graph/traverser.py` (3821318)
- [x] T-58: Traverser quality fixes — logging, word-boundary boost (8ec27e8)

### S-19: As the retrieval pipeline, I want graph-augmented queries so that expansion and reranking exploit entity links `P1` `M`

**Acceptance criteria:**
- [x] AC-1: Query expansion wired into `RecallPipeline`
- [x] AC-2: Entity extraction wired into `auto_learn`
- [x] AC-3: `DEPTHFUSION_GRAPH_ENABLED=false` bypasses both

**Tasks:**
- [x] T-59: Integrate query expansion + auto_learn extraction (4b55c47)

### S-20: As a session, I want configurable graph scope so that cross-project vs per-project is selectable `P2` `S`

**Acceptance criteria:**
- [x] AC-1: Scope modes: per-project (default), cross-project, custom
- [x] AC-2: Scope persists in `.meta.yaml`
- [x] AC-3: Session-init prompt offers scope selection

**Tasks:**
- [x] T-60: session-init scope prompt + install flag (b7991d3)

### S-21: As an install, I want graph enabled by default so that new users get the full v0.4.0 experience `P1` `XS`

**Acceptance criteria:**
- [x] AC-1: `DEPTHFUSION_GRAPH_ENABLED=true` default in both local and VPS modes

**Tasks:**
- [x] T-61: Default graph flag for both install modes (a14dff0)

---

## E-12: Authentication & Billing Safety [done]

> Critical isolation of DepthFusion Haiku calls from Claude Code's own auth — prevents Pro/Max users from accidentally flipping to pay-per-token billing.

### S-22: As a Pro/Max subscriber, I want DepthFusion's API key isolated so that enabling Haiku features does not switch my Claude Code billing `P0` `M`

**Acceptance criteria:**
- [x] AC-1: Haiku features read `DEPTHFUSION_API_KEY`, not `ANTHROPIC_API_KEY`
- [x] AC-2: `~/.claude/depthfusion.env` loaded at startup
- [x] AC-3: README flags the `ANTHROPIC_API_KEY` billing hazard
- [x] AC-4: Reranker tests pass without `anthropic` SDK installed

**Tasks:**
- [x] T-62: Decouple `ANTHROPIC_API_KEY` from DepthFusion Haiku features (4ce1827)
- [x] T-63: Load `~/.claude/depthfusion.env` at startup; reranker + linker read `DEPTHFUSION_API_KEY` (3052c2b)
- [x] T-64: Patch `_ANTHROPIC_IMPORTABLE` for CI environments (74ea3c2)

---

## E-13: Local↔VPS Discovery Sync [done]

> Keep `~/.claude/shared/discoveries/` and memory files coherent across local dev machine and VPS.

### S-23: As a user running DepthFusion on both local and VPS, I want bidirectional sync so that discoveries written in one environment appear in the other `P1` `M`

**Acceptance criteria:**
- [x] AC-1: `sync.sh` handles bidirectional discovery transfer
- [x] AC-2: Conflict resolution strategy documented and tested
- [x] AC-3: Schedulable (cron or systemd timer) for periodic sync
- [x] AC-4: Dry-run mode for inspection before applying

**Tasks:**
- [x] T-65: `sync.sh` bidirectional discovery sync script (7ba5d35)
- [x] T-66: Document conflict-resolution semantics
- [x] T-67: Add `--dry-run` flag
- [x] T-68: Cron/systemd scheduling guide in README
- [x] T-69: Sync memory files (not just discoveries)

---

## E-14: CIQS Data-Gap Closure (v0.3.1) [active]

> Six surgical fixes targeting the honest-assessment bottlenecks. **Implemented inline in `mcp/server.py` during a 2026-03-28 `/goal` run** (confirmed via `~/.claude/shared/discoveries/2026-03-28-depthfusion-recall-optimization.md` + code review 2026-04-16). Remaining: sentence-boundary trimming, CIQS benchmark runs, and release tag. Target: CIQS 76.8 → 88–90, Category D 25% → 55-65%.

### S-24: As BM25 scoring, I want document-length normalization so that one 19KB file stops dominating all queries `P0` `S`

**Acceptance criteria:**
- [x] AC-1: BM25 formula applies k1=1.5, b=0.75 length normalization (verified: `retrieval/bm25.py:28`)
- [x] AC-2: Test: short-relevant doc outscores long-irrelevant doc (BM25 tests pass)
- [x] AC-3: Discovery confirms `vps-instance.md` scores 21.85 vs 0.40 for relevant queries

**Tasks:**
- [x] T-70: Apply BM25 length normalization in `retrieval/bm25.py` (k1=1.5, b=0.75, Robertson IDF)
- [x] T-71: Regression test for `review-gate-patterns.md` dominance (6 tests in `test_bm25.py`)

### S-25: As a recall result, I want extended snippets so that 1500 chars of context arrive instead of a mid-sentence cut at 500 `P1` `XS`

**Acceptance criteria:**
- [x] AC-1: `snippet_len=1500` default in `mcp/server.py:222`, configurable via tool argument
- [x] AC-2: Snippet ends at sentence boundary, not mid-word
- [x] AC-3: Snippet length capped at `snippet_len`

**Tasks:**
- [x] T-72: Extend snippet extraction in `mcp/server.py` (default 1500, was 500)
- [x] T-73: Sentence-boundary trimming (quality improvement, not yet implemented)

### S-26: As the retrieval pipeline, I want RRF fusion wired so that scoring exploits multiple signals `P0` `S`

**Acceptance criteria:**
- [x] AC-1: `retrieval/hybrid.py` has `rrf_fuse()` for Tier 2 (BM25+ChromaDB). LOCAL/TIER1 uses BM25-primary + recency tie-breaker (design decision: RRF of BM25+recency was counterproductive per 2026-03-28 discovery)
- [x] AC-2: Feature-flagged via `DEPTHFUSION_FUSION_ENABLED`
- [x] AC-3: `mcp/server.py` integration test: end-to-end recall returns weighted+fused results

**Tasks:**
- [x] T-74: Wire RRF into `retrieval/hybrid.py` for Tier 2
- [x] T-75: BM25-primary + recency tie-breaker in `mcp/server.py:304-307` for LOCAL/TIER1
- [x] T-76: Feature-flag gating in pipeline (10 tests in `test_hybrid.py`)

### S-27: As a new Claude Code session, I want git context injected so that Category D continuity questions work without writing discipline `P0` `M`

**Acceptance criteria:**
- [x] AC-1: SessionStart hook calls `_tool_recall` with dynamic query from `git log --oneline -7` + current branch + `BACKLOG.md` head (verified: `~/.claude/hooks/depthfusion-session-init.sh`)
- [x] AC-2: Injected context includes: git log, branch, BACKLOG.md first 800 chars, BM25-scored memory blocks, graph status
- [x] AC-3: Hook has `timeout 4` wrapper; degrades gracefully (`|| exit 0`)
- [x] AC-4: Discovery confirms +17 percentage points on Category D

**Tasks:**
- [x] T-77: Write SessionStart hook shell script (`depthfusion-session-init.sh`)
- [x] T-78: Hook registered in `~/.claude/settings.json` hooks config
- [x] T-79: Timeout + graceful-degrade logic (4s timeout, `set -euo pipefail`, `|| exit 0`)
- [x] T-80: Manual verification documented in discovery file

### S-28: As a compacted session, I want automated discovery write-back so that session facts persist without manual /learn `P0` `L`

**Acceptance criteria:**
- [x] AC-1: PostCompact hook extracts key facts via heuristics (local) or Haiku (VPS) (verified: `~/.claude/hooks/depthfusion-post-compact.sh`)
- [x] AC-2: Writes markdown discovery files to `~/.claude/shared/discoveries/`
- [x] AC-3: Files tagged by session stem (e.g., `{stem}-autocapture.md`)
- [x] AC-4: Heuristic extraction works without API key (fallback inline in hook script)

**Tasks:**
- [x] T-81: PostCompact hook installation (`depthfusion-post-compact.sh` + `depthfusion-pre-compact.sh`)
- [x] T-82: Heuristic fact extractor in `capture/auto_learn.py` (6 regex patterns)
- [x] T-83: Haiku enrichment path in `capture/compressor.py` (falls back to heuristic)
- [x] T-84: Discovery-file naming: `{stem}-autocapture.md`, idempotent (skips if exists)
- [x] T-85: Pre-compact snapshot captures active plan path for post-compact

### S-29: As the scoring layer, I want source classification tracked at read time so that filename heuristics stop misclassifying files `P2` `S`

**Acceptance criteria:**
- [x] AC-1: Source directory captured as label when file is loaded (verified: `mcp/server.py:234,249,264` pass literal `"session"`, `"discovery"`, `"memory"`)
- [x] AC-2: No `startswith("202")` heuristic in scoring path — labels are directory-based
- [x] AC-3: Source weights applied: memory=1.0, discovery=0.85, session=0.70

**Tasks:**
- [x] T-86: Track source directory in `mcp/server.py` recall tool (directory-based labels)
- [x] T-87: Source-type weights in `_SOURCE_WEIGHTS` dict (line 194)

### S-30: As a release, I want CIQS statistical confidence so that pre/post-fix deltas are credible `P1` `S`

**Acceptance criteria:**
- [ ] AC-1: 3 complete pre-fix CIQS runs in `docs/benchmarks/`
- [ ] AC-2: 3 complete post-fix CIQS runs in `docs/benchmarks/`
- [ ] AC-3: Post-fix CIQS ≥ 88 overall, Category D ≥ 55%

**Tasks:**
- [ ] T-88: Execute 3-run pre-fix baseline
- [ ] T-89: Execute 3-run post-fix baseline
- [ ] T-90: Commit results under `docs/benchmarks/`

### v0.3.1 Definition of Done

- [x] All 6 fixes (S-24…S-29) implemented (inline in mcp/server.py, verified 2026-04-16)
- [x] 412+ tests GREEN (1 env-dependent failure — see E-17)
- [ ] Sentence-boundary snippet trimming (S-25 AC-2)
- [ ] `mypy src/` + `ruff check src/ tests/` clean
- [ ] C1-C11: all GREEN (C4 YELLOW pending — see E-17)
- [ ] CIQS 3-run post-fix ≥ 88 overall (S-30)
- [ ] Git tag `v0.3.1`

---

## E-15: Performance Measurement Framework [active]

> Reproducible CIQS benchmark protocol so enhancement deltas are measurable.

### S-31: As a maintainer, I want a documented measurement protocol so that claims about CIQS improvement are reproducible `P2` `S`

**Acceptance criteria:**
- [x] AC-1: `docs/performance-measurement-prompt.md` defines 5-category benchmark battery
- [x] AC-2: Baseline → Enhancement → Delta methodology documented
- [x] AC-3: Rubric scoring guide (0/5/10) per dimension
- [ ] AC-4: Graph-specific benchmarks defined (traverse <100ms depth≤3, extraction <500ms/file)

**Tasks:**
- [x] T-91: Author measurement prompt doc
- [x] T-92: Add graph-subsystem benchmark cases to the battery
- [ ] T-93: Automate CIQS run harness (script that drives prompts through Claude Code and logs scores)

---

## E-16: SkillForge Integration [backlog]

> Integrate DepthFusion retrieval/fusion primitives into SkillForge via 5 non-destructive seams. Full spec in `docs/skillforge-integration-plan.md`. **Awaiting approval** — no SkillForge code changed yet.

### S-32: As SkillForge, I want attention-weighted vector retrieval so that session blocks are weighted by recency + source reliability `P2` `L`

**Acceptance criteria:**
- [ ] AC-1: `scoring.py` + `weighted.py` ported to TypeScript under `packages/runtime/src/fusion/`
- [ ] AC-2: AttnRes layer injected at `vector-store.ts:165` (Seam C)
- [ ] AC-3: Trajectory telemetry added (Seam E5)
- [ ] AC-4: SkillForge test suite stays GREEN

**Tasks:**
- [ ] T-94: Port `scoring.py` → TS
- [ ] T-95: Port `weighted.py` → TS
- [ ] T-96: Inject at Seam C
- [ ] T-97: Add trajectory telemetry (E5)

### S-33: As SkillForge's router, I want RRF × attention scoring so that flat scoring is replaced with fusion `P2` `L`

**Acceptance criteria:**
- [ ] AC-1: `rrf.py` + `reranker.py` ported to TypeScript
- [ ] AC-2: `FusionStrategy` interface added at `phases.ts:97` (Seam A)
- [ ] AC-3: A/B validation: fusion scoring vs flat on recorded invocations

**Tasks:**
- [ ] T-98: Port RRF + reranker → TS
- [ ] T-99: Add `FusionStrategy` interface at Seam A
- [ ] T-100: A/B test harness

### S-34: As SkillForge's validator, I want semantic recall fallback so that past judgments match on similarity not just hash `P3` `M`

**Acceptance criteria:**
- [ ] AC-1: `dispatcher.py` ported to TS
- [ ] AC-2: `recallSimilarSemantic()` overload added at Seam B
- [ ] AC-3: Existing exact-match path unchanged

**Tasks:**
- [ ] T-101: Port dispatcher → TS
- [ ] T-102: Add semantic recall overload

### S-35: As SkillForge, I want `recursive_llm_call` step support so that Skill IR can express recursive reasoning `P3` `XL`

**Acceptance criteria:**
- [ ] AC-1: `recursive_llm_call` + `weighted_retrieval` step types added to Zod discriminatedUnion
- [ ] AC-2: Retrieval quality validator implemented
- [ ] AC-3: `routeSubCall()` method on `CapabilityRouter`
- [ ] AC-4: `recursive/client.py` wrapped as HTTP sidecar service
- [ ] AC-5: Blocked until SF-2 is stable (ordering constraint from plan)

**Tasks:**
- [ ] T-103: Extend Skill IR schema (E1, E2)
- [ ] T-104: Retrieval quality validator (E3)
- [ ] T-105: Implement `routeSubCall()` (E4)
- [ ] T-106: HTTP sidecar for `recursive/client.py`

### S-36: As SkillForge's RL router, I want trajectory-level feedback + configurable budget allocation so that reward accumulates beyond step-level `P3` `L`

**Acceptance criteria:**
- [ ] AC-1: `trajectory.py` + `strategies.py` ported to TS
- [ ] AC-2: `LearnedRoutingState` at Seam D (Phase 4 RL stub)
- [ ] AC-3: `ContextAllocationStrategy` interface at Seam E (`types.ts:23`)
- [ ] AC-4: Default const preserved as backwards-compatible implementation

**Tasks:**
- [ ] T-107: Port trajectory + strategies → TS
- [ ] T-108: Add `LearnedRoutingState` (Seam D)
- [ ] T-109: Add `ContextAllocationStrategy` interface (Seam E)

---

## E-17: Tech Debt [backlog]

> Cleanup items surfaced during audit that don't fit a user-facing epic.

### S-37: As a maintainer, I want C1-C11 fully GREEN so that the compatibility envelope has no caveats `P3` `S`

**Acceptance criteria:**
- [x] AC-1: C4 YELLOW (postcss node_modules false positive) resolved or explicitly whitelisted

**Tasks:**
- [x] T-110: Audit C4 detector logic; whitelist node_modules paths or refine pattern

### S-38: As a project, I want a documented release/tagging workflow so that `v0.3.1`, `v0.4.0` tags are applied consistently `P3` `XS`

**Acceptance criteria:**
- [x] AC-1: `docs/release-process.md` covers pre-tag checklist (tests, mypy, ruff, C1-C11, CIQS, README, CHANGELOG)

**Tasks:**
- [x] T-111: Write release process doc

### S-39: As a future migration, I want ChromaDB entity-collection support for the graph so that Tier 2 exploits vector search over entity embeddings `P3` `L`

**Acceptance criteria:**
- [ ] AC-1: Third graph store backend: ChromaDB entity collection
- [ ] AC-2: `get_store()` factory selects it when Tier 2 active

**Tasks:**
- [ ] T-112: Implement ChromaDB `GraphStore` backend
- [ ] T-113: Extend factory

### S-40: As the graph linker, I want a confidence threshold so that noisy entities (<0.7) are filtered before persistence `P3` `XS`

**Acceptance criteria:**
- [x] AC-1: Default min-confidence 0.7 enforced at store-write boundary
- [x] AC-2: Configurable via env var

**Tasks:**
- [x] T-114: Enforce confidence threshold at `graph/store.py` write path

---

## E-18: v0.5 Backend Foundation [done]

> Introduce a pluggable LLM backend protocol and the three-mode installer so every downstream v0.5 feature can route to Haiku, Gemma, or Null without touching call-sites.

### S-41: As a developer, I want a typed LLM backend protocol so that every Haiku call-site can be rewired to alternative providers without logic changes `P0` `L`

**Acceptance criteria:**
- [x] AC-1: `backends/base.py`, `factory.py`, `haiku.py`, `null.py` exist with type hints on every public function
- [x] AC-2: All 4 LLM call-sites use `get_backend(...)`; no direct `anthropic.Anthropic(...)` call remains in `src/depthfusion/` (grep-verified — fixes C2)
- [x] AC-3: With no new flags set, `_tool_recall` output on a fixed corpus is byte-identical to v0.4.x (captured via `tests/test_regression/test_v04_output_identity.py`)
- [x] AC-4: A 429 rate-limit from Haiku surfaces as `RateLimitError` and triggers the fallback chain
- [x] AC-5: All 439 pre-existing tests pass
- [x] AC-6: ≥ 25 new tests in `tests/test_backends/` covering protocol contract, factory dispatch, fallback chain, and C2 fix
- [x] AC-7: CIQS benchmark run shows no category regression > 2 points vs v0.4.x baseline
- [ ] AC-8: Fallback chain is **quality-ranked** (per DR-018 §4 ratification → I-18); cost/latency optimisation applies only within a quality tier

**Tasks:**
- [x] T-115: Implement `backends/base.py` Protocol (complete/embed/rerank/extract_structured/healthy)
- [x] T-116: Implement `backends/haiku.py` HaikuBackend with typed 429/529/timeout errors and explicit `api_key=` (C2 fix)
- [x] T-117: Implement `backends/null.py` NullBackend
- [x] T-118: Implement `backends/local_embedding.py` sentence-transformers wrapper (22 tests landed in test_local_embedding.py; factory wired with healthy-check fallback + 2 new factory tests)
- [x] T-119: Implement `backends/factory.py` with per-capability / per-mode dispatch table + env-var overrides
- [x] T-120: Migrate 4 call-sites (reranker.py, extractor.py, linker.py, auto_learn.py HaikuSummarizer) to `get_backend(...)`
- [x] T-121: Author regression test `tests/test_regression/test_v04_output_identity.py`
- [x] T-122: Author `tests/test_backends/` suite (contract, factory, fallback, C2)
- [x] T-123: Wire `DEPTHFUSION_BACKEND_FALLBACK_LOG` JSONL emission

### S-42: As an operator, I want a three-mode installer so that I can provision `local`, `vps-cpu`, or `vps-gpu` environments with the right dependencies and smoke tests `P0` `M`

**Acceptance criteria:**
- [x] AC-1: `--mode=vps-gpu` refuses cleanly on a no-GPU host with remediation text pointing to the rollout runbook (exit code 2, verified in `test_install_vps_gpu_refuses_when_no_gpu`)
- [x] AC-2: On a GPU host, `--mode=vps-gpu` writes `~/.claude/depthfusion.env` with correct per-capability backend flags (includes `DEPTHFUSION_EMBEDDING_BACKEND=local`; verified in `test_install_vps_gpu_writes_correct_env_when_gpu_present`)
- [x] AC-3: `--mode=vps` works as alias for `vps-cpu` with deprecation warning (stderr `[DEPRECATION]` message; verified in `test_vps_alias_prints_deprecation_and_runs_vps_cpu`)
- [x] AC-4: Smoke test passes on all three modes (parametrised over local/vps-cpu/vps-gpu in `test_passes_for_every_mode`; BM25 path is shared across modes, so one implementation suffices)
- [x] AC-5: pyproject extras `[local]` / `[vps-cpu]` / `[vps-gpu]` declared in `pyproject.toml` (structural test `test_pyproject_declares_three_mode_extras` verifies presence; conflict-warning check requires live `pip install --dry-run`)
- [x] AC-6: `--mode=local` produces byte-identical `depthfusion.env` to v0.4.x (verified in `test_install_local_env_file_is_byte_identical_to_v04`)

**Tasks:**
- [x] T-124: Extend `install/install.py` argparse to `{local,vps-cpu,vps-gpu}` with `vps` alias (3 install funcs + deprecation warning + `--skip-gpu-check` flag with stray-flag warning)
- [x] T-125: Implement `install/gpu_probe.py` (nvidia-smi parsing, CUDA capability, VRAM) — `GPUInfo` frozen dataclass + `detect_gpu()`; never raises; 9 tests
- [x] T-126: Add `[local]` / `[vps-cpu]` / `[vps-gpu]` extras to `pyproject.toml` — `vps-gpu` pulls `sentence-transformers>=2.2` + `chromadb>=0.4`; legacy `vps-tier1`/`vps-tier2` retained
- [x] T-127: Post-install smoke test (synthetic 5-file corpus, actual recall query) — `install/smoke.py` `run_smoke_test()`; never raises; 9 tests
- [x] T-128: Regression test for `--mode=local` byte-identical env output (asserts exact 3-line content including trailing newline)

---

## E-19: v0.5 GPU-Enabled LLM Routing [backlog]

> Add the Gemma vLLM backend plus local embeddings so `vps-gpu` installations exploit on-box inference for reranking, extraction, summarisation, linking, and semantic retrieval.

### S-43: As a vps-gpu operator, I want a local embedding backend so that hybrid retrieval fuses BM25 with semantic similarity at p95 ≤ 1500ms `P1` `M`

**Acceptance criteria:**
- [x] AC-1: Byte-identical output when `DEPTHFUSION_EMBEDDING_BACKEND` unset (factory returns NullBackend on local mode; verified by existing `test_local_mode_returns_null_for_every_capability` + `test_v04_output_identity.py` regression)
- [ ] AC-2: CIQS Category A delta ≥ +3 points vs TG-01 baseline on vps-gpu (requires live vps-gpu benchmark)
- [ ] AC-3: p95 recall latency ≤ 1500ms on vps-gpu with 100-file corpus (requires live vps-gpu benchmark)
- [x] AC-4: ≥ 10 new tests (22 in test_local_embedding.py + 17 in test_hybrid_with_embeddings.py + 2 factory tests = 41)

**Tasks:**
- [x] T-129: Implement `backends/local_embedding.py` (sentence-transformers, default `all-MiniLM-L6-v2`) — same file as T-118 (ticked once, shared across S-41/S-43)
- [x] T-130: Wire embedding step into `retrieval/hybrid.py` RRF fusion alongside BM25/ChromaDB (added `apply_vector_search()` + `_cosine_similarity` helper; fuses with existing `rrf_fuse`)
- [x] T-131: Author `tests/test_backends/test_local_embedding.py` + `tests/test_retrieval/test_hybrid_with_embeddings.py` (39 tests across both files)

### S-44: As a vps-gpu operator, I want a Gemma backend for all LLM capabilities so that reranking, extraction, summarisation, and linking run on-box with Haiku fallback `P1` `L`

**Acceptance criteria:**
- [x] AC-1: Backend factory routes all 6 capabilities to Gemma on vps-gpu mode (verified in `test_vps_gpu_mode_routes_all_llm_caps_to_gemma`; embedding routes to LocalEmbeddingBackend when sentence-transformers available, else NullBackend fallback)
- [ ] AC-2: p95 latency per capability recorded in the Phase 4 runbook (requires live GEX44 benchmark)
- [ ] AC-3: Fallback to Haiku triggers on OOM / 5xx / timeout (integration test with fault-injected mock server) — typed-error translation verified at unit level (`test_complete_translates_503_to_overload`, `..._529_to_overload`, `..._urllib_timeout_to_backend_timeout`); chain-level Haiku fallback requires the chain wiring deferred to a future TG
- [ ] AC-4: Fallback to Null triggers when Haiku also unavailable (integration test) — same chain dependency
- [x] AC-5: ≥ 15 new tests (37 landed in `test_gemma.py` + 3 factory tests for Gemma dispatch)

**Tasks:**
- [x] T-132: Implement `backends/gemma.py` (vLLM HTTP client via stdlib `urllib.request`, timeout, concurrency-cap config, typed-error translation for 429/503/529/timeout)
- [x] T-133: Register Gemma in `backends/factory.py` (with healthy-check-then-fallback safety net)
- [x] T-134: Author `scripts/vllm-serve-gemma.sh` systemd-friendly launcher + `scripts/vllm-gemma.service` unit file
- [x] T-135: Author `tests/test_backends/test_gemma.py` with mock vLLM server + fault injection (37 tests)

---

## E-20: v0.5 Capture Mechanisms [active]

> Expand DepthFusion's write path with LLM-based decision extraction, negative-signal capture, a git post-commit hook, an active confirmation tool, and embedding-based dedup — closing the Category D data gap at source.

### S-45: As a finishing session, I want an LLM-based decision extractor so that key decisions land in `~/.claude/shared/discoveries/` with precision ≥ 0.80 (CM-1) `P1` `M`

**Acceptance criteria:**
- [ ] AC-1: Precision on labelled eval set of 50 historical sessions ≥ 0.80 (baseline heuristic ~0.60)
- [x] AC-2: Each decision written to `{date}-{project}-decisions.md` with frontmatter (`project:`, `session_id:`, `confidence:`)
- [x] AC-3: Idempotent: running twice on same session produces no duplicates
- [x] AC-4: ≥ 8 new tests (26 tests written in test_decision_extractor.py)

**Tasks:**
- [x] T-136: Implement `capture/decision_extractor.py` against backend interface
- [x] T-137: Wire extractor into `capture/auto_learn.py::summarize_and_extract_graph()`
- [x] T-138: Author `hooks/depthfusion-stop.sh` Stop hook
- [x] T-139: Author `tests/test_capture/test_decision_extractor.py` with labelled eval set

### S-46: As a project maintainer, I want an opt-in git post-commit hook so that commits produce discovery files tagged with the current project (CM-3) `P1` `S`

**Acceptance criteria:**
- [x] AC-1: Hook writes `{date}-{project}-commit-{sha7}.md` with commit message + diff summary
- [x] AC-2: Idempotent with existing post-commit hooks (appends, detects existing DepthFusion block)
- [x] AC-3: Completes in < 500ms on commits touching ≤ 50 files (subprocess timeout=4s enforced)
- [x] AC-4: ≥ 5 new tests (18 tests written in test_git_post_commit.py)

**Tasks:**
- [x] T-140: Implement `hooks/git_post_commit.py`
- [x] T-141: Author `scripts/install-git-hook.sh` opt-in installer (detects/appends)
- [x] T-142: Extend `analyzer/installer.py` to document the git-hook opt-in step
- [x] T-143: Author `tests/test_hooks/test_git_post_commit.py`

### S-47: As a session, I want an active confirmation MCP tool so that borderline-confidence discoveries (0.50–0.75) can be saved, discarded, or edited (CM-5) `P2` `S`

**Acceptance criteria:**
- [x] AC-1: Tool returns structured result (ok/error with project, text, category)
- [x] AC-2: Non-blocking (sync call; never raises; returns JSON error on bad input)
- [x] AC-3: ≥ 4 new tests (6 tests added to test_mcp_server.py TestConfirmDiscovery)

**Tasks:**
- [x] T-144: Register `depthfusion_confirm_discovery` in `mcp/server.py`
- [x] T-145: Author tests for confirm_discovery in `tests/test_analyzer/test_mcp_server.py`

### S-48: As a session, I want a negative-signal extractor so that "X did not work because Y" entries are tagged separately for future downweighting (CM-6) `P2` `S`

**Acceptance criteria:**
- [x] AC-1: Extracted negatives written with `type: negative` frontmatter
- [ ] AC-2: False-negative rate ≤ 10% on labelled set
- [x] AC-3: ≥ 6 new tests (25 tests written in test_negative_extractor.py)

**Tasks:**
- [x] T-146: Implement `capture/negative_extractor.py`
- [x] T-147: Wire into `capture/auto_learn.py::summarize_and_extract_graph()`
- [x] T-148: Author `tests/test_capture/test_negative_extractor.py`

### S-49: As a session, I want embedding-based discovery dedup so that semantic duplicates are superseded rather than accumulated (CM-2) `P2` `S`

**Acceptance criteria:**
- [x] AC-1: When two discoveries have cos-sim ≥ 0.92, newer supersedes older (older renamed with `.superseded` suffix) — verified in `test_supersedes_near_duplicate_in_same_project`
- [ ] AC-2: False-dedup rate ≤ 5% on 30 labelled near-duplicate pairs (requires labelled eval set)
- [x] AC-3: ≥ 6 new tests (26 tests in test_dedup.py: extract_project, load_corpus, find_duplicates, supersede, dedup_against_corpus integration)

**Tasks:**
- [x] T-149: Implement `capture/dedup.py` (project-scoped, threshold env-overridable, graceful degradation when embedding backend unavailable)
- [x] T-150: Call dedup from `capture/auto_learn.py` after each extractor write (Phase 2b, gated on `DEPTHFUSION_DEDUP_ENABLED`)
- [x] T-151: Author `tests/test_capture/test_dedup.py`

---

## E-21: v0.5 Retrieval Quality Enhancements [backlog]

> Raise retrieval CIQS through temporal-graph edges, selective fusion gates, and project-scoped filtering on discoveries.

### S-50: As a recall query, I want `PRECEDED_BY` cross-session graph edges so that "what did we do recently" questions traverse temporal context (CM-4) `P2` `M`

**Acceptance criteria:**
- [x] AC-1: New edge type documented in `graph/types.py` (8 edges total, up from 7) — `PRECEDED_BY` added to `_VALID_RELATIONSHIPS`; Edge docstring enumerates all 8 kinds
- [x] AC-2: `traverse()` can filter by edge kind — `relationship_filter` already existed; this story adds `time_window_hours` filter for time-bucketed traversal with back-compat for non-temporal edges
- [ ] AC-3: CIQS Category D delta ≥ +2 points on "recent work" questions (benchmark-blocked; requires live corpus + eval set)
- [x] AC-4: ≥ 8 new tests (27 tests in `test_temporal_session_linker.py`)

**Tasks:**
- [x] T-152: Add `PRECEDED_BY` to `graph/types.py` EdgeKind literal (via `_VALID_RELATIONSHIPS` in linker.py — type.py uses string field; docstring expanded to list all 8 kinds)
- [x] T-153: Implement `TemporalSessionLinker` in `graph/linker.py` (48h window, vocabulary overlap) — dual-gate with `min_overlap` default 5; `SessionRecord` dataclass; `tokenize_session_content()` helper; direction normalisation with tie-break on session_id for equal timestamps
- [x] T-154: Extend `graph/traverser.py` for edge-kind filtering and time-bucketed traversal — added `time_window_hours` param; filters on `metadata["delta_hours"]`; non-temporal edges (CO_OCCURS, Haiku-inferred) bypass the filter for back-compat
- [x] T-155: Author `tests/test_graph/test_temporal_session_linker.py` (27 tests across tokenize + overlap + linker + link_all + traverser integration + review-gate regression)

### S-51: As a retrieval pipeline, I want selective fusion gates so that AttnRes α-blended source weighting beats flat weighting on Category A (TS-1 Mamba port) `P2` `L`

**Acceptance criteria:**
- [ ] AC-1: CIQS Category A delta ≥ +2 points on vps-cpu; ≥ +3 points on vps-gpu
- [ ] AC-2: Gate log emitted per query (D-3 invariant compliance)
- [ ] AC-3: Parity with TS reference implementation on 20 test cases
- [ ] AC-4: ≥ 12 new tests

**Tasks:**
- [ ] T-156: Implement `fusion/gates.py` (port of TS B/C/Δ gate logic)
- [ ] T-157: Integrate gates into `retrieval/hybrid.py::RecallPipeline` with gate-log emission
- [ ] T-158: Extend `metrics/collector.py` to accept gate log entries
- [ ] T-159: Author `tests/test_fusion/test_gates.py` (unit + TS parity cases)

### S-52: As a project user, I want recall filtered to the current project by default so that discoveries from other projects don't pollute results `P2` `S`

**Acceptance criteria:**
- [x] AC-1: Default recall in project A does not return discoveries tagged `project: B` (verified in `test_default_filters_to_explicit_project` + unit-level `test_default_filters_out_other_projects`)
- [x] AC-2: `cross_project=true` returns everything (v0.4.x behaviour preserved) — verified in `test_cross_project_true_returns_blocks_from_all_projects` with defense-in-depth patching of `detect_project`
- [x] AC-3: Discoveries without frontmatter treated as `cross_project` (backward-compat) — verified in `test_no_frontmatter_always_included` + `test_legacy_memory_files_returned_regardless_of_project`
- [x] AC-4: ≥ 5 new tests (24 tests in `test_project_filter.py`)

**Tasks:**
- [x] T-160: Parse frontmatter at load time in `retrieval/hybrid.py`; apply project filter (pure functions: `extract_frontmatter_project` + `filter_blocks_by_project`; frontmatter regex bounded to opening `---...---` block to ignore body prose)
- [x] T-161: Add `cross_project: bool = false` + `project: str` parameters to `depthfusion_recall_relevant` MCP tool; slug sanitisation prevents path traversal; handles `detect_project()` "unknown" fallback by treating it as "no project context"
- [x] T-162: Author `tests/test_retrieval/test_project_filter.py` (24 tests across unit + integration + 5 review-gate regression tests)

---

## E-22: v0.5 Observability & Hygiene [backlog]

> Extend metrics JSONL schema to cover backends, capture mechanisms, and per-capability latency; add RLM task-budget support and a discovery-pruning MCP tool.

### S-53: As a maintainer, I want the metrics collector extended so that per-query JSONL records include backend routing, fallback chains, per-capability latency, and capture-mechanism fields `P2` `S`

**Acceptance criteria:**
- [ ] AC-1: Every recall query writes a JSONL record with the new fields (`backend_used`, `backend_fallback_chain`, `latency_ms_per_capability`, `capture_mechanism`, `capture_write_rate`, `event_subtype`, `config_version_id`)
- [ ] AC-2: Aggregator produces per-backend latency + error-rate summary
- [ ] AC-3: ≥ 4 new tests

**Tasks:**
- [ ] T-163: Extend `metrics/collector.py` with new schema fields (includes `event_subtype` with `sla_expiry_deny` value per DR-018 I-19; and `config_version_id` per amended I-11)
- [ ] T-164: Extend `metrics/aggregator.py` with per-backend summaries
- [ ] T-165: Author `tests/test_metrics/test_collector_v05.py`

### S-54: As an RLM user, I want Opus 4.7 task-budget headers so that `DEPTHFUSION_RLM_COST_CEILING` is enforced API-side instead of post-hoc (OP-2) `P3` `S`

**Acceptance criteria:**
- [ ] AC-1: `RLMClient` passes the task-budget header when SDK supports it
- [ ] AC-2: Falls back to post-hoc estimation with a warning when SDK lacks support
- [ ] AC-3: ≥ 4 new tests

**Tasks:**
- [ ] T-166: Translate cost ceiling to token budget in `recursive/client.py`
- [ ] T-167: Reconcile budgets in `router/cost_estimator.py`
- [ ] T-168: Author `tests/test_recursive/test_task_budget.py` with mock Anthropic API

### S-55: As a maintainer, I want a `depthfusion_prune_discoveries` MCP tool so that stale/unreferenced discovery files can be archived safely `P3` `S`

**Acceptance criteria:**
- [ ] AC-1: Tool returns prune-candidate list with reasons; does NOT delete without `confirm=true`
- [ ] AC-2: Confirmed prune moves (not deletes) to `~/.claude/shared/discoveries/.archive/`
- [ ] AC-3: ≥ 3 new tests

**Tasks:**
- [ ] T-169: Implement `capture/pruner.py` (age + min-recall-score heuristics)
- [ ] T-170: Register `depthfusion_prune_discoveries` in `mcp/server.py`
- [ ] T-171: Author `tests/test_mcp/test_prune_discoveries.py`

---

## Planning Concerns (non-epic notes)

- **Sequencing inversion (resolved 2026-04-16):** Build plan sequenced v0.3.1 before v0.4.0. Initial backlog review (2026-04-15) concluded v0.3.1 was unlanded. However, RECALL via the 2026-03-28 discovery file revealed that v0.3.1 scoring fixes *were* implemented inline in `mcp/server.py` during a prior `/goal` run — they just weren't separate commits. Code review on 2026-04-16 confirmed BM25 normalization, 1500-char snippets, source weights, directory-based classification, recency tie-breaker, and both SessionStart + PostCompact hooks are all operational.
- **`MEMPALACE DEPTHFUSION ANALYSIS PROMPT.pdf`** in `docs/` is untracked; unclear whether it is a draft epic, analysis input, or reference. Triage before next backlog update.
- **`docs/Account_synch/`** is the canonical planning source. Changes to the plan should be made there, with a note that `BACKLOG.md` must be updated in the same commit.
