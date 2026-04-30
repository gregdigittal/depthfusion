# Backlog — DepthFusion

> Last updated: 2026-04-21 (E-14/E-15/E-20/E-21 reconciled to `[done]`; E-26 benchmark-harness epic opened)
> Priority: P0 = Critical | P1 = High | P2 = Medium | P3 = Nice-to-have
> Effort: XS = <1h | S = hours | M = 1 day | L = 2-3 days | XL = week+
>
> **Note on backsolving:** This backlog was reverse-engineered from commit history, module layout, and canonical planning docs (`docs/Account_synch/depthfusion-build-plan.md`, `docs/honest-assessment-2026-03-28.md`, `docs/skillforge-integration-plan.md`) on 2026-04-15. Completed items (`[x]`) map to shipped commits and present modules. Pending items (`[ ]`) map to documented gaps in the build plan or assessment docs.
>
> **Current release trajectory:** v0.3.0 (baseline) → v0.4.0 (knowledge graph) → v0.5.0 (three-mode backend protocol + installer) → v0.5.1 (observability + quality baseline) → v0.5.2 (observability depth + interactive install UX) — all shipped and tagged. v0.3.1 data-gap fixes were absorbed into v0.5 (v0.3.1 was never cut as its own tag). Next planned releases: **v0.5.3** (project-filter polish + dogfooded telemetry), **v0.6.0-alpha** (GPU routing: S-43 local embeddings + S-44 on-box Gemma) — latter gated on GPU VPS migration.
>
> **Benchmark separation (2026-04-21):** ACs requiring labelled eval sets or multi-run CIQS measurements have been lifted out of feature epics and consolidated under **E-26: Benchmark Harness & Evaluation Data**. Feature epics E-14/E-15/E-20/E-21 are considered code-complete; their remaining unchecked ACs are referenced from E-26 and will be ticked as the harness runs land.

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

## E-14: CIQS Data-Gap Closure (v0.3.1) [done]

> Six surgical fixes targeting the honest-assessment bottlenecks. **Implemented inline in `mcp/server.py` during a 2026-03-28 `/goal` run** (confirmed via `~/.claude/shared/discoveries/2026-03-28-depthfusion-recall-optimization.md` + code review 2026-04-16). All six stories (S-24..S-29) code-complete. S-30 (3-run statistical confidence) moved to **E-26: Benchmark Harness**. Original v0.3.1 tag was superseded — fixes shipped as part of the v0.5.x line. Target: CIQS 76.8 → 88–90, Category D 25% → 55-65% (to be measured via E-26 harness).

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
- [x] T-73: Sentence-boundary trimming — `_trim_to_sentence()` at `mcp/server.py:224` (seeks `.!?\n` after 60% of `max_len`); called from the three recall return paths (lines 503, 550, 611)

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

### v0.3.1 Definition of Done (reconciled 2026-04-21)

- [x] All 6 fixes (S-24…S-29) implemented (inline in mcp/server.py, verified 2026-04-16)
- [x] 412+ tests GREEN
- [x] Sentence-boundary snippet trimming (S-25 AC-2) — `_trim_to_sentence()` at `mcp/server.py:224`
- [x] `mypy src/depthfusion` + `ruff check src/ tests/` clean (S-59 closed 2026-04-20)
- [x] C1-C11: all GREEN (S-37 closed; C4 false-positive whitelisted)
- [ ] CIQS 3-run post-fix ≥ 88 overall (S-30) — **moved to E-26: Benchmark Harness**
- [x] ~~Git tag `v0.3.1`~~ — **superseded by v0.5.0/v0.5.1/v0.5.2 tag line**; v0.3.1 never cut as its own release

---

## E-15: Performance Measurement Framework [done]

> Documentation, rubrics, and benchmark-battery design. Authoring work is complete; the remaining T-93 (automated CIQS run harness) is a distinct implementation concern and moves to **E-26: Benchmark Harness** as its S-63 deliverable.

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
- [ ] T-93: Automate CIQS run harness (script that drives prompts through Claude Code and logs scores) — **delivery moved to E-26 S-63 (2026-04-21)**

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

### S-68: As a user re-running the installer, I want it to preserve my user-authored env file content so that my API key + custom flags aren't silently deleted `P1` `S`

> Surfaced 2026-04-22 during the Hetzner walk-through. Current
> `_write_env_config()` in `src/depthfusion/install/install.py`
> uses `Path.write_text()` which **truncates** the file — any
> user-authored lines (e.g. `DEPTHFUSION_API_KEY=…`, custom
> `DEPTHFUSION_HAIKU_ENABLED=true`, `DEPTHFUSION_GEMMA_URL=…`)
> added before the installer runs are silently wiped. This is a
> P1 because it's silent data loss on re-install / upgrade paths
> — users who add the API key first (as earlier versions of the
> quickstart instructed) lose it when the installer writes
> mode-specific defaults.

**Acceptance criteria:**
- [ ] AC-1: `_write_env_config()` merges with existing file content
  rather than overwriting — reads existing lines, keys values by
  `KEY=` prefix, preserves user-authored keys not in the mode-specific
  set
- [ ] AC-2: Known mode-specific keys (`DEPTHFUSION_MODE`,
  `DEPTHFUSION_TIER_THRESHOLD`, `DEPTHFUSION_*_BACKEND`) get
  overwritten to reflect the selected mode — those ARE owned by
  the installer
- [ ] AC-3: User-authored keys (`DEPTHFUSION_API_KEY`,
  `DEPTHFUSION_HAIKU_ENABLED`, `DEPTHFUSION_GEMMA_URL`,
  `DEPTHFUSION_FUSION_*`, etc.) are preserved verbatim
- [ ] AC-4: File permissions preserved — if existing file was
  `chmod 600`, new file is `chmod 600` (no reopen-as-world-readable)
- [ ] AC-5: Comment lines (`# …`) and blank lines preserved in
  their original positions
- [ ] AC-6: If the installer would change a user-authored key's
  value (rare; only when a mode explicitly manages it), it prints
  a warning with the key name + old value + new value — never
  silent mutation
- [ ] AC-7: ≥ 5 tests in `tests/test_install/test_env_merge.py`:
  no existing file (fresh write); existing file with no DepthFusion
  keys (append-only); existing file with user-authored API key
  (preserved); existing file with outdated mode key (updated);
  existing file with chmod 600 (preserved)
- [ ] AC-8: Quickstart guides updated — remove the "Order matters"
  warning in §2/§3 once the merge is live; re-fold credential
  append into the same step as the installer run

**Tasks:**
- [ ] T-212: Add `_parse_env_file(path: Path) -> list[tuple[str, str | None]]`
  helper that returns ordered pairs of (line, key_or_None) preserving
  original structure (blank lines + comments as `(line, None)`)
- [ ] T-213: Rewrite `_write_env_config()` to:
  1. Parse existing file if present
  2. Build ordered output: existing lines with known-mode-keys
     updated; remaining mode-keys appended at the end
  3. Preserve file permissions via `os.stat`/`os.chmod`
- [ ] T-214: Tests in `tests/test_install/test_env_merge.py` covering
  the five AC-7 scenarios + warning emission (AC-6)
- [ ] T-215: Remove the "Order matters" preamble + restructure §2/§3
  in both quickstart guides once the merge is live

---

### S-67: As a new user, I want the installer to register the MCP server automatically so that DepthFusion tools are usable in Claude Code without a separate `claude mcp add` step `P2` `S`

> Surfaced 2026-04-21 while answering "do I need to enable per session?" — the installer writes env config and registers compaction hooks, but **does not** register the DepthFusion MCP server with Claude Code. The current `vps-cpu-quickstart.md` and `vps-gpu-quickstart.md` have a dedicated "Register the MCP server" step (§3/§4 respectively) as a workaround. This story folds that step back into the installer.

