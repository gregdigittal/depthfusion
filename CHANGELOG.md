# Changelog

All notable changes to DepthFusion are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/) with project-specific adjustments (inline T-/S-/E- backlog references).

Conventions:
- Dates in ISO (YYYY-MM-DD)
- Version anchors: `## [Unreleased]`, `## [v0.5.0] — YYYY-MM-DD`
- Sections per release: Added / Changed / Deprecated / Removed / Fixed / Security
- Backlog cross-references in parentheses: `(T-115)`, `(S-41, S-42)`, `(E-18)`

---

## [Unreleased]

No changes pending beyond v0.5.0.

---

## [v0.5.0] — 2026-04-20

**Theme:** pluggable LLM backends, three-mode installer, Category D capture
mechanisms, selective retrieval quality.

v0.5 is the largest single-release feature delta since v0.3.0. It refactors
every LLM call-site through a provider-agnostic backend protocol, adds a
three-mode installer (`local` / `vps-cpu` / `vps-gpu`), ships five new
capture mechanisms for architectural decisions and commit metadata, and
adds three retrieval-quality filters (project-scoped recall, temporal graph
edges, Mamba B/C/Δ fusion gates).

From 439 tests at v0.3.1 to **887 tests at v0.5.0**. All new features are
opt-in or byte-identical-by-default with v0.4.x — the `DEPTHFUSION_*` flag
matrix below controls what activates.

### Epic summary

| Epic                        | Stories                         | Status |
|-----------------------------|---------------------------------|--------|
| E-18 Backend Foundation     | S-41, S-42                      | Closed |
| E-19 GPU-Enabled LLM Routing| S-43, S-44                      | Code complete; CIQS/latency ACs benchmark-pending |
| E-20 Capture Mechanisms     | S-45, S-46, S-47, S-48, S-49    | Code complete; precision/false-rate ACs require labelled eval |
| E-21 Retrieval Quality      | S-50, S-51, S-52                | Closed |

### Added

**Backend protocol + factory (S-41, T-115..T-123, E-18):**
- `backends/base.py` — `LLMBackend` `Protocol` with `complete` / `embed` / `rerank` / `extract_structured` / `healthy`; typed errors `RateLimitError`, `BackendOverloadError`, `BackendTimeoutError`, `BackendExhaustedError`.
- `backends/null.py` — terminal fallback; always healthy; returns safe degenerate values.
- `backends/haiku.py` — Anthropic Haiku with explicit `api_key=DEPTHFUSION_API_KEY` (closes C2 billing-isolation hazard from v0.4.x).
- `backends/gemma.py` — vLLM HTTP client via stdlib `urllib.request`, typed 429/503/529/timeout translation (T-132).
- `backends/local_embedding.py` — sentence-transformers wrapper, default `all-MiniLM-L6-v2`; lazy model load behind `threading.Lock`; healthy check uses `importlib.util.find_spec` (no model load) (T-118/T-129).
- `backends/factory.py` — `get_backend(capability, mode)` with per-capability env-var overrides and healthy-check fallback to `NullBackend`; emits `backend.fallback` JSONL event on downgrade (T-123).
- Six capabilities routed through the factory: `reranker`, `extractor`, `linker`, `summariser`, `embedding`, `decision_extractor`.

**Three-mode installer (S-42, T-124..T-128):**
- `install/install.py` — `--mode={local,vps-cpu,vps-gpu}` with deprecated `vps`→`vps-cpu` alias; `--skip-gpu-check` CI flag with stray-flag warning.
- `install/gpu_probe.py` — `detect_gpu()` via `nvidia-smi` subprocess (2s timeout); never raises; `GPUInfo` frozen dataclass.
- `install/smoke.py` — `run_smoke_test()` writes 5-file synthetic corpus, runs BM25 query, asserts known target ranks first.
- `pyproject.toml` — `[local]`, `[vps-cpu]`, `[vps-gpu]` optional-dependencies extras; legacy `vps-tier1`/`vps-tier2` aliases retained.