**Acceptance criteria:**
- [ ] AC-1: `install.install` detects the `claude` CLI via `shutil.which("claude")`
- [ ] AC-2: When `claude` CLI is present AND the MCP server is not already registered (detected by parsing `claude mcp list` output OR reading settings.json's `mcpServers` key), the installer invokes `claude mcp add depthfusion --scope user -- <sys.executable> -m depthfusion.mcp.server`
- [ ] AC-3: Idempotent — re-running the installer on an already-configured host does NOT duplicate the entry and does NOT error
- [ ] AC-4: When `claude` CLI is absent, the installer prints the exact manual `claude mcp add …` command to stdout with a brief explanation — never silently skips
- [ ] AC-5: Failure of the `claude mcp add` subprocess (non-zero exit) is reported to the user but does NOT abort the install — the env-file write + hook registration must have already completed
- [ ] AC-6: ≥ 5 new tests in `tests/test_install/test_mcp_registration.py`: CLI present + not registered (invokes); CLI present + already registered (skips); CLI absent (prints manual command); invocation failure (reports but install continues); `--dry-run` respected
- [ ] AC-7: Quickstart guides updated — remove the standalone "Register the MCP server" sections from both; re-number to close the gap

**Tasks:**
- [ ] T-208: Add `_register_mcp_server()` helper in `src/depthfusion/install/install.py` with `shutil.which` detection and idempotency probe
- [ ] T-209: Wire helper into `install_local`, `install_vps_cpu`, `install_vps_gpu` (called after `_register_hooks`)
- [ ] T-210: Author `tests/test_install/test_mcp_registration.py` covering all AC-6 scenarios (subprocess mocked for the actual CLI invocation)
- [ ] T-211: Remove standalone MCP-registration sections from `docs/install/vps-cpu-quickstart.md` and `docs/install/vps-gpu-quickstart.md`; re-number; remove the "Why isn't this automatic?" aside (no longer needed)

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

## E-19: v0.5 GPU-Enabled LLM Routing [done]

> Both stories code-complete with comprehensive unit coverage: S-43 (LocalEmbeddingBackend, 41 tests across `test_local_embedding.py` + `test_hybrid_with_embeddings.py`) and S-44 (GemmaBackend + FallbackChain, 37 + 24 tests). Factory routes all 6 capabilities correctly on vps-gpu mode (verified `test_vps_gpu_mode_routes_all_llm_caps_to_gemma`). Remaining ACs requiring live GPU benchmarks (S-43 AC-2/AC-3, S-44 AC-2) reference **E-26: Benchmark Harness** as their measurement home — they remain unchecked pending the GPU VPS migration and `vps-gpu` harness run (tracked as S-66 below).

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
- [x] AC-3: Fallback to Haiku triggers on OOM / 5xx / timeout — `FallbackChain` in `backends/chain.py` (v0.6.0-alpha scope) wraps an ordered backend list and catches `RateLimitError` / `BackendOverloadError` / `BackendTimeoutError`, emitting `backend.runtime_fallback` events per transition. Verified by 24 tests in `test_chain.py` including the canonical 3-link cascade `gemma+haiku+null`. **Factory wiring (make chain the default on vps-gpu mode) deferred to v0.6.0 stable — v0.6.0-alpha ships the chain class only, gated opt-in.**
- [x] AC-4: Fallback to Null triggers when Haiku also unavailable — covered by the same `FallbackChain`: a `[gemma, haiku, null]` chain falls through both on sequential typed errors, returning Null's safe defaults. Verified in `test_gemma_haiku_null_cascade` and `test_exhaustion_chain_names_in_order` (plus `test_all_unhealthy_raises_exhausted_empty_chain` for the degenerate case).
- [x] AC-5: ≥ 15 new tests (37 landed in `test_gemma.py` + 3 factory tests for Gemma dispatch)

**Tasks:**
- [x] T-132: Implement `backends/gemma.py` (vLLM HTTP client via stdlib `urllib.request`, timeout, concurrency-cap config, typed-error translation for 429/503/529/timeout)
- [x] T-133: Register Gemma in `backends/factory.py` (with healthy-check-then-fallback safety net)
- [x] T-134: Author `scripts/vllm-serve-gemma.sh` systemd-friendly launcher + `scripts/vllm-gemma.service` unit file
- [x] T-135: Author `tests/test_backends/test_gemma.py` with mock vLLM server + fault injection (37 tests)

---

## E-20: v0.5 Capture Mechanisms [done]

> All five capture mechanisms (S-45..S-49) code-complete: LLM decision extractor (CM-1), git post-commit hook (CM-3), active-confirmation MCP tool (CM-5), negative-signal extractor (CM-6), embedding-based dedup (CM-2). Benchmark-blocked ACs (S-45 AC-1 precision ≥ 0.80, S-48 AC-2 false-neg ≤ 10%, S-49 AC-2 false-dedup ≤ 5%) reference **E-26: Benchmark Harness** as their evaluation home — they remain unchecked here pending labelled eval sets (S-64).

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

## E-21: v0.5 Retrieval Quality Enhancements [done]

> S-50 (PRECEDED_BY temporal graph edges), S-51 (selective fusion gates / AttnRes α-blending, TS-1 Mamba port), S-52 (project-scoped recall default) — all code-complete. Wired into production via S-60 (stream emission) + S-61 (latency tables) during v0.5.1/v0.5.2. CIQS Category A / Category D delta ACs (S-50 AC-3, S-51 AC-1) reference **E-26: Benchmark Harness** — they remain unchecked pending the harness runs (S-65).

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
- [ ] AC-1: CIQS Category A delta ≥ +2 points on vps-cpu; ≥ +3 points on vps-gpu (benchmark-blocked — requires live CIQS eval on both environments)
- [x] AC-2: Gate log emitted per query (D-3 invariant compliance) — `MetricsCollector.record_gate_log()` writes to `YYYY-MM-DD-gates.jsonl` every apply() call; verified in `test_gate_log_written_to_disk` + `test_fallback_triggered_field_in_gate_log_entry`
- [x] AC-3: Parity with TS reference implementation on 20 test cases (parametrised `_PARITY_CASES` matrix — 20 deterministic cases encoding the Python-vs-TS contract)
- [x] AC-4: ≥ 12 new tests (57 tests in `test_gates.py`: 6 GateConfig + 4 cosine + 5 percentile + 10 behaviour + 4 pipeline integration + 20 parity + 5 review-gate regressions + 3 invariants)

**Tasks:**
- [x] T-156: Implement `fusion/gates.py` — `GateConfig`/`GateDecision`/`GateLog` frozen dataclasses + `SelectiveFusionGates` class; B gate (query similarity), C gate (topical coherence), Δ gate (α-blended fused-score threshold); base_scores normalised to percentile [0,1] before the blend so α semantics hold regardless of raw BM25 magnitude
- [x] T-157: Integrate gates into `retrieval/hybrid.py::RecallPipeline` — `apply_fusion_gates()` method; gated on `DEPTHFUSION_FUSION_GATES_ENABLED=true`; fail-open on error AND on empty survivors; emits D-3-compliant gate log via MetricsCollector with `fallback_triggered` flag
- [x] T-158: Extend `metrics/collector.py` — `record_gate_log()` writes to separate `YYYY-MM-DD-gates.jsonl` stream with `fcntl.flock` guarding against concurrent-writer interleaving (gate entries exceed 4 KiB PIPE_BUF); numpy-safe `_json_default` coerces numpy scalars to Python floats (not strings) so downstream log parsers receive native types
- [x] T-159: Author `tests/test_fusion/test_gates.py` (57 tests; 20-case TS parity matrix)

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

## E-22: v0.5 Observability & Hygiene [done]

> Re-opened 2026-04-21 for S-60 integration, re-closed same day.

> Extend metrics JSONL schema to cover backends, capture mechanisms, and per-capability latency; add RLM task-budget support and a discovery-pruning MCP tool.

### S-53: As a maintainer, I want the metrics collector extended so that per-query JSONL records include backend routing, fallback chains, per-capability latency, and capture-mechanism fields `P2` `S`

**Acceptance criteria:**
- [x] AC-1: Every recall query writes a JSONL record with the new fields — `record_recall_query()` writes to `YYYY-MM-DD-recall.jsonl` with `backend_used`, `backend_fallback_chain`, `latency_ms_per_capability`, `total_latency_ms`, `result_count`, `event_subtype`, `config_version_id`. Capture events use `record_capture_event()` to a separate `YYYY-MM-DD-capture.jsonl` stream; `capture_write_rate` is computed by the aggregator from write_success counts per mechanism.
- [x] AC-2: Aggregator produces per-backend latency + error-rate summary — `backend_summary()` returns `{per_backend: {cap::backend: {count, measured_count, avg/p50/p95 latency, error_count, error_rate}}, per_capability_fallback, total_queries, total_errors, overall_error_rate}`. Companion `capture_summary()` returns per-mechanism write rates.
- [x] AC-3: ≥ 4 new tests (26 tests in `test_collector_v05.py` — 2 constants + 5 record_recall_query + 3 record_capture_event + 5 backend_summary + 4 capture_summary + 4 percentile helper + 3 review-gate regressions)

**Tasks:**
- [x] T-163: Extend `metrics/collector.py` — `record_recall_query()` + `record_capture_event()`; two module-level enums (`_VALID_EVENT_SUBTYPES` incl. `sla_expiry_deny` per DR-018 I-19, `_VALID_CAPTURE_MECHANISMS` for the 5 v0.5 CMs); `_append_jsonl()` private helper shared across streams; `_validate_event_subtype()` with DEBUG log on coercion (review fix HIGH-2)
- [x] T-164: Extend `metrics/aggregator.py` — `backend_summary()` + `capture_summary()`; `_percentile()` nearest-rank helper; error attribution fixed so timeout-path queries with no measured latency still get a per-backend bucket (review fix MED-4)
- [x] T-165: Author `tests/test_metrics/test_collector_v05.py` (26 tests)

**Follow-up (L6/L7 from review, optional for v0.6):**
- [ ] Simple `record()` stream not flock-guarded (pre-existing); migrate if multi-process interleaving is observed.
- [ ] `_iter_jsonl` silently skips malformed lines; `skipped_lines` counter in summary would surface data-integrity gaps.

### S-54: As an RLM user, I want Opus 4.7 task-budget headers so that `DEPTHFUSION_RLM_COST_CEILING` is enforced API-side instead of post-hoc (OP-2) `P3` `S`

**Acceptance criteria:**
- [x] AC-1: `RLMClient` passes the task-budget header when SDK supports it — `_task_budget_beta_available()` dual-gates on `DEPTHFUSION_RLM_TASK_BUDGET_ENABLED=true` AND anthropic SDK surface presence; `inspect.signature` probe confirms rlm accepts the `task_budget_tokens` kwarg before passing it; verified in `test_passes_task_budget_when_supported`.
- [x] AC-2: Falls back to post-hoc estimation with a warning when SDK lacks support — DEBUG log explains the skip path; pre-flight `_estimate_cost` ceiling check still fires before any RLM construction; verified in `test_skips_kwarg_when_rlm_does_not_accept` + `test_no_kwarg_when_env_var_off`.
- [x] AC-3: ≥ 4 new tests (19 tests in `test_task_budget.py`: 7 budget translation, 5 probe gate, 4 RLM integration, 2 sanity safety nets, 1 documented-overshoot regression)

**Tasks:**
- [x] T-166: Translate cost ceiling to token budget in `recursive/client.py` — `_task_budget_beta_available()` probe + `inspect.signature` probe on rlm.RLM.__init__; kwarg conditionally added to rlm_kwargs dict
- [x] T-167: Reconcile budgets in `router/cost_estimator.py` — `budget_tokens_for_ceiling(ceiling_usd, model)` translates USD to integer tokens via input pricing; docstring explicitly documents the output-heavy overshoot hazard (up to 5× for opus)
- [x] T-168: Author `tests/test_recursive/test_task_budget.py` (19 tests with mock Anthropic module + mock rlm package)

**Kill-criterion honored:** shipped as "best-effort wrapper without CIQS claim" per build plan §TG-13. Activation requires explicit env var opt-in AND a future SDK release; default behaviour is byte-identical to v0.4.x.

### S-55: As a maintainer, I want a `depthfusion_prune_discoveries` MCP tool so that stale/unreferenced discovery files can be archived safely `P3` `S`

**Acceptance criteria:**
- [x] AC-1: Tool returns prune-candidate list with reasons; does NOT delete without `confirm=true` — verified in `test_confirm_false_returns_candidates_without_moving`; `confirm=False` is an explicit first-line no-op in `prune_discoveries`
- [x] AC-2: Confirmed prune MOVES (not deletes) to `~/.claude/shared/discoveries/.archive/` — verified in `test_confirm_true_moves_to_archive` + `test_never_deletes_only_moves`; archive collision handled with timestamp suffix to prevent overwrites
- [x] AC-3: ≥ 3 new tests (23 tests in `test_prune_discoveries.py`)

**Tasks:**
- [x] T-169: Implement `capture/pruner.py` — `PruneCandidate` frozen dataclass + `identify_candidates()` + `prune_discoveries()`. Two heuristics shipped: `age_exceeded` (default 90d via `DEPTHFUSION_PRUNE_AGE_DAYS`) and `superseded` (`.superseded` suffix from CM-2 dedup). `min-recall-score` heuristic from TG-14 deferred — requires `record_recall_query` to capture chunk_ids of returned blocks, which it doesn't in v0.5.1.
- [x] T-170: Register `depthfusion_prune_discoveries` in `mcp/server.py` — always-enabled tool with `_tool_prune_discoveries(arguments)` handler; `age_days` validated as positive int; returns `{ok, candidates, moved, message}` JSON
- [x] T-171: Author `tests/test_mcp/test_prune_discoveries.py` — 23 tests (4 env var, 8 identify_candidates, 5 prune_discoveries safety, 5 MCP tool, 1 review-gate regression on dot-file filter)

**Follow-up noted:**
- [ ] `superseded_min_age_hours` grace-period parameter (v0.6) — adds an age floor to the superseded heuristic so false-positive dedup runs have a safety window before archival.
- [ ] `min-recall-score` heuristic — requires `record_recall_query` extension to capture chunk_ids of returned blocks per query (separate epic).

### S-60: As an operator, I want production code paths to emit the structured recall/capture streams added in S-53 so that `backend_summary()` and `capture_summary()` actually return data `P2` `S`

**Acceptance criteria:**
- [x] AC-1: `_tool_recall` emits a `recall_query` JSONL record per invocation with `backend_used`, `total_latency_ms`, `result_count`, and `event_subtype` (`ok` on success, `error` on exception). Wrapper extracted via `_tool_recall_impl` to keep emission separate from business logic; error path skips the 6× backend probe for efficiency.
- [x] AC-2: Each capture mechanism emits a `capture` JSONL record per write attempt: `decision_extractor` (success + skip), `negative_extractor` (success + skip), `dedup` (success + skip AND when no duplicates found — review fix IMP-2), `git_post_commit` (success + skip), `confirm_discovery` (re-buckets decision_extractor via `capture_mechanism` kwarg override to avoid double-counting).
- [x] AC-3: Metrics emission never raises into the hot path — shared `capture/_metrics.py::emit_capture_event` helper + local `_emit_capture_event` wrapper in `git_post_commit.py` (defense in depth so a metrics failure can never block a git commit). Verified by `test_broken_metrics_collector_doesnt_break_*` tests.
- [x] AC-4: ≥ 5 integration tests (13 tests in `test_integration.py` — one per call site + 2 review-gate regressions + 2 safety-net checks)

**Tasks:**
- [x] T-186: Wire `record_recall_query` into `_tool_recall` via wrapper around extracted `_tool_recall_impl`; measures `total_latency_ms` via `time.monotonic`, counts blocks from JSON response, detects error path via outer try/except; `_detect_current_backends()` helper probes factory routing (skipped on error path per review fix)
- [x] T-187: Wire `record_capture_event` into `decision_extractor.write_decisions` + `negative_extractor.write_negatives` (via shared `_metrics.py` helper); decision_extractor gains `capture_mechanism` kwarg override for caller re-bucketing
- [x] T-188: Wire `record_capture_event` into `dedup.dedup_against_corpus` — one event per supersede AND a dedicated event when dedup completes with no duplicates (so metrics stream distinguishes "ran, found nothing" from "never ran")
- [x] T-189: Wire `record_capture_event` into `hooks/git_post_commit.write_commit_discovery` via a local `_emit_capture_event` wrapper with extra try/except layer (git hooks must never block a commit)
- [x] T-190: Wire `record_capture_event` into `_tool_confirm_discovery` via the `capture_mechanism="confirm_discovery"` override on `write_decisions` (single event per call, re-bucketed to the higher-level tool label)
- [x] T-191: Integration tests in `tests/test_metrics/test_integration.py` (13 tests)

**Scope note:** `latency_ms_per_capability` field on `record_recall_query` ships with an empty dict in S-60 — per-capability latency measurement requires wrapping individual backend calls (reranker, embedding) with timing decorators, deferred to a v0.6 follow-up.

**Follow-up noted (v0.6):** `_DISCOVERIES_DIR` module-level constants in `negative_extractor.py` + `git_post_commit.py` should be converted to `_default_discoveries_dir()` runtime helpers for consistency with `decision_extractor.py` / `pruner.py` / `install.py`. Same freeze-at-import pattern that bit us in S-42 and again here.

---

## E-24: v0.5.2 Observability Depth [done]

> Fill the `latency_ms_per_capability` field on `record_recall_query` that shipped empty in v0.5.1/S-60. Focused on the two capabilities the recall path actually invokes — `reranker` and `fusion_gates` — rather than the full-refactor instrumentation that would be needed to cover all six LLM call-sites across the codebase.

### S-61: As an operator, I want `latency_ms_per_capability` populated for the two capabilities the recall path invokes so that `backend_summary()` can produce meaningful latency tables `P2` `XS`

**Acceptance criteria:**
- [x] AC-1: When `_tool_recall` emits a `recall_query` event, `latency_ms_per_capability` contains entries for `reranker` and `fusion_gates` when those phases ran — verified in `test_fusion_gates_phase_timed_when_enabled` + `test_reranker_phase_timed_in_non_local_mode`
- [x] AC-2: Phases that didn't run are absent from the dict — verified in `test_local_mode_no_phase_latencies` (neither key present) + `test_empty_pool_skips_fusion_gates_timing` (empty dict)
- [x] AC-3: ≥ 3 new tests (5 tests in `TestLatencyPerCapability`)

**Tasks:**
- [x] T-192: Wire `apply_fusion_gates` into the recall path (previously the method existed on `RecallPipeline` but nothing called it from `_tool_recall_impl`); time both that phase and the existing `apply_reranker` call with `time.monotonic()` brackets. Phase entries emitted only when the phase ran.
- [x] T-193: Thread a mutable `perf_ms: dict[str, float]` through `_tool_recall_impl` (new keyword arg); `_tool_recall` wrapper creates the dict, passes it into the impl, and hands it to `record_recall_query(latency_ms_per_capability=perf_ms)`
- [x] T-194: 5 tests in `test_integration.py::TestLatencyPerCapability` covering local/non-local modes, gates on/off, empty-pool short-circuit, and JSON number serialisation

**Bonus gap closed:** S-61 also wires `apply_fusion_gates` INTO the recall path — the method was added to `RecallPipeline` in S-51 but never called from `_tool_recall_impl`. Now gates actually run when `DEPTHFUSION_FUSION_GATES_ENABLED=true` sees a non-empty input pool, between BM25 scoring and reranking.

**Scope note:** Full backend-level instrumentation (wrapping every `.complete()` / `.embed()` call-site across reranker.py, linker.py, gemma.py, etc.) remains a v0.6 refactor. S-61 times the TWO phases `_tool_recall_impl` actually runs, which are the two that matter for recall-latency observability. Capture-path capabilities (extractor, summariser, linker, decision_extractor) remain out of scope until the auto_learn hot path gets similar instrumentation.

---

---

## E-25: Pre-GPU-Migration UX Bundle [done]

> Three high-leverage items that improve the first-install experience on the new GPU-enabled VPS: interactive mode auto-selection, `apply_vector_search` wired into the recall path (uses the GPU's embedding backend from day 1), and a `vps-gpu`-specific smoke test. All three are shippable before the migration; all three produce immediate value the moment the new host comes up.

### S-62: As a new-host operator, I want the installer to auto-detect my GPU and recommend the right mode so that provisioning doesn't require me to know `--mode=vps-gpu` vs `--mode=vps-cpu` by heart `P1` `S`

**Acceptance criteria:**
- [x] AC-1: `python -m depthfusion.install.install` (no `--mode` arg) probes GPU via `detect_gpu()` and prints a recommendation banner with mode options and detected hardware
- [x] AC-2: Interactive shell (stdin is a tty) prompts `[1/2/3 or Enter]`; non-interactive shell (CI, scripts) + `--yes` flag auto-picks the recommended mode
- [x] AC-3: `apply_vector_search` is wired into `_tool_recall_impl` (gated on `DEPTHFUSION_VECTOR_SEARCH_ENABLED=true`), fuses with BM25 via `rrf_fuse`, times phase as `perf_ms["vector_search"]`
- [x] AC-4: `vps-gpu`-specific smoke test (`run_vps_gpu_smoke()`) validates three probes (nvidia-smi + sentence-transformers + embed roundtrip); runs after `install_vps_gpu` writes the env file; failure is a warning, not fatal
- [x] AC-5: ≥ 7 new tests across the three items (12 new tests: 5 for install UX, 4 for gpu smoke, 3 for vector_search wiring)

**Tasks:**
- [x] T-195: Interactive mode auto-select in `install/install.py::main()` — `_recommend_mode_from_gpu()` picks mode based on `detect_gpu()` + `DEPTHFUSION_API_KEY`; `_print_mode_banner()` shows options; `_read_mode_choice()` handles interactive input; `--yes` + non-tty auto-accept
- [x] T-196: `_tool_recall_impl` wires `apply_vector_search` + `rrf_fuse` between BM25 scoring and fusion gates; phase timed into `perf_ms["vector_search"]`; gated on `DEPTHFUSION_VECTOR_SEARCH_ENABLED`
- [x] T-197: `install/smoke.py::run_vps_gpu_smoke()` three-probe check; called from `install_vps_gpu` post-env-write with warning-not-fatal semantics
- [x] T-198: 12 new tests across `test_install.py`, `test_smoke.py`, `test_metrics/test_integration.py`

> Remove v0.5-era deprecations, wire `config_version_id` for full I-8 compliance, and retire pre-existing mypy/ruff errors. No new features — this epic exists to keep the tech-debt surface from accumulating across v0.6 feature work.

### S-56: As a maintainer, I want the deprecated `--mode=vps` installer alias removed so that the CLI surface doesn't carry indefinite compatibility shims `P2` `XS`

**Acceptance criteria:**
- [ ] AC-1: `python -m depthfusion.install.install --mode=vps` exits with a non-zero argparse error naming the valid choices `{local, vps-cpu, vps-gpu}` — no deprecation-warning pass-through path
- [ ] AC-2: The v0.5-era deprecation test (`test_vps_alias_prints_deprecation_and_runs_vps_cpu`) is replaced with a "rejects vps" regression test
- [ ] AC-3: CHANGELOG §Removed documents the break with an explicit migration note pointing at `--mode=vps-cpu`

**Tasks:**
- [ ] T-172: Remove `"vps"` from argparse choices in `install/install.py`; delete the `if mode == "vps"` deprecation branch in `main()`
- [ ] T-173: Update `test_vps_alias_prints_deprecation_and_runs_vps_cpu` → `test_vps_alias_rejected_in_v06`
- [ ] T-174: Add `[Removed]` entry to `CHANGELOG.md` under `## [v0.6.0]`

### S-57: As a package installer, I want the legacy `vps-tier1`/`vps-tier2` pyproject extras removed so that users migrate cleanly to the three-mode extras `P3` `XS`

**Acceptance criteria:**
- [ ] AC-1: `pyproject.toml` `[project.optional-dependencies]` contains only `local`, `vps-cpu`, `vps-gpu`, `dev`, `rlm` — no `vps-tier1` / `vps-tier2` keys
- [ ] AC-2: `pip install '.[vps-tier1]'` fails with a clear "no matching distribution" error message (standard pip behaviour on removed extras)
- [ ] AC-3: Release notes + migration guide updated

**Tasks:**
- [ ] T-175: Delete `vps-tier1` / `vps-tier2` entries from `pyproject.toml`
- [ ] T-176: Grep the repo for remaining references to `vps-tier1` / `vps-tier2`; update any install docs, runbooks, or agent skills that reference them
- [ ] T-177: Add `[Removed]` entry to `CHANGELOG.md`

### S-58: As an auditor, I want `config_version_id` populated on every gate-log record so that gate decisions can be reproduced against the config snapshot active at invocation (I-8 compliance) `P1` `M`

**Acceptance criteria:**
- [x] AC-1: `GateConfig.version_id()` — sha256 of `(alpha, b_threshold, c_threshold, delta_threshold)` truncated to 12 hex chars; attached to every `record_gate_log()` entry via `RecallPipeline.apply_fusion_gates`
- [x] AC-2: When `GateConfig` changes mid-session (env var reload), the next gate-log entry carries the NEW `config_version_id` — verified in `test_config_version_id_changes_when_env_var_changes`
- [x] AC-3: `TODO(I-8)` marker in `retrieval/hybrid.py::apply_fusion_gates` removed; docstring explicitly names I-8 compliance and the DR-018 §4 ratification as the contract
- [x] AC-4: ≥ 4 new tests (13 tests in `test_gate_config_version.py`: determinism, sensitivity, clamp normalisation, signed-zero regression, end-to-end on-disk)

**Tasks:**
- [x] T-178: `GateConfig.version_id()` — `.10f`-precision format string, defense-in-depth `_normalise_float` collapses signed-zero to guard against IEEE 754 edge cases
- [x] T-179: `apply_fusion_gates` computes `cfg.version_id()` and threads into `record_gate_log(..., config_version_id=...)`; TODO marker replaced with reference to the ratified contract
- [x] T-180: `tests/test_fusion/test_gate_config_version.py` — 13 tests

### S-59: As a maintainer, I want pre-existing mypy + ruff errors retired so that the default `ruff check` and `mypy src/depthfusion` commands are clean `P3` `S`

**Acceptance criteria:**
- [x] AC-1: `mypy src/depthfusion` reports 0 errors — `Success: no issues found in 72 source files`
- [x] AC-2: `ruff check src/ tests/` reports 0 errors — `All checks passed!`
- [ ] AC-3: CI / pre-commit hooks guard against re-introduction (follow-up — current commit + push workflows run ruff but not mypy in the gate)

**Tasks:**
- [x] T-181: Added `types-PyYAML>=6.0.0` to `[dev]` extras; `# type: ignore[import-untyped]` on `import yaml` in `session/loader.py` + `session/tagger.py` with explanatory comment for minimal-deploy environments where the stubs aren't installed
- [x] T-182: `storage/vector_store.py` — narrowed Chroma's `list | None` return types via `results.get(...) or []` + early-return on empty nested list; per-row `distances[0][i] if distances and distances[0] else 0.0` guards
- [x] T-183: `retrieval/hybrid.py` — private `_TierManager` / `_StorageTier` bindings inside `try/except`, public `TierManager = _TierManager` re-alias preserves back-compat with tests that patch `depthfusion.retrieval.hybrid.TierManager`
- [x] T-184: Split E501 long lines — extracted `chunk_id` local in `mcp/server.py:423`, reformatted `return json.dumps({...})` to multi-line, moved the long `type:` enumeration comment in `graph/types.py:16` into the dataclass docstring
- [x] T-185: Moved `from depthfusion.retrieval.bm25 import ...` to module-top in `mcp/server.py`; deleted the mid-file duplicate import block

---

## E-26: Benchmark Harness & Evaluation Data [backlog]

> Consolidates all benchmark-blocked acceptance criteria from feature epics (E-14, E-20, E-21) into a single deliverable workstream. The feature code ships independently; this epic produces the measurement apparatus that lets us assert *how well* it works. Until this epic is active, feature epics carry forward with their benchmark-blocked ACs unchecked — that is a deliberate, documented carve-out, not drift.

### S-63: As a release, I want an automated CIQS run harness so that pre/post-change deltas can be measured reproducibly without manual per-prompt execution `P1` `M`

**Acceptance criteria:**
- [x] AC-1: Harness script drives the 5-category CIQS battery (defined in `docs/performance-measurement-prompt.md` and extracted to `docs/benchmarks/prompts/ciqs-battery.yaml`) through a configurable backend (local / vps-cpu / vps-gpu) and logs per-prompt scores to `docs/benchmarks/{YYYY-MM-DD}-{mode}-run{N}-scored.jsonl` — `scripts/ciqs_harness.py run` + `score` subcommands
- [x] AC-2: 3-run aggregate produces mean + stddev per category with bootstrapped 95% CI — `scripts/ciqs_summarise.py` (5000 bootstrap resamples, seed=1729; math covered by 24 unit tests in `tests/test_scripts/test_ciqs_summarise.py`)
- [ ] AC-3: Closes S-30 ACs (3 pre-fix + 3 post-fix runs committed under `docs/benchmarks/`, post-fix ≥ 88 overall with Category D ≥ 55) — **execution pending (T-201)**
- [ ] AC-4: Closes S-50 AC-3 (Category D ≥ +2 points from PRECEDED_BY edges) and S-51 AC-1 (Category A ≥ +2 on vps-cpu, ≥ +3 on vps-gpu) — **execution pending (T-201)**

**Tasks:**
- [x] T-199: Author `scripts/ciqs_harness.py` — argparse-driven runner with `run`/`score` subcommands, YAML battery, Category A auto-retrieval via `depthfusion.mcp.server._tool_recall`, scoring-template emission for B/C/D/E
- [x] T-200: Implement aggregation + CI computation (`scripts/ciqs_summarise.py`) — linear-interpolated percentile, deterministic bootstrap CI, per-category stats table + raw dump; `docs/benchmarks/README.md` documents the three-stage flow
- [ ] T-201: Commit baseline 3-run for each of local / vps-cpu under `docs/benchmarks/` (vps-gpu run blocked on VPS migration — S-43/S-44 era) — **calendar-blocked on executing the runs**

### S-64: As a capture-mechanism maintainer, I want labelled evaluation sets so that precision/recall claims in S-45/S-48/S-49 can be measured rather than asserted `P2` `M`

**Acceptance criteria:**
- [ ] AC-1: 50-session decision-extraction gold set under `docs/eval-sets/decision-extraction/` — each session has human-labelled "decisions worth capturing" + expected discovery files. **Scaffolding + 2 seed examples landed 2026-04-21; full curation (50 examples) calendar-blocked.**
- [ ] AC-2: 30-pair near-duplicate dedup gold set under `docs/eval-sets/dedup/` — pairs labelled as true-dup / false-dup. **Scaffolding + 2 seed pairs landed 2026-04-21; full curation (30 pairs) calendar-blocked.**
- [ ] AC-3: 40-example negative-signal gold set under `docs/eval-sets/negative/` — sentences labelled as genuine negative vs false-positive. **Scaffolding + 2 seed examples landed 2026-04-21; full curation (40 examples) calendar-blocked.**
- [ ] AC-4: Closes S-45 AC-1 (precision ≥ 0.80), S-48 AC-2 (false-neg ≤ 10%), S-49 AC-2 (false-dedup ≤ 5%) — **execution blocked on AC-1/2/3 population**

**Tasks:**
- [~] T-202: Curate + commit the three gold sets with eval scripts (`scripts/eval_decision.py`, `scripts/eval_dedup.py`, `scripts/eval_negative.py`) — **partial:** all three eval scripts shipped (heuristic extractor + bag-of-words cosine matching, deliberate backend-free lower-bound); 2 seed examples per set pin the JSON schema and smoke-test the scripts. Full curation (50 + 30 + 40 examples) is the remaining labour.
- [x] T-203: Document eval methodology in `docs/eval-sets/README.md` (labelling protocol, inter-rater-agreement guidance, add-new-example workflow) — 175-line methodology doc + per-set READMEs covering schema, edge cases, running the measurements

### S-66: As a post-migration operator, I want a vps-gpu CIQS baseline so that the S-43/S-44 latency and quality ACs can be validated on the real GPU hardware `P1` `S`

**Acceptance criteria:**
- [ ] AC-1: 3-run CIQS battery executed on vps-gpu mode against the live Hetzner GEX44 host; scored JSONL + summary markdown committed under `docs/benchmarks/`
- [ ] AC-2: Closes S-43 AC-2 (CIQS Category A delta ≥ +3 points vs v0.5.0 baseline) and S-43 AC-3 (p95 recall latency ≤ 1500 ms with 100-file corpus)
- [ ] AC-3: Closes S-44 AC-2 (p95 latency per capability recorded in the Phase 4 section of `docs/runbooks/gpu-vps-migration.md`)

**Tasks:**
- [ ] T-206: Execute 3-run baseline via `scripts/ciqs_harness.py --mode vps-gpu` after §4e of the GPU migration runbook
- [ ] T-207: Commit scored JSONL + summary + post-migration entry under `docs/runbooks/dogfood-reports/` referencing the specific hardware (GEX44 / RTX 4000 SFF Ada)

---

### S-65: As a maintainer, I want a dogfood-telemetry runbook so that `backend_summary()` + `capture_summary()` outputs from real sessions validate the observability layer shipped in v0.5.1/v0.5.2 `P1` `S`

**Acceptance criteria:**
- [ ] AC-1: Runbook in `docs/runbooks/dogfood-telemetry.md` prescribes: enable instrumentation, use DepthFusion for ≥ 1 week of real work, collect JSONL streams, run aggregators, inspect outputs
- [ ] AC-2: First dogfood pass committed as `docs/runbooks/dogfood-reports/{YYYY-MM-DD}-week1.md` with concrete findings (fields with empty values, fields that lied, missing fields we wish existed)
- [ ] AC-3: Findings triaged into v0.5.3 polish backlog (new stories under a fresh epic if warranted)

**Tasks:**
- [x] T-204: Author the runbook — `docs/runbooks/dogfood-telemetry.md` (252 lines; mental model + prereqs + daily protocol + aggregation incantations + analysis checklists for all four streams + triage workflow + report template + known limits)
- [ ] T-205: Execute the first pass on this repo; commit the report — **calendar-blocked ≥ 7 days**

---

## E-27: Memory Policy Layer [backlog]

> Per-discovery operator-controlled lifecycle policy: pinning, importance/salience scoring, bucketed decay, recall-feedback loop, and high-importance event hook. Augments E-09/E-11/E-20/E-21 by adding per-item policy on top of the existing file-system + capture pipeline.
>
> **Source:** Surfaced from a 2026-04-29 read-only audit comparing ClaudeClaw OS Memory v2 against DepthFusion's live surface — see `docs/depthfusion-feature-inventory.md` in the agent-ops repo (sibling project at `~/projects/agent-ops/`). The audit's full report and source prompt are at `docs/depthfusion-evaluation-prompt.md` and `docs/claudeclaw-feature-analysis.md` in the same repo. Most ClaudeClaw v2 features turned out either COVERED or NOT-APPLICABLE; this epic captures only what was confirmed missing in DepthFusion's own backlog.

### S-69: As an operator, I want to pin discoveries so that high-value entries are exempt from age-based pruning `P2` `S`

**Acceptance criteria:**
- [ ] AC-1: New YAML frontmatter field `pinned: bool` on discovery markdown (default `false` if absent — backward compatible).
- [ ] AC-2: `prune_discoveries` skips files where `pinned: true` regardless of age.
- [ ] AC-3: New MCP tool `depthfusion_pin_discovery(filename, pinned=true)` toggles the field; idempotent.
- [ ] AC-4: ≥ 4 tests covering pin/unpin/skip-during-prune/missing-file edge case.

**Tasks:**
- [ ] T-216: Extend frontmatter parser in `capture/` to read `pinned` (with default-false fallback)
- [ ] T-217: Update `analyzer/prune.py` (or equivalent) to honour `pinned` in candidate selection
- [ ] T-218: Register `depthfusion_pin_discovery` in `mcp/server.py`
- [ ] T-219: Author `tests/test_capture/test_pin.py`

### S-70: As a discovery, I want separate `importance` and `salience` scalars so that lifecycle policy can weigh intrinsic value distinctly from recent usefulness `P1` `M`

> **Foundational story.** S-71 (decay buckets), S-72 (recall feedback), and S-73 (high-importance hook) all depend on this landing first.

**Acceptance criteria:**
- [ ] AC-1: New frontmatter fields `importance: float ∈ [0.0, 1.0]` and `salience: float ∈ [0.0, 5.0]` on every discovery markdown. Defaults: `importance: 0.5`, `salience: 1.0` if not set.
- [ ] AC-2: Set at publish time by `publish_context` (operator-supplied) and at extract time by `auto_learn` / decision extractor (S-45) / negative extractor (S-48) / confirm_discovery (S-47) — extractors derive `importance` from their existing confidence score.
- [ ] AC-3: Backward compatible — existing discoveries without these fields are treated as defaults; no migration required.
- [ ] AC-4: New MCP tool `depthfusion_set_memory_score(filename, importance?, salience?)` for explicit operator overrides; idempotent.
- [ ] AC-5: ≥ 8 tests covering: defaults applied, extractor-derived values, operator override, backward-compat with old files, persistence across recall.

**Tasks:**
- [ ] T-220: Frontmatter schema additions in `capture/types.py` (or equivalent canonical types module)
- [ ] T-221: Default-derivation rules in each extractor (decision/negative/regex/Haiku) — confidence → importance mapping
- [ ] T-222: `publish_context` plumbing for explicit importance arg
- [ ] T-223: `depthfusion_set_memory_score` MCP tool
- [ ] T-224: Tests in `tests/test_capture/test_scoring.py`

### S-71: As a memory store, I want bucketed decay rates tied to `importance` so that high-value discoveries persist longer than transient ones `P2` `S`

> Depends on S-70.

**Acceptance criteria:**
- [ ] AC-1: Decay policy: pinned → 0 %/day, `importance ≥ 0.8` → 1 %/day, `≥ 0.5` → 2 %/day, `< 0.5` → 5 %/day. Decay applies to `salience`.
- [ ] AC-2: Hard-archive threshold: when `salience < 0.05`, file is moved to `.archive/` immediately on next prune cycle regardless of age.
- [ ] AC-3: Decay job runnable as `scripts/decay-job.py` (cron-friendly) or as a new MCP tool `depthfusion_apply_decay()`.
- [ ] AC-4: All four bucket boundaries + threshold are env-configurable (`DEPTHFUSION_DECAY_RATE_HIGH`, `_MID`, `_LOW`, `_HARD_ARCHIVE_THRESHOLD`).
- [ ] AC-5: ≥ 4 tests covering each bucket + the hard-archive case.

**Tasks:**
- [ ] T-225: Implement bucketed decay computation in `capture/decay.py` (new module)
- [ ] T-226: `scripts/decay-job.py` (calls decay, writes audit summary) + cron documentation
- [ ] T-227: Env-var plumbing in `core/config.py`
- [ ] T-228: Tests in `tests/test_capture/test_decay.py`

### S-72: As a recall caller, I want a feedback loop so that the system learns which surfaced chunks were actually useful `P1` `M`

> Depends on S-70.

**Acceptance criteria:**
- [ ] AC-1: `recall_relevant` response includes `recall_id` (uuid v4) per call.
- [ ] AC-2: A short-term store maps `recall_id → [chunk_id]` for at least 24 hours.
- [ ] AC-3: New MCP tool `depthfusion_recall_feedback(recall_id, used: chunk_id[], ignored: chunk_id[])` applies `salience += 0.1` per used and `-= 0.05` per ignored chunk.
- [ ] AC-4: Idempotent — replaying the same `recall_id + items` payload doesn't double-apply.
- [ ] AC-5: Salience changes are bounded (`max 5.0`, `min 0.0`).
- [ ] AC-6: ≥ 6 tests covering: id correlation, used/ignored signals, idempotency, bounds, expiry of unfetched recall_ids.

**Tasks:**
- [ ] T-229: Add `recall_id` to `recall_relevant` response shape
- [ ] T-230: Short-term recall-id store (in-memory dict with TTL eviction, or sqlite, depending on tier)
- [ ] T-231: Register `depthfusion_recall_feedback` in `mcp/server.py`
- [ ] T-232: Salience boost/decay applied to discovery frontmatter
- [ ] T-233: Idempotency guard (track applied `(recall_id, chunk_id)` pairs)
- [ ] T-234: Tests in `tests/test_analyzer/test_recall_feedback.py`

### S-73: As a consumer, I want a structured event when a discovery is published with high importance so that downstream systems can review high-stakes context as it's captured `P3` `S`

> Depends on S-70.

**Acceptance criteria:**
- [ ] AC-1: When a discovery is published with `importance ≥ 0.8`, append a JSONL line to `~/.claude/shared/depthfusion-events.jsonl` (path env-configurable via `DEPTHFUSION_EVENT_LOG`).
- [ ] AC-2: Event schema: `{timestamp, event: "high_importance_discovery", project, file_path, importance, salience, summary}`.
- [ ] AC-3: Threshold env-configurable (`DEPTHFUSION_HIGH_IMPORTANCE_THRESHOLD`, default 0.8).
- [ ] AC-4: Consumers tail the file or use inotify; DepthFusion does not own delivery (no Slack/webhook coupling here).
- [ ] AC-5: ≥ 3 tests covering threshold trigger, schema, env-var override.

**Tasks:**
- [ ] T-235: Event emitter in publish path (single emit point, after dedup + decay decisions)
- [ ] T-236: JSONL writer with daily rotation (size cap optional)
- [ ] T-237: Tests in `tests/test_capture/test_event_hook.py`

### S-78: As a publish caller, I want `publish_context` to actually persist items idempotently by `content_hash` so that retries on transient failures don't create duplicate context entries `P1` `M`

> **Cross-project blocker:** agent-ops ADR 0004 (DepthFusion publish retry policy) cannot accept option β (single retry on transient errors) until this story lands. Today, `_tool_publish_context` in `mcp/server.py:629-631` is a stub that echoes success without storing the item; even when it persists, `FileBus.publish()` (`router/bus.py:62-73`) does unconditional append and `ContextItem` (`core/types.py:34-43`) has no `content_hash` field. Without dedup-on-publish, agent-ops retries would create duplicate `bus.jsonl` rows that distort recall until the next prune cycle. See `~/projects/agent-ops/docs/decisions/0004-depthfusion-publish-retry.md` and the audit report at `~/projects/agent-ops/docs/depthfusion-feature-inventory.md`.

**Acceptance criteria:**
- [ ] AC-1: `_tool_publish_context` (`mcp/server.py`) calls a real `ContextBus.publish()` (DI-injected the same way `recall_relevant` is wired). The current stub return — `{"published": True, "item": item}` — is replaced.
- [ ] AC-2: `ContextItem` (`core/types.py`) gains `content_hash: str` field, computed as sha256 of `content` at construction time. Auto-derive in a factory function or `__post_init__` so callers cannot mismatch hash and content.
- [ ] AC-3: `FileBus.publish()` and `InMemoryBus.publish()` (`router/bus.py`) skip the append/insert when an item with the same `content_hash` already exists in the bus. Skip is silent — no exception, no log warning at default level.
- [ ] AC-4: The MCP tool response shape becomes `{published: bool, item_id: str, deduped: bool}` so callers can distinguish first-publish from retry-dedup. `published: true, deduped: false` = newly stored. `published: true, deduped: true` = idempotent hit (already present). The original `item_id` of the existing record is returned in the deduped case.
- [ ] AC-5: Idempotency is exact-content — bytewise-identical `content` produces the same hash and dedupes; any whitespace, casing, or metadata difference produces a different hash and is stored as a new item. Tag differences alone do not affect the hash.
- [ ] AC-6: Backward compatible — existing `bus.jsonl` rows written before this story (which lack `content_hash`) are loaded as legacy items with no hash, and are never matched for dedup. New rows include `content_hash`.
- [ ] AC-7: ≥ 8 tests covering: first publish stores; repeat publish dedupes and returns the original item_id; 1-character difference creates a new item; tag-only difference still dedupes; backward-compat load of pre-existing rows; concurrent publish of identical content (file-locking or read-then-write race) doesn't double-insert; MCP tool returns correct response shape; large content (>1 MB) hashes and persists correctly.

**Tasks:**
- [ ] T-255: Add `content_hash: str` field to `ContextItem` in `core/types.py` with sha256 auto-derivation (factory function `make_context_item(...)` or `__post_init__`)
- [ ] T-256: Implement dedup-on-publish in `FileBus.publish()` (`router/bus.py`) — maintain an in-memory hash index built from `bus.jsonl` on init, update on each successful append; handle the legacy-row case (rows with no `content_hash` are never indexed)
- [ ] T-257: Implement dedup-on-publish in `InMemoryBus.publish()` (`router/bus.py`) — simple set-based hash index
- [ ] T-258: Wire `_tool_publish_context` in `mcp/server.py` to call a DI-injected `ContextBus`, mirroring how `recall_relevant` resolves its dependencies
- [ ] T-259: Update MCP tool response shape to `{published: bool, item_id, deduped: bool}` and document the contract in the tool description string
- [ ] T-260: Author tests in `tests/test_router/test_bus_idempotency.py` (covers both `InMemoryBus` and `FileBus`; uses tmp_path for FileBus)
- [ ] T-261: Update `docs/runbooks/` (or equivalent) with the publish-API idempotency contract so consumers (agent-ops, future MCP clients) can rely on it

---

## E-28: Tier-1 Engagement Audit & Introspection Surface [backlog]

> Verify why graph subsystems (E-11) and embedding-augmented recall (E-19/S-43) don't engage on `vps-tier1` despite being code-complete and env-flagged on, then add MCP introspection so operators can tell what's running without reading source.
>
> **Source:** Same 2026-04-29 audit as E-27. The live `vps-tier1` deployment showed `graph_status` returning 0/0/{} after 44 sessions with `DEPTHFUSION_GRAPH_ENABLED=true` set, and `recall_relevant` reporting only `BM25+RRF` despite `DEPTHFUSION_EMBEDDING_BACKEND=local` being set. Either is by design (and currently undocumented from the MCP surface) or it's a deployment / wiring gap; this epic resolves the ambiguity.

### S-74: As an operator, I want the vps-tier1 graph engagement state explained or fixed so that empty graphs after dozens of sessions aren't ambiguous `P2` `S`

**Acceptance criteria:**
- [ ] AC-1: Reproduce: confirm whether a fresh vps-tier1 install with auto_learn invocations populates the graph or leaves it empty.
- [ ] AC-2: Triage to one of: (a) by design — graph extraction is gated to vps-gpu; (b) configuration gap on this deployment; (c) silent extraction failure (e.g., Haiku not invoked from auto_learn on tier-1).
- [ ] AC-3: If (a): document in `docs/runbooks/tier-feature-matrix.md` + update `graph_status` response to surface `extraction_active: bool` and `tier_gates_extraction: bool`.
- [ ] AC-4: If (b) or (c): fix and add a regression test.

**Tasks:**
- [ ] T-238: Reproduce in a fresh vps-tier1 dev install
- [ ] T-239: Read `capture/auto_learn.py` and `graph/extractor.py` to confirm tier gating
- [ ] T-240: Document or fix per AC-3 / AC-4
- [ ] T-241: Update `graph_status` MCP response if (a)

### S-75: As an operator, I want the vps-tier1 embedding-recall engagement state explained or fixed so that `EMBEDDING_BACKEND=local` doesn't silently no-op `P2` `S`

**Acceptance criteria:**
- [ ] AC-1: Reproduce: confirm whether `recall_relevant` ever invokes vector search on vps-tier1 with `DEPTHFUSION_EMBEDDING_BACKEND=local`.
- [ ] AC-2: Triage to: (a) by design — semantic recall gated to vps-gpu (S-43 only); (b) wiring gap; (c) embedding model not loaded.
- [ ] AC-3: If (a): document in `docs/runbooks/tier-feature-matrix.md`. Update recall response to include `engaged_layers: ["bm25", ...]` (see S-76).
- [ ] AC-4: If (b) or (c): fix and benchmark p95 latency impact on tier-1.

**Tasks:**
- [ ] T-242: Reproduce + log inspection of recall path
- [ ] T-243: Read `retrieval/hybrid.py` `apply_vector_search()` to confirm tier gating
- [ ] T-244: Document or fix per AC-3 / AC-4
- [ ] T-245: Benchmark if engagement is enabled on tier-1

### S-76: As an MCP consumer, I want introspection tools so that I can tell which retrieval layers and capture mechanisms are engaged in a given recall or publish without reading source `P2` `S`

**Acceptance criteria:**
- [ ] AC-1: `recall_relevant` response includes a new field `engaged_layers: string[]` listing the layers that contributed (subset of `["bm25", "embedding", "graph_traverse", "reranker"]`).
- [ ] AC-2: New MCP tool `depthfusion_describe_capabilities()` returns: `{tier, mode, engaged_layers_per_op: {recall: [...], publish: [...], confirm_discovery: [...], ...}}`.
- [ ] AC-3: Tool descriptions for `publish_context` and `confirm_discovery` document the input schema explicitly (currently absent from MCP surface).
- [ ] AC-4: New optional MCP tool `depthfusion_inspect_discovery(filename)` returns parsed frontmatter (importance, salience, pinned, project, etc.) — useful once S-69 + S-70 land.
- [ ] AC-5: ≥ 4 tests.

**Tasks:**
- [ ] T-246: Add `engaged_layers` to `RecallPipeline` response
- [ ] T-247: New `depthfusion_describe_capabilities` MCP tool
- [ ] T-248: Augment tool descriptions for publish_context + confirm_discovery
- [ ] T-249: New `depthfusion_inspect_discovery` MCP tool (gated on S-69 / S-70 frontmatter)
- [ ] T-250: Tests in `tests/test_analyzer/test_introspection.py`

### S-77: As an operator, I want `compress_session` and `auto_learn` to fire on a configurable cadence so that the capture pipeline doesn't depend on session-end memory `P3` `S`

**Acceptance criteria:**
- [ ] AC-1: New env var `DEPTHFUSION_AUTO_COMPRESS_HOURS` (default unset = manual only); when set, idle sessions older than N hours are compressed automatically.
- [ ] AC-2: Implementation may use the existing Stop hook (`hooks/depthfusion-stop.sh` per S-45 T-138), a cron entry shipped via the installer, or both — no internal scheduler.
- [ ] AC-3: Idle detection: no session-file writes in the last N hours.
- [ ] AC-4: Logged via the existing observability stream (capture_summary).
- [ ] AC-5: ≥ 3 tests.

**Tasks:**
- [ ] T-251: Idle detection in `capture/compress_session.py`
- [ ] T-252: Cron entry template + installer hook (or extend Stop hook with cadence parameter)
- [ ] T-253: Env-var plumbing
- [ ] T-254: Tests in `tests/test_capture/test_auto_compress.py`

### Cross-cutting notes for E-27 / E-28

- **Dependency graph:** S-70 blocks S-71, S-72, S-73 (all rely on importance/salience fields). S-69 is independent. S-74 and S-75 must precede S-76 so the `engaged_layers` documentation reflects real tier behaviour. S-77 is independent.
- **Effort summary:** E-27 ~ 1 P1-M (S-70) + 1 P1-M (S-72) + 1 P2-S (S-69) + 1 P2-S (S-71) + 1 P3-S (S-73) ≈ 1 week. E-28 ~ 4 P2-S + 1 P3-S ≈ 3-4 days. Total: ~ 2 weeks at a relaxed pace.
- **Items deliberately out of scope** (covered in `docs/claudeclaw-feature-analysis.md` §7 in the agent-ops repo): War Room voice, Telegram bot per agent, Pika video meeting, TTS/STT cascades, PIN lock, launchd/systemd plist generation. ClaudeClaw-style consumer features belong in agent-ops or a future ClaudeClaw-shaped peer; they should not land in DepthFusion.
- **Confirmed already shipped, no new story needed** (audit findings that landed in earlier epics):
  - Embedding-based dedup at `cos-sim ≥ 0.92` (S-49 in E-20)
  - Knowledge graph with 8 entity types + Haiku linker incl. `CONFLICTS_WITH` and `REPLACES` edges (S-14–S-21 in E-11) — supersession is covered for graph entities
  - Pattern recognition / consolidation insights via `compress_session` (E-08)
  - Local embedding backend wired into hybrid retrieval (S-43 in E-19) — but see S-75 for tier-1 engagement
- **Side-channel finding from the audit (worth a separate look, not a story here):** `DEPTHFUSION_API_KEY` is exposed in env to any process inheriting the shell. Normal for self-hosted services, but verify the value isn't echoed into discovery files, recall responses, or the new event log proposed in S-73.

---

- **Sequencing inversion (resolved 2026-04-16):** Build plan sequenced v0.3.1 before v0.4.0. Initial backlog review (2026-04-15) concluded v0.3.1 was unlanded. However, RECALL via the 2026-03-28 discovery file revealed that v0.3.1 scoring fixes *were* implemented inline in `mcp/server.py` during a prior `/goal` run — they just weren't separate commits. Code review on 2026-04-16 confirmed BM25 normalization, 1500-char snippets, source weights, directory-based classification, recency tie-breaker, and both SessionStart + PostCompact hooks are all operational.
- **`MEMPALACE DEPTHFUSION ANALYSIS PROMPT.pdf`** in `docs/` is untracked; unclear whether it is a draft epic, analysis input, or reference. Triage before next backlog update.
- **`docs/Account_synch/`** is the canonical planning source. Changes to the plan should be made there, with a note that `BACKLOG.md` must be updated in the same commit.