**Capture mechanisms (E-20):**
- `capture/decision_extractor.py` — LLM-based decision extractor (S-45/CM-1); heuristic fallback; idempotent write to `{date}-{project}-decisions.md`.
- `capture/negative_extractor.py` — extracts "X did not work because Y" entries tagged `type: negative` (S-48/CM-6).
- `capture/dedup.py` — embedding-based discovery deduplication; cos-sim ≥ 0.92 → older file renamed `.superseded`; project-scoped; strict "never dedup projectless files" for conservative correctness (S-49/CM-2).
- `hooks/git_post_commit.py` — opt-in git post-commit hook writing `{date}-{project}-commit-{sha7}.md`; idempotent; never blocks commits (S-46/CM-3).
- `scripts/install-git-hook.sh` — per-repo opt-in installer; detects existing hooks and appends rather than overwrites.
- `mcp/server.py` — new tool `depthfusion_confirm_discovery` for session-time active confirmation (S-47/CM-5).
- `hooks/depthfusion-stop.sh` — Stop hook runs `SessionCompressor` on most-recent `.tmp` file.

**Retrieval quality (E-21):**
- `retrieval/hybrid.py` — `extract_frontmatter_project()` + `filter_blocks_by_project()`; `_tool_recall` accepts `cross_project: bool` and `project: str` args with path-traversal-safe slug sanitisation (S-52/T-160/T-161).
- `retrieval/hybrid.py` — `apply_vector_search()` uses `LocalEmbeddingBackend.embed()` and fuses with BM25 via existing `rrf_fuse` (T-130).
- `retrieval/hybrid.py` — `apply_fusion_gates()` runs the three-stage Mamba B/C/Δ filter; gated on `DEPTHFUSION_FUSION_GATES_ENABLED`; fail-open on error and on empty survivors (S-51/T-157).
- `graph/linker.py` — `SessionRecord` dataclass + `TemporalSessionLinker` producing `PRECEDED_BY` edges between sessions close in time AND sharing vocabulary; direction normalised to "later PRECEDED_BY earlier" with session-id tie-break on identical timestamps (S-50/T-153).
- `graph/traverser.py` — `time_window_hours` parameter on `traverse()` filters edges by `metadata["delta_hours"]`; non-temporal edges bypass the filter for back-compat (T-154).
- `graph/types.py` — 8th edge kind `PRECEDED_BY` documented in the `Edge` docstring.
- `fusion/gates.py` — NEW module: `GateConfig` (α default 0.30 per TG-11), `GateDecision`, `GateLog` frozen dataclasses; `SelectiveFusionGates` class; base_scores normalised to percentile [0,1] before the α blend so the B signal is not drowned out by raw BM25 magnitudes.

**Observability:**
- `metrics/collector.py` — `record_gate_log()` writes to `YYYY-MM-DD-gates.jsonl` with `fcntl.flock` against concurrent-writer interleaving; `_json_default` coerces numpy scalars to Python floats (no silent stringification). `fallback_triggered` field flags entries where the retrieval layer overrode the gate verdict (T-158/LOW-7).

**Planning artefacts:**
- `docs/plans/v0.5/01-assessment.md` through `06-*.md` — feature assessment, 15-task-group build plan with per-TG acceptance criteria, SkillForge integration spec, rollout runbook, commit strategy, backlog proposal. AC-01-8 post DR-018 ratification for quality-ranked fallback order. Invariant rows I-8/I-9/I-10/I-11 ratified per DR-018 §4.

### Changed

- `analyzer/installer.py` — extended to document the git-hook opt-in step; recommends per-repo hook install during analysis (T-142).
- `mcp/server.py` — 11 → 12 tools (added `depthfusion_confirm_discovery`).
- `retrieval/hybrid.py` — `_tool_recall` filter path applied BEFORE BM25 scoring so IDF weights reflect the filtered corpus (S-52).
- `capture/auto_learn.py` — Phase 2b dedup integration after extractor writes (T-150); gated on `DEPTHFUSION_DEDUP_ENABLED` (default true, safe no-op when embedding backend unavailable).
- `pyproject.toml` — project version 0.3.0 → 0.5.0.

### New environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEPTHFUSION_RERANKER_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_EXTRACTOR_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_LINKER_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_SUMMARISER_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_EMBEDDING_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_DECISION_EXTRACTOR_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_DECISION_EXTRACTOR_ENABLED` | `false` | Gates the LLM decision extractor + negative extractor. |
| `DEPTHFUSION_GEMMA_URL` / `DEPTHFUSION_GEMMA_MODEL` | see gemma.py | vLLM endpoint + model. |
| `DEPTHFUSION_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | sentence-transformers model name. |
| `DEPTHFUSION_DEDUP_ENABLED` | `true` | Opt-out of embedding-based discovery dedup. |
| `DEPTHFUSION_DEDUP_THRESHOLD` | `0.92` | Cosine similarity for dedup. |
| `DEPTHFUSION_FUSION_GATES_ENABLED` | `false` | Opt-in to Mamba B/C/Δ gates. |
| `DEPTHFUSION_FUSION_GATES_ALPHA` | `0.30` | AttnRes α blend weight. |
| `DEPTHFUSION_FUSION_GATES_B_THRESHOLD` | `0.10` | B gate floor. |
| `DEPTHFUSION_FUSION_GATES_C_THRESHOLD` | `0.05` | C gate floor. |
| `DEPTHFUSION_FUSION_GATES_DELTA_THRESHOLD` | `0.0` | Δ gate floor (normalised scale). |
| `DEPTHFUSION_BACKEND_FALLBACK_LOG` | `true` | Emit `backend.fallback` events. |
| `DEPTHFUSION_PROJECT` | — | Override auto-detected project slug. |

### Fixed

- **C2 billing-isolation hazard (S-41 AC-2):** v0.4.x `HaikuBackend` constructed `anthropic.Anthropic()` with no explicit `api_key=`, falling back to the SDK's `ANTHROPIC_API_KEY` lookup — which silently switched Claude Code's billing from Pro/Max subscription to pay-per-token. v0.5 always passes `api_key=DEPTHFUSION_API_KEY` explicitly.
- **Path-traversal risk in `depthfusion_confirm_discovery` and `depthfusion_recall_relevant`:** externally-supplied `project` slugs now pass through `_sanitise_project_slug()` (lowercase + `[a-z0-9-]` allowlist, capped at 40 chars) before reaching `write_decisions()` or the filter comparison.
- **`detect_project()` "unknown" sentinel handling:** in a bare-MCP context with no git remote and no env var, `detect_project()` returns the literal string `"unknown"`. v0.4.x code that filtered against this sentinel would silently zero out recall. v0.5 treats `"unknown"` as "no project context" and skips the filter.

### Deprecated

- `--mode=vps` — alias for `--mode=vps-cpu`. Emits `[DEPRECATION]` warning on stderr. Removal target: v0.6.
- `pyproject.toml` extras `vps-tier1` / `vps-tier2` — replaced by `local` / `vps-cpu` / `vps-gpu`. Removal target: v0.6.

### Security

- Gate log emits `query_hash` (sha256[:12]) only — raw query text is never written to disk (T-158).
- All post-commit hook subprocess calls have explicit timeouts (`timeout=4` for git commands, `timeout=15` for installer).
- `nvidia-smi` probe uses 2s timeout; `smi_path` resolved via `shutil.which`; subprocess called with list-form args (no shell injection vector).

### Not yet measured (benchmark-blocked ACs)

These ACs are code-complete but require running the shipped code against live infrastructure or labelled eval corpora to close. Tracked for the v0.5.1 measurement pass:
- S-43 AC-2/AC-3: CIQS Category A delta ≥ +3 on vps-gpu; p95 recall latency ≤ 1500ms.
- S-44 AC-2/AC-3/AC-4: p95 latency per capability on vps-gpu; live chain-level fallback integration.
- S-45 AC-1: decision-extractor precision ≥ 0.80 on 50-session labelled set.
- S-48 AC-2: negative-extractor false-negative rate ≤ 10% on labelled set.
- S-49 AC-2: dedup false-positive rate ≤ 5% on 30 near-duplicate pairs.
- S-50 AC-3: CIQS Category D delta ≥ +2 on "recent work" questions.
- S-51 AC-1: CIQS Category A delta ≥ +2 on vps-cpu; ≥ +3 on vps-gpu.
- S-42 AC-5: `pip install --dry-run` conflict-check on all three extras (structural test is green; live run deferred).

### Upstream dependencies

- `docs/research/DR-018_LEGACY_INVARIANT_REINSTATEMENT.md` — ratified 2026-04-18. Five per-legacy-invariant verdicts locked; cascaded amendments applied to v0.5 planning docs and to the gate-log record shape (`config_version_id` field present on every `record_gate_log()` entry per I-8 ratification).

### Test metrics

- **887 passed, 1 skipped** (sentence-transformers integration test; gated by `pytest.importorskip`).
- **Ruff clean** on all code introduced in v0.5.
- **Mypy:** zero errors introduced by v0.5 work; 6 pre-existing errors untouched.
- Test count delta: v0.3.1 baseline 439 → v0.5.0 887 (+448 net new across the v0.5 release window).

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
