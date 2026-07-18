# Changelog

All notable changes to DepthFusion are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/) with project-specific adjustments (inline T-/S-/E- backlog references).

Conventions:
- Dates in ISO (YYYY-MM-DD)
- Version anchors: `## [Unreleased]`, `## [v0.5.0] — YYYY-MM-DD`
- Sections per release: Added / Changed / Deprecated / Removed / Fixed / Security
- Backlog cross-references in parentheses: `(T-115)`, `(S-41, S-42)`, `(E-18)`

---

## [Unreleased]

### Added

**E-68 — Layered Memory Augmentation (TencentDB Agent Memory-inspired):**
- `src/depthfusion/cognitive/distillation_client.py`: `DistillationClient(config)` — configurable AI distillation backend; probes `DEPTHFUSION_LOCAL_LLM_URL` in `auto` mode, falls back to Haiku; `DEPTHFUSION_DISTILLATION_BACKEND=auto|local|haiku` (S-228)
- `src/depthfusion/cognitive/persona.py`: `PersonaEngine` — auto-generates L3 user persona every `persona_trigger_every_n` (default 50) new memories; writes `~/.claude/shared/discoveries/persona-{project_id}.md`; wired into `depthfusion_capture` ingestion path (S-229)
- `src/depthfusion/cognitive/scenario.py`: `ScenarioEngine` — clusters memories by cosine similarity + 24 h time window into L2 scene blocks; writes `scenarios-{project_id}.md`; triggered post-persona (S-230)
- `src/depthfusion/cognitive/offloader.py`: `ContextOffloader` — offloads verbose blobs to `~/.claude/shared/refs/{session_id}/{node_id}.md`; returns compact Mermaid node refs `ref_{id}[/"📎 ctx:{id}"/]` (S-231)
- `src/depthfusion/mcp/tools/bridge.py`: `node_id` retrieval path — `depthfusion_bridge node_id=<id>` returns raw blob without LLM call (S-231)
- `src/depthfusion/mcp/tools/capture.py`: `depthfusion_compress_session` extended to produce Mermaid task canvas; `PersonaEngine.maybe_trigger()` wired into ingestion (S-229, S-231)
- `src/depthfusion/mcp/tools/recall.py`: `include_persona` and `include_scenarios` params on `depthfusion_recall_relevant` (S-229, S-230)
- `src/depthfusion/mcp/tools/system.py`: `depthfusion_status` reports `distillation_backend`, `persona_last_updated`, `offload_enabled`, `refs_count` (S-228, S-229, S-231)
- `src/depthfusion/core/config.py`: `distillation_backend`, `local_llm_url`, `persona_trigger_every_n`, `offload_enabled`, `offload_mmd_max_tokens` fields (S-228, S-231)
- `tests/test_cognitive/test_distillation_client.py`: 15 tests (S-228)
- `tests/test_cognitive/test_persona_engine.py`: 25 tests (S-229)
- `tests/test_cognitive/test_scenario_engine.py`: 67 tests (S-230)
- `tests/test_cognitive/test_offloader.py`: 16 tests + 14 security path-traversal cases (S-231)

### Security

- `src/depthfusion/cognitive/offloader.py`: `_assert_confined()` — `path.resolve().relative_to(refs_base)` confinement check before any read or write; `PermissionError` on escape (S-231)
- `src/depthfusion/mcp/tools/bridge.py`: allowlist regex `^[A-Za-z0-9_\-]+$` on `node_id` / `session_id` at MCP boundary before reaching `ContextOffloader` (S-231)

---

## [v2.2.0] — 2026-07-12

### Added

**E-65 — Auth infrastructure & first-run setup wizard (final ACs):**
- `app/src/__tests__/auth.test.ts`: 3 new tests for OIDC deep-link callback flow — `depthfusion://callback` → `authenticated` state transition, error path when `handle_deep_link` exchange fails, non-matching URL guard (S-216 AC-4, S-217 AC-3)
- Existing Rust vault test `valid_key_round_trips_through_the_vault` in `app/src-tauri/src/auth/local.rs` confirmed as vault round-trip AC coverage (S-215 AC-3)

**E-67 — Claims-reality rectification:**
- `tests/mcp/test_dispatch_parity.py`: `DISPATCHABLE` frozenset in `server.py`; parity test asserts tool name set matches the frozenset (S-219)
- `src/depthfusion/core/config.py`: `fusion_gates_enabled`, `cognitive_scoring_enabled` bool fields; `from_profile()` classmethod (S-220, S-224)
- `src/depthfusion/core/profiles.py`: four named configuration profiles — `minimal`, `standard`, `server`, `research` (S-224)
- `tests/core/test_profiles.py`: 12 tests covering profile overrides and `from_profile()` keyword-arg precedence (S-224)
- `src/depthfusion/mcp/http_server.py`: Fernet `CacheManager` wired to `/api/v1/search`; principal-isolated cache keys via `_principal_id_from_auth()` — `sha256(principal_id + "\x00" + q + "\x00" + limit)[:32]` (S-225)
- `tests/test_http_mcp/test_search_cache.py`: 8 cache isolation tests (S-225)
- `src/depthfusion/cognitive/consolidator.py`: embedding cosine similarity in `MemoryConsolidator`; cross-scope merge guard prevents cross-project memory bleed (S-226)
- `tests/test_cognitive/test_consolidator_embeddings.py`: 18 tests (S-226)
- `tests/fixtures/recall_goldset_v2.jsonl`: 200-entry goldset with graded relevance (0=distractor, 1=secondary, 2=primary) (S-227)
- `scripts/benchmark.py`: MRR@10 and nDCG@5 rank-aware retrieval metrics (S-227)
- `docs/benchmarks/2026-07-11-standard-vs-research-goldset-v2.md`: standard vs. research profile comparison report (S-227)

### Changed

- `README.md`: three-tier feature-status table (On by default / Behind flag / Projected), measured benchmark values MRR@10=1.0000, nDCG@5=0.9934 (S-221, S-222, S-227)
- `.github/workflows/release-desktop.yml`: `releaseDraft: false` — releases now publish immediately rather than as drafts (S-223)
- `tests/mcp/test_status_flags.py`: `depthfusion_status` tool reflects all `DepthFusionConfig` boolean fields via `dataclasses.fields()` reflection (S-221)
- `tests/retrieval/test_hybrid_gates_config.py`: `fusion_gates_enabled`/`cognitive_scoring_enabled` read from config object, not `os.environ` directly (S-220)
- Version bumped `2.1.1` → `2.2.0` in `pyproject.toml`, `app/package.json`, `app/src-tauri/tauri.conf.json`

---

## [v2.1.1] — 2026-06-23

### Added

**E-66 — ChatGPT Desktop macOS MCP Integration (v2.1.1):**
- `docs/chatgpt-mcp-setup.md` — full setup guide: config file path, token retrieval, tool reference table (30 tools; 11 marked Claude Code–only), troubleshooting (401, SSE drops, config not picked up)
- `docs/chatgpt-install.sh` — one-step Python install script (no heredoc); prompts for `DEPTHFUSION_MCP_TOKEN` via `getpass`, writes `~/Library/Application Support/com.openai.chat/mcp.json` with mode 0o600 and directory mode 0o700
- README "ChatGPT Desktop Integration" section with one-step install command and manual JSON snippet
- `BACKLOG.md` E-66 epic: S-218 story (T-756–T-759) marked done

**E-65 — MCP HTTP/SSE server auth hardening:**
- `src/depthfusion/mcp/http_server.py`: fail-closed Bearer token auth on `/sse` and `/messages`; JWKS JWT validation when OIDC env vars are set; static `DEPTHFUSION_MCP_TOKEN` fallback; timing-safe comparison via `secrets.compare_digest`
- `src/depthfusion/api/auth.py`: `_LegacyTokenDep.__call__` uses `secrets.compare_digest` to prevent Bearer token prefix leakage via short-circuit equality

**Other:**
- `docs/install/mac-mlx-quickstart.md` — complete install guide for Apple Silicon Macs: launchd plist setup, MLX-LM inference server, Claude Desktop + Claude Code CLI registration, HNSW cold-start note, troubleshooting section (launchctl load pitfall, duplicate plist labels, zsh paste issues)
- `docs/install/README.md` updated — Mac MLX guide added to the install guide table
- `scripts/mac-parity.sh` — idempotent plistlib-based script to add missing E-31 env vars (`DEPTHFUSION_GRAPH_ENABLED`, `DEPTHFUSION_COGNITIVE_RETRIEVAL`, `DEPTHFUSION_DECISION_MEMORY`, `DEPTHFUSION_OPERATIONAL_MEMORY`) to the macOS launchd plist and reload the service; supports `--dry-run`

### Changed

- **Default server URL** updated to `https://mcp.tonracein.com` (port 7301) in Tauri desktop app settings (v2.1.1).
- **Canonical 21-tool set** — parity audit removed 11 low-value / unshipped tools from the MCP server. All platforms now expose exactly 21 tools. Removed: `depthfusion_run_recursive`, `depthfusion_tier_status`, `depthfusion_describe_capabilities`, `depthfusion_get_cognitive_state`, `depthfusion_inspect_discovery`, `depthfusion_prune_discoveries`, `depthfusion_hnsw_capability`, `depthfusion_surface_skill_candidates`, `depthfusion_event_publish`, `depthfusion_event_seed`, `depthfusion_agent_trail`. Underlying Python functions are retained; only the MCP surface registration was removed.
- **REST API feature-flag bug fix** — all 16 endpoint handlers in `api/rest.py` called `DepthFusionConfig()` (bare constructor, all flags False) instead of `DepthFusionConfig.from_env()`. Feature-flagged tools (`graph_*`, `cognitive_retrieval`, `decision_memory`, `operational_memory`) were silently excluded from REST responses on all platforms regardless of env vars. Fixed with global replacement.

### Fixed

- **REST `/context` endpoint wiring bug** — `POST /context` now wraps the request body into the `arguments["item"]` shape expected by `_tool_publish_context`. Previously all REST publish calls returned `{"error": "publish_context: 'item' must be an object"}`. The endpoint now generates an `item_id` (UUID), uses `"rest-api"` as `source_agent`, and folds `project`/`session_id` into `item.metadata`. MCP tool (`depthfusion_publish_context`) was unaffected.

### Security

**E-66 — Path confinement and token security:**
- `src/depthfusion/mcp/tools/graph.py` (`_tool_set_memory_score`, `_tool_pin_discovery`): path confinement — resolves the caller-supplied filename and rejects any path outside `~/.claude/shared/discoveries/`. Prevents external callers (e.g. ChatGPT MCP) from writing to arbitrary server paths.
- `docs/chatgpt-install.sh`: install script rewrote to use `getpass.getpass` (removes hardcoded token), sets file permissions to 0o600 and directory to 0o700.
- Token rotation: `DEPTHFUSION_MCP_TOKEN` rotated after prior token was committed to public repo (now prompts interactively, never hardcodes).

E-61 / T-684 — pentest remediation (all 4 High/Medium findings from AV-01–AV-05):

- **F-001 (High)** — `token_validator.py`: `validate(token, nonce=None)` now raises `TokenInvalidError` when the token carries a `nonce` claim but the caller omitted `nonce=`. Previously a replay-eligible token was silently accepted; callers that don't use nonces must explicitly opt out.
- **F-002 (High)** — `authz/classification.py` + `roles.py`: dual `Role` enum vocabulary mismatch blocked `MEMBER`-role principals from `INTERNAL`-classified records they explicitly owned. `classification.Role.MEMBER` added and mapped into `CLASSIFICATION_POLICY[INTERNAL].allowed_roles`. Role hierarchy inversion is resolved.
- **F-006 (Medium)** — `cache/manager.py`: `CacheManager(key=None)` now emits a `WARNING` log and the server startup path loads `DEPTHFUSION_CACHE_KEY` (base64-encoded 32-byte Fernet key) from the environment. Ephemeral auto-generated key behaviour is preserved for test environments only.
- **F-008 (High)** — `cache/lease_lifecycle.py`: `PurgeEngine._high_water_mark` is now persisted to and reloaded from the lease store metadata on construction, closing the restart+clock-rollback attack window. A process restart no longer resets HWM to `0.0`.
- **Test regression guard** — `tests/test_security_t684.py`: `TestF001NonceReplayBypass` teardown used a "late capture" pattern that left `token_validator._jwk_to_public_key` pointing at a `MagicMock` after each test, polluting later test runs. All three async tests now restore the original function via `validator._jwk_to_public_key_patch` (the stash written by `_make_validator()`). Full suite: 3448 passed, 36 skipped.

---

## [v0.6.0-alpha] — 2026-05-23

**Theme:** Event Graph Fabric — multi-agent shared memory, agent provenance graph, `fabric_seed` cold-start mode. Covers E-46.

### Added

**Event Graph Fabric (E-46):**
- `S-141` `event` Entity type and four new edge relationships (`AGENT_PUBLISHED`, `AGENT_RECEIVED`, `SAME_SESSION_AS`, `DERIVED_FROM`) added to the knowledge graph vocabulary; `StreamBackend` Protocol + `RedisStreamBackend` (Redis Streams XADD/XREAD); `EventStore` class with `publish()`, `get_recent_events()`, `subscribe_stream()`, graceful Redis degradation (best-effort stream, graph write always succeeds)
- `S-142` REST endpoints: `POST /v1/events/publish`, `GET /v1/events/stream` (SSE), `GET /v1/events/seed`; `DEPTHFUSION_API_TAILSCALE=1` bind on Tailscale interface IP (loopback always active; token required for non-loopback per infra-exposure.md); `redis>=5.0` added to new `fabric` optional-dependency extra
- `S-143` `fabric_seed` mode for `depthfusion_session_seed` MCP tool — cold-start context bundle ranked by `recall_relevance × recency_decay × log(1+observer_count)`; write-path content-hash deduplication (100 concurrent publishes of identical content → 1 MemoryEntity + 100 EventEntities); three new MCP tools: `depthfusion_event_publish`, `depthfusion_event_seed`, `depthfusion_agent_trail`
- `S-144` Provenance query endpoints: `GET /v1/graph/agent/{agent_id}/trail` (all AGENT_PUBLISHED/AGENT_RECEIVED events for an agent, time-filtered), `GET /v1/graph/memory/{entity_id}/observers` (distinct agents with AGENT_RECEIVED edges, with timestamps)
- `S-145` Performance baselines: publish p99 = 30ms (SLA < 500ms, 16× headroom); `fabric_seed` p99 = 110ms (SLA < 2s, 18× headroom); `/trail` p99 = 50ms (SLA < 500ms, 9× headroom); `/observers` p99 = 8.5ms (SLA < 500ms, 59× headroom); graceful degradation verified; results in `docs/performance/event-graph-baseline-2026-05-23.md`
- `S-146` Documentation: README "Shared Memory Fabric" section with 5-command curl quickstart; `docs/fabric/tailscale-setup.md`, `docs/fabric/api-reference.md`, `docs/fabric/kafka-flink-migration.md`

### Fixed

- **CI lint/type errors in E-46 code** — two post-release fix commits resolved all ruff and mypy issues introduced by the fabric feature:
  - `bench_degradation.py`, `bench_fabric_seed.py`, `bench_publish_sse.py`: removed unused imports (F401) and fixed import sort order (I001)
  - `bench_provenance_queries.py`: E501 line-length violations in comment and print statement fixed
  - `event_store.py`: F401 (`asynccontextmanager`), I001 import order, E501 on `_event_entity_id` signature; mypy `Cannot infer type of lambda` fixed (closure capture replaces default-arg capture); mypy `Coroutine has no __aiter__` fixed by changing `StreamBackend.subscribe` from `async def` to `def` (async generators return `AsyncIterator` directly — no `await` at the call site)
  - `mcp/server.py`: E501 on three dict-literal and datetime-chain expressions
  - `test_event_store.py`, `test_events_api.py`, `test_mcp_server.py`: F401 and I001 fixes; E741 ambiguous variable `l` renamed to `ln`
  - `pyproject.toml`: added `pytest-asyncio>=0.23` to `[dev]` extras (required for `@pytest.mark.asyncio` tests)

---

## [v1.2.0] — 2026-05-22

**Theme:** HNSW approximate nearest-neighbour embedding index + BM25-HNSW fused recall (ruflo-mod contract), CI matrix hardening, security updates, REST API systemd service, and generated CLI. Covers E-45.

### Added

**HNSW Embedding Index (E-45):**
- `S-134` `HNSWStore` — `hnswlib`-backed approximate nearest-neighbour index with lazy `LocalEmbeddingBackend` (384-dim `all-MiniLM-L6-v2`), label map (`.labels.json` sidecar), metadata (`.meta.json` sidecar), auto-save every 100 upserts, atomic writes via tmp + `os.replace()`; graceful degradation to no-op when `hnswlib` is absent
- `S-135` `depthfusion_hnsw_capability` MCP tool — returns `HNSWCapability` shape (`enabled`, `backend`, `model`, `dimension`, `index_path`, `entry_count`) regardless of index state; always-on, no feature flag required; designed for the agent-ops bridge startup probe
- `S-136` `publish_context` HNSW integration — every publish upserts into the HNSW index when `DEPTHFUSION_HNSW_ENABLED=true`; `indexed_in_hnsw: bool` field added to all `publish_context` responses (additive, back-compat with existing callers)
- `S-137` BM25+HNSW fused recall — when HNSW is available, `recall_relevant` applies post-hoc cosine fusion: BM25 scores first, HNSW cosine similarity boosts matching items and appends HNSW-only hits; `final_score = 0.6 × bm25_score + 0.4 × hnsw_cosine`; results re-sorted and sliced to `top_k`
- `S-138` Recall response contract extension — `strategy` field (`"bm25-only"` / `"bm25+hnsw-fused"`) and `hnsw_available: bool` added to ALL `recall_relevant` response paths including empty results, filtered queries, index/timeline modes, and error paths; additive, back-compat
- `S-139` Graceful SIGTERM/SIGINT shutdown — HNSW store saves to disk on graceful server shutdown; registered via `_register_hnsw_shutdown()` at server start (main-thread guarded)
- `S-140` New `hnsw` extras group in `pyproject.toml` — `hnswlib>=0.7`; also added to `vps-gpu` and `mac-mlx` extras; `hnswlib` is optional — all code paths degrade gracefully without it

### New env vars (E-45)

| Env Var | Controls | Default |
|---|---|---|
| `DEPTHFUSION_HNSW_ENABLED` | Enable HNSW index + fused BM25+vector recall | `false` |
| `DEPTHFUSION_HNSW_INDEX_PATH` | Directory for HNSW index files + sidecars | `~/.depthfusion/hnsw/` |
| `DEPTHFUSION_EMBEDDING_MODEL` | sentence-transformers model for HNSW embeddings | `all-MiniLM-L6-v2` |

### Test totals (v1.2.0)
- **2000 passed · 9 skipped · 0 failed** (up from 1986 in v1.1.0)
- 9 skipped: hnswlib-gated tests — skip gracefully when `hnswlib` is not installed in the dev venv
- MCP tool count: **29** (28 in v1.1.0 + `depthfusion_hnsw_capability`)

**REST API systemd service:**
- `infra/systemd/depthfusion-rest.service` — user-level systemd unit for the FastAPI REST API
  (`127.0.0.1:7300`); reads `~/.claude/depthfusion.env` via `EnvironmentFile`; `Restart=on-failure`
- `infra/systemd/README.md` — install instructions: `cp`, `daemon-reload`, `enable --now`

**HNSW activated on VPS (operator change — 2026-05-22):**
- `DEPTHFUSION_HNSW_ENABLED=true` and `DEPTHFUSION_VECTOR_SEARCH_ENABLED=true` added to
  `~/.claude/depthfusion.env` on the `vps-gpu` host; both services restarted; recall now
  uses `strategy: "bm25+hnsw-fused"` with real `vector_score` values (e.g. 0.4242)
- `docs/install/vps-gpu-quickstart.md` — §7 (HNSW enablement), §8 (REST API systemd service),
  §9 (generated CLI install) added; "Done" checklist updated

**Generated CLI (`depthfusion-pp-cli` / `depthfusion-pp-mcp`):**
- 30-command Go CLI generated from `infra/depthfusion/openapi-spec.yaml` via cli-printing-press v4.11.0
  (29 generated + 3 compound); Scorecard: A (83%); binaries at
  `~/printing-press/library/depthfusion/build/stage/bin/`
- Compound commands: `discovery-audit` (discovery age/conflict audit),
  `graph-inspect` (BM25 recall → graph traversal), `batch-recall` (concurrent multi-query dedup)
- `depthfusion-pp-mcp` — stdio MCP server mirroring all 30 commands as agent tools;
  registered as `depthfusion-cli` in Claude Code (`claude mcp add --scope user`)
- `docs/cli.md` — full CLI reference (install, auth, common workflows, compound commands, MCP server)
- `infra/depthfusion/openapi-spec.yaml` — 29-endpoint OpenAPI 3.0 spec reverse-engineered from
  MCP tool signatures; source of truth for CLI generation
- `infra/depthfusion/catalog.yaml` — local cli-printing-press catalog entry

### CI

- **Windows CI matrix** — all 9/9 jobs green (ubuntu/macos/windows × Python 3.10/3.11/3.12); Windows
  switched to subprocess-free test allowlist (`test_core`, `test_session`, `test_hooks`, `test_storage`,
  `test_cognitive`, `test_regression`) — 292 tests in ~2 min vs prior 40–60 min timeout; Ubuntu runs
  the full suite as authoritative reference
- **Node.js 24 opt-in** — `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true` in both workflows ahead of
  GitHub's 2026-06-02 forced migration

### Fixed

- `core/file_locking.py` — `# type: ignore[attr-defined]` on `fcntl.flock`/`LOCK_EX`/`LOCK_SH`/
  `LOCK_UN` calls; mypy false-positive on Windows (runtime-guarded behind `try/except ImportError`)
- `api/rest.py` — `body: SetMemoryScoreBody = ...` Ellipsis default annotated with
  `# type: ignore[assignment]`; FastAPI idiom not understood by mypy

### Security

- **34 → 0 Dependabot alerts**: urllib3 `2.7.0`, cryptography `46.0.7`, setuptools `78.1.1`,
  requests `2.33.0`, jinja2 `3.1.6`, certifi `2024.7.4`, idna `3.15`, configobj `5.0.9`,
  pyasn1 `0.6.3`, wheel `0.46.2`, pytest `9.0.3`
- **chromadb `>=0.4` → `>=1.0`** in all chromadb extras — eliminates 0.x dep paths that brought in
  vulnerable Mako, PyJWT, and Markdown versions
- **Explicit lower bounds** in all chromadb extras: `Mako>=1.3.12`, `PyJWT>=2.12.0`,
  `Pygments>=2.20.0`, `Markdown>=3.8.1`

### Housekeeping

- `.gitignore` extended: `.claude/`, `.pm/`, `.rollback/`, `.codex`, `text.txt`, `.remember/`

---

## [v1.1.0] — 2026-05-20

**Theme:** SkillForge operational hardening, MemPalace retrieval integration, metrics reliability, full Python parity for the Mamba B/C/Δ selective fusion stack, and Windows + cross-platform installer. Covers E-38 through E-44.

### Added

**MemPalace Retrieval Integration (E-38):**
- `S-119` Temporal validity filter — `valid_from` / `valid_until` frontmatter fields respected at recall time; stale window entries excluded without eviction
- `S-120` Knowledge-graph provenance on recalled chunks — each result carries the entity path that produced it, enabling callers to reason about why a chunk surfaced
- `S-121` Linear-blend fusion opt-in — `DEPTHFUSION_LINEAR_BLEND=true` replaces RRF with a configurable α-weighted linear combination; benchmarked at parity with RRF+vector on current corpus
- `S-122` Wing/Room sub-project scoping (OD-3 resolution) — `DEPTHFUSION_WING_ID` / `DEPTHFUSION_ROOM_ID` confine recall and capture to a logical sub-project partition within a shared `~/.claude/` corpus; resolves the OD-3 ADR ambiguity
- `S-123` KG edge invalidation + point-in-time `get_edges` — edges can now carry `valid_until`; `graph_traverse` excludes invalidated edges by default; `get_edges(at=<timestamp>)` returns the graph state at any prior point

**SkillForge SF-2 Integration (E-39):**
- `depthfusion_run_recursive` MCP tool now routes via SkillForge HTTP API when three env vars are set: `DEPTHFUSION_SKILLFORGE_API_URL`, `DEPTHFUSION_SKILLFORGE_API_TOKEN`, `DEPTHFUSION_SKILLFORGE_RECURSIVE_SKILL_ID`
- `RLMClient.is_skillforge_configured()` — predicate exposed so operators can verify routing before use
- MCP server gate updated: returns `{"error": "neither SkillForge nor rlm configured"}` instead of the old hard "rlm not available" error — preserves existing rlm path as fallback
- `_parse_response` handles SkillForge `status: FAILED` gracefully — when output schema validation fails but the LLM ran, extracts `log.rawResponse.content` as the result text instead of raising `ValueError`; raises only when no LLM output is present at all

**CIQS Category D Benchmark Harness (E-40):**
- `tests/benchmarks/ciqs_cat_d_harness.py` — PRECEDED_BY temporal-edge recall benchmark; measures cross-session continuity lift from knowledge-graph temporal linkage; reusable for regression CI

**Metrics Reliability (E-41):**
- `MetricsCollector.record()` now flock-guarded (`fcntl.flock`) — eliminates the multi-process interleaving window under concurrent hook execution (pre-existing gap, surfaced in S-53 review)
- `_iter_jsonl_counted()` added to `metrics/aggregator.py` — like `_iter_jsonl` but returns `(entries, skipped_lines)` for data-integrity visibility
- `backend_summary()` returns `skipped_lines` count — visible in `depthfusion_status` output
- `capture_summary()` returns `skipped_lines` count — matching the backend summary API

**Pruner grace period (E-42):**
- `identify_candidates()` accepts `superseded_min_age_hours: int = 0` — superseded files younger than the threshold are not returned as prune candidates; prevents false-positive archival when dedup runs faster than the age floor; default 0 = current behaviour (back-compat)

**SkillForge Divergence Gaps (E-43):**
- `S-128` JWT token auto-refresh on HTTP 401 from SkillForge — `RLMClient._run_via_skillforge()` transparently re-fetches auth and retries once on 401; subsequent 401 surfaces as `ValueError`
- `S-129` Selective fusion weighter Python port — `SelectiveFusionWeighter` (Mamba B/C/Δ sequential multiplicative gates) ported from `selective-fusion-weighter.ts`; full TS/Python parity; opt-in via `DEPTHFUSION_FUSION_GATES_ENABLED=true`
- `S-130` Materialisation policy + chunk state compression Python port — `MaterialisationPolicy` (three-gate: score threshold → novelty → capacity eviction) and `ChunkStateCompressor` (Mamba-style fixed-size boundary state: topic EMA, entity LRU, score stats, exponential decay) ported from TypeScript; `RecallPipeline` wired to persist `ChunkBoundaryState` across `apply_fusion_gates()` calls; fail-open contract

### Changed

- `capture_summary()` now uses `_iter_jsonl_counted` internally — return shape gains `skipped_lines` key (additive; existing callers unaffected)
- `depthfusion_run_recursive` MCP error message updated: `"rlm package not available"` → `"neither SkillForge nor rlm configured"` when both paths are absent

**Cross-Platform Installer (E-44):**
- `S-131` `scripts/install.sh` — single-command Mac/Linux installer: creates venv, pip-installs, prompts for API key, writes `~/.claude/depthfusion.env` (chmod 600), merges `~/.claude/claude_desktop_config.json` with atomic write + timestamped backup; refuses keys matching `^sk-ant-api03-` to prevent subscription billing cross-contamination
- `S-131` `src/depthfusion/install/install.py`: `install_local_windows()` + `--non-interactive` flag — Windows install path writes to `%APPDATA%\Claude\`, registers python.exe in Claude Desktop config; `--non-interactive` reads `DEPTHFUSION_API_KEY` from env for CI provisioning
- `S-132` `scripts/install.ps1` — Windows PowerShell installer (requires 5.1+): venv creation, pip install, ACL-restricted env file (current user only), `ConvertTo-Json -Depth 32` (avoids silent truncation of complex existing configs), atomic temp-file write + timestamped backup of `%APPDATA%\Claude\claude_desktop_config.json`
- `S-132` `scripts/mcp-server.bat` — Windows Claude Desktop MCP launcher; uses `%~dp0`-relative venv path
- `S-132` `fcntl` Windows portability — `flock_ex` / `flock_sh` / `flock_un` wrappers in `core/file_locking.py` behind `try: import fcntl as _fcntl / except ImportError: no-op`; all direct `fcntl.flock()` call sites in `storage/event_log.py`, `router/bus.py`, `metrics/collector.py` migrated; emits `RuntimeWarning` when `fcntl` unavailable on non-Windows (degraded-lock observability)
- `S-133` `.github/workflows/installer-ci.yml` — 9-cell CI matrix (ubuntu-latest / macos-latest / windows-latest × Python 3.10 / 3.11 / 3.12); `fail-fast: false`; 20-minute timeout; OS-conditional pip quoting
- `docs/install/windows-quickstart.md` — step-by-step Windows install guide covering prerequisites, clone, `powershell -ExecutionPolicy Bypass -File scripts\install.ps1`, API key sourcing, restart, verification via `depthfusion_status`, and troubleshooting

### Test totals (v1.1.0)

- **1986 tests passing** (post-merge; benchmark suite excluded from count; delta reflects test suite restructuring)
- 0 ruff violations · 0 mypy errors

---

## [v1.0.0] — 2026-05-18

**Theme:** Ambient cognition — session auto-recall/capture, REST query API, telemetry platform, and retrieval quality lift (Cat A +21.7pp via OpenHuman scoring signals). Covers E-32 through E-37 + install UX (S-110/S-111) + full lint/type cleanup.

### Added

**Ambient Capture & Auto-Recall (E-35):**
- `S-110` PostToolUse ambient capture hook — every tool call emits a structured capture event without requiring manual `/publish_context`
- `S-111` SessionStart auto-recall seed injection — fresh sessions start warm; top-k recall result injected before the first user turn
- `S-112` Structured observation fields on `ContextItem` — typed `files_read`, `files_modified`, `tool_name`, `session_id` fields enable filtering and scoring on structure rather than prose blob
- `S-113` 3-layer progressive disclosure search (MCP tool `depthfusion_retrieve_context`) — lightweight 10%-cost mode (SQLite FTS5 → BM25 pre-filter → optional semantic) for context-injection hot paths
- `S-114` SQLite FTS5 index on `MemoryStore` — phrase queries and pre-filter passes run against an indexed projection; degrades gracefully to in-memory BM25 scan when FTS5 is unavailable

**CIQS Category A Retrieval Quality (E-36):**
- `S-115` Project-aware scoring — `detect_mentioned_projects()` + 2× mention-boost multiplier in scoring loop; boilerplate session-envelope suppression; raises Cat A from 18.3% to 40.0% (+21.7pp, 3-run harness, commit `33d0d54`/`11081ef`)

**Memory Scoring Signals — OpenHuman Port (E-37):**
- `S-116` Lexical richness penalty — `lexical_richness_penalty(content) → [0.5, 1.0]` based on type-token ratio; 6th scoring multiplier; penalises log dumps and repetitive envelopes without touching high-entropy sessions
- `S-117` Query-hits feedback loop — `HitTracker` singleton persists per-chunk hit counts to `~/.claude/.depthfusion_hits.jsonl` (30-day rolling window, 5 MB prune); `query_hits_boost` (max 1.5×) wired as 7th multiplier; hits registered on output blocks, not input (avoids self-inflation)
- `S-118` Pre-indexing admission gate v2 — `_admission_score = boilerplate_penalty × lexical_richness_penalty`; chunks scoring below `DEPTHFUSION_ADMISSION_THRESHOLD` (default 0.10) are skipped at index time with a DEBUG log

**Install UX (E-05 additions):**
- `S-110` Guided web install wizard at `127.0.0.1:7300/install` — 6-step browser UI for mode selection, dependency checks, API key entry, hook/MCP wiring, and confirmation; binds loopback only
- `S-111` Windows compatibility for `local` and `vps-cpu` modes — PowerShell hook equivalents, `dep_checker.py` cross-platform package detection; `vps-gpu` and `mac-mlx` blocked with clear error on win32

**Query REST API (E-32):**
- `S-104` `/query/discoveries`, `/query/sessions`, `/query/aggregate` — date-range + filter endpoints for BI tool connectivity; row-level SQL injection protection via parameterised queries
- `S-105` BI connectivity guide + Metabase dashboard template (`docs/bi-connectivity.md`)

**Telemetry Data Platform (E-33/E-34):**
- `S-106` `df_record_telemetry` MCP tool — PostToolUse hooks can log structured telemetry events with session, tool, and cost metadata
- `S-107` `df_query_telemetry` MCP tool + rollup aggregations — think-time, model pricing, session-type breakdown; cost estimation in query API
- `S-108` `/query/sessions` telemetry enrichment with `telemetry_summary` field
- `S-109` Candidate skill surfacing — `df_surface_skill_candidates` tool drafts recurring patterns for human review in SkillForge

### Fixed

- All 30 ruff violations resolved across `src/` and `scripts/` (commits `97e8ff3`–`c7e3a22`); `ruff check .` is now clean
- All 4 mypy errors resolved in `vector_store.py` (ndarray cast) and `server.py` (set narrowing, threshold cast); `mypy src/` reports `Success: no issues found in 101 source files` (`bc85efa`)
- Benchmark `benchmark_home` fixture no longer bleeds into live `~/.claude/depthfusion-metrics/` under `DEPTHFUSION_*` env flags (`f98ba33`)

### Test totals as of v1.0.0

- **1843 tests collected and passing** (was 1519 at v0.6.0; +324 from E-35 + E-36 + E-37 + install UX + scoring signals)
- New test files: `test_hit_tracker.py` (21 tests), `test_admission_gate.py` (18 tests), `test_ui_server.py`, `test_ciqs_a_regression.py` additions

---

## [v0.6.0] — 2026-05-11

**Theme:** Build-plan alignment (E-30) — two P0 correctness fixes that made advertised modes non-functional, plus MCP schema completeness, vector embedding consistency, benchmark harness, SQLite metadata cache, recall explainability, and the SkillForge RLM HTTP sidecar. Promotes from `0.6.0a2`.

### Fixed

**P0: `DEPTHFUSION_MODE=vps-cpu` / `vps-gpu` silently fell through to local BM25 (S-86):**
- `RecallPipeline.from_env()` checked for the legacy `"vps"` string as the non-local gate. Setting either advertised mode (`vps-cpu`, `vps-gpu`) caused silent degradation to local-only retrieval.
- New `utils/mode.py::normalise_mode()` maps `vps-cpu` and `vps-gpu` to canonical mode strings; `vps` is a deprecated alias for `vps-cpu` with a `DeprecationWarning`; unknown values fall back to `local` with a log warning.
- `hybrid.py::from_env()` updated to consume `normalise_mode()`; behavior for `local` is unchanged.

**P0: `pip install depthfusion[vps-cpu]` did not install the Anthropic SDK (S-87):**
- Haiku reranking silently degraded to `NullBackend` on every `vps-cpu` install because `anthropic>=0.40` was missing from the `[vps-cpu]` extra.
- `anthropic>=0.40` added to `[vps-cpu]`; `anthropic>=0.40`, `sentence-transformers>=2.2`, `chromadb>=0.4` confirmed present in `[vps-gpu]`.
- `fastapi>=0.100` and `uvicorn>=0.23` also added to both extras (required by new RLM sidecar).

**Vector embedding space consistency (S-89):**
- `ChromaDBStore.add_document()` and `query()` now both explicitly call `get_backend("embedding")` via a lazy import and pass the embedding vectors to Chroma rather than letting Chroma pick its own embedding function. Eliminates silent vector-space mismatch between index and query.
- When the embedding backend is null or raises, both paths fall back to Chroma auto-embedding and log a warning — BM25 recall continues unaffected.

### Added

**MCP tool schemas — all 18 tools (S-88):**
- `_make_tool_schema()` replaced by `TOOL_SCHEMAS` lookup dict. Every enabled MCP tool now returns a non-empty JSON schema with typed properties, `required` arrays, and documented bounds. Invalid payloads produce actionable 422 errors.

**Benchmark harness (S-90):**
- `scripts/benchmark.py` runs without API keys against `tests/fixtures/recall_goldset.jsonl` (8 representative query+corpus+relevant-chunk entries).
- Metrics: `precision_at_1`, `precision_at_5`, `hit_rate_at_5`, `fallback_rate`, `p50_latency_ms`, `p95_latency_ms`, `cost_estimate_usd`. Each metric carries a `basis` label: `measured | estimated | projected`.
- Flags: `--goldset`, `--top-k`, `--output`, `--mode`, `--quiet`.

**SQLite metadata cache (S-91):**
- `storage/file_index.py::FileMetadataIndex` — WAL-mode SQLite index storing `(file_path, mtime, size, content_hash, project, importance, salience, pinned, indexed_at)`.
- `is_stale()` lets callers skip full file reads for unchanged files; `update()` / `get()` / `remove()` / `list_project()` / `purge_missing()` provide the CRUD surface.
- Default path: `~/.claude/.depthfusion_file_index.db`. Thread-safe via per-instance `threading.Lock`. Exported from `storage/__init__.py`.

**Recall explainability (S-92):**
- `depthfusion_recall_relevant` accepts `explain: bool` (default `false`).
- When `explain=true`, each result block includes `{"bm25_score", "source_weight", "rrf_score", "reranker_rank"}` plus `"vector_score"` and `"project_match"` when those stages ran.
- Default response unchanged — no size regression.

**SkillForge HTTP sidecar (S-35 AC-4 / T-106):**
- `recursive/sidecar.py` — FastAPI service exposing `RLMClient` over HTTP. Endpoints: `GET /health`, `POST /run`, `GET /schema`. Default port 8771 (`DEPTHFUSION_RLM_PORT`), loopback-only (`127.0.0.1`).
- SkillForge TypeScript callers can reach the Python recursive backend without a Python import dependency.

### Verification

**Test totals as of v0.6.0:**
- 1519 tests collected and passing (was 1430 at v0.6.0a2; +89 from E-30 + S-35 AC-4)
- `ruff check src tests`: all checks passed
- New test modules: `test_mode.py`, `test_mode_resolution.py`, `test_tool_schemas.py`, `test_recall_explain.py`, `test_benchmark.py`, `test_file_index.py`, `test_vector_store.py` (extended), `test_sidecar.py`

---

## [v0.6.0a2] — 2026-05-08

**Theme:** v0.5.3 polish (E-29) — observability gaps surfaced by the week-1 dogfood report. No retrieval-quality or MCP-surface changes; this release closes the substrate gap (S-79) and populates four previously-empty fields in the structured streams.

### Added

**Startup self-check + `system.startup` event (S-79 AC-3 / T-265, T-267):**
- `_emit_startup_event()` in `mcp/server.py` writes a `metric="system.startup"` record to the legacy `.jsonl` stream on every MCP server init.
- Includes `tools_enabled` count and `server_version` in labels.
- Logs WARNING (never raises) when the metrics directory is unwritable.
- An empty metrics directory at end-of-day now distinguishes "server never ran" from "ran but emitted nothing" — absence of `system.startup` is the signal. Catches future environment drift before it causes another 13-day silent gap.

**Per-capability latency for all six retrieval capabilities (S-80 / T-268, T-269, T-270):**
- `_detect_current_backends()` now records probe latency per capability into `latency_ms_per_capability`. Pipeline measurements (reranker, embedding/vector_search) win over probe times via merge-with-priority. Error paths use try/finally so latency is captured even when a backend raises.
- All six capability keys (`extractor`, `linker`, `summariser`, `embedding`, `decision_extractor`, `reranker`) now appear in every recall event. **Unblocks S-43 AC-3** (p95 recall latency per capability) and **S-64 AC-2** (GPU migration phase 4 latency table).

**`config_version_id` plumbing via runtime resolver (S-81 / T-271, T-272, T-273):**
- `CONFIG_VERSION_NONE = "none"` sentinel replaces the empty-string emission. `MetricsCollector` gains a `config_version_resolver` kwarg defaulting to a 12-char sha256 hash of 19 tracked env-var keys (mode, all 6 backends, fusion gate params, RRF/RLM tuning).
- `record_capture_event` and `record_recall_query` route the field through the resolver: explicit non-empty values pass through verbatim, empty/None falls through to runtime resolution, resolver failures coerce to `"none"`.
- Empty string is no longer a valid output for capture/recall events. The DR-018 §4 D-3 invariant (auditor reproducibility) is now structurally enforced rather than declared.

**`backend_fallback_chain` populated in recall events (S-83 / T-278, T-279):**
- The MCP server now populates the structured `backend_fallback_chain` field on every successful `_tool_recall` emission. Each capability records its cascade as a list: single-backend resolutions write `[name]`; `FallbackChain` resolutions split `backend.name` on `+` and write the full cascade (e.g. `["gemma", "haiku", "null"]`).
- Documented as **complementary** to the legacy simple-stream events:
  - Legacy `backend.fallback` / `backend.runtime_fallback` = aggregate count per (capability, error_type), useful for rate dashboards
  - Structured `backend_fallback_chain` = per-query cascade trace, useful for debugging "what cascade did query X use?"
- Migration note: consumers of `~/.claude/depthfusion-metrics/*-recall.jsonl` must NOT assume `backend_fallback_chain` is empty — it now contains per-query cascade traces.

**Test/prod telemetry separation (S-82 / T-274, T-275, T-276):**
- New `tests/conftest.py` autouse session fixture intercepts bare `MetricsCollector()` calls during pytest runs and redirects writes to a per-session tmp dir. Explicit `metrics_dir` args always win — escape hatch for legitimate integration tests.
- After this lands, `~/.claude/depthfusion-metrics/` reflects only production-path activity. The next dogfood pass will distinguish real usage from test noise without manual filtering.
- Documented in new `tests/README.md`.

**Runbook self-correction (S-84 / T-280):**
- `docs/runbooks/dogfood-telemetry.md` §2 prereqs now correctly document `DEPTHFUSION_FUSION_GATES_ENABLED=true` as the gates-stream prereq (was: incorrectly stated "no env flags need setting").
- §3 daily protocol gains a day-1 verification step.
- §6 triage table gains a "Substrate gap" row (P0; blocks all other findings).
- §7 report template gains a "Headline finding" section above "Stream health".

### Changed

**Handoff doc corrections (commits 1fca21c, 54d8e6d):**
- `docs/coordination/2026-05-05-from-depthfusion-e27-ready-for-agent-ops.md` §2.3 corrected three response-shape errors caught by independent review:
  - `recall_relevant.source` field is a label (`"session"` / `"discovery"` / `"memory"`), not a path
  - Empty-result shape: `total_sources_scanned` is **absent** on empty paths, not `0`
  - Per-block caveat: `gate_b_score` / `gate_c_score` / `gate_fused_score` appear when `DEPTHFUSION_FUSION_GATES_ENABLED=true`

### Fixed

**Hook chain venv path drift (S-79 AC-1 / T-263, T-264):**
- The week-1 dogfood report's headline finding ("100% test fixtures, zero production-path emissions over 13 days") was traced to `~/.claude-shared/hooks/*.sh` referencing a stale capitalised venv path (`/home/gregmorris/Development/Projects/...`) that no longer existed. Real checkout was at `/home/gregmorris/projects/depthfusion/`. Fix applied via sed across 6 files. Recall portion validated 2026-05-07 (4 production-path recall events across 2 fresh sessions).

### Verification

**Test totals as of v0.6.0a2:**
- 1430 tests collected
- `tests/test_metrics/`: 83 pass (was 50 pre-E-29)
- `tests/test_backends/`: 225 pass
- `tests/test_mcp/`: 23 pass
- No regressions of S-80, S-81, S-82, S-83 cross-checked at each commit.

---
- The MCP server now populates the structured `backend_fallback_chain`
  field on every successful `_tool_recall` emission. Each capability
  records its cascade as a list: single-backend resolutions write
  `[name]`; `FallbackChain` resolutions split `backend.name` on `+`
  and write the full cascade (e.g. `["gemma", "haiku", "null"]`).
- Drives the aggregator's `per_capability_fallback` view with real data;
  prior to this change the field was empty in 100% of dogfood-observed
  recall events (30/30) because nothing populated it.
- Migration note for telemetry consumers: **do not assume
  `backend_fallback_chain` is empty.** It now contains per-query cascade
  traces. Existing dashboards that aggregated zero values from the
  field will start seeing populated lists from this version forward.
- Contract (kept distinct on purpose):
  - **Legacy `backend.fallback`** (factory-time, in `factory.py`) and
    **`backend.runtime_fallback`** (chain-time, in `chain.py`) are
    *aggregate-count* simple-stream events — one row per
    (capability, error_type, transition). Useful for rate dashboards.
  - **Structured `backend_fallback_chain`** field in the recall stream
    is the *per-query-detail* trace — answers "what cascade did *this*
    specific query use?". The simple stream cannot answer that.
  - Both paths are kept as complementary. Neither is being removed.
- 4 new tests in `tests/test_metrics/test_fallback_canonical.py` cover:
  single-backend resolution writes `[name]`, `FallbackChain` resolution
  writes the split cascade, the legacy `backend.fallback*` simple-stream
  events still fire (no regression), and `backend_summary()` reads the
  structured field correctly into its `per_capability_fallback` view.

**Two-mode CIQS comparison (`scripts/ciqs_compare.py`):**
- Unpaired-bootstrap delta CI between baseline and candidate CIQS runs
  (e.g. vps-cpu pre-migration vs vps-gpu post-migration). Classifies each
  category as `improved` / `regressed` / `parity` based on whether zero
  falls in the delta CI — prevents claiming wins on sampling noise.
- `--exit-nonzero-on-regression` flag for CI/automation gating.
- 23 unit tests covering math, verdict classification, report formatting,
  end-to-end CLI, and the review-gate `_reset_summ_for_testing()` hook.

**Session-history prompt miner (`scripts/mine_session_prompts.py`):**
- Extracts user-authored prompts from `~/.claude/projects/*/session-*.jsonl`
  for eval-corpus expansion. Higher signal than LLM-synthesised prompts
  because the corpus is already in-distribution for the user.
- Filters: `type: user` + string content; drops wrappers
  (`<command-message>`, `<system-reminder>`, etc.); length threshold;
  exact-dup removal via normalised-hash.
- Redacts common secret patterns (OpenAI/Anthropic, AWS, GitHub,
  Slack) before the dedup hash so secrets-only-differing prompts collapse.
- Smoke-tested on 1445 real session files producing 78 unique prompts.
- 32 unit tests plus the review-gate regression for
  `dropped_project_filter` stat counting.

**Autonomous weekly regression monitor (`scripts/ciqs_weekly.py`):**
- Reads the already-emitted `backend_summary()` / `capture_summary()`
  JSONL streams; compares last 7 days to prior 7 days; flags latency
  (> 20%), error-rate (> 5pp), capture-volume (> 30% drop), and
  availability (< 95% with prior full coverage) regressions.
- Does NOT auto-score quality — that requires labelled expected outputs.
  Report ships with an explicit disclaimer so operators don't conflate
  "no mechanical regression" with "no quality issue".
- `scripts/ciqs-weekly.service` + `scripts/ciqs-weekly.timer` systemd
  units for scheduled (Monday 06:00 local) execution with 10m jitter.
- Exit 0 on no regressions, 1 on regressions (systemd marks unit failed),
  2 on analysis error. Surfaces in `systemctl status` and journal.
- 19 unit tests covering window aggregation, threshold semantics, the
  "new backend not flagged" invariant, report formatting, CLI exit codes,
  and the review-gate regression for `window_days` threading.

### Fixed

**Placeholder-key guard in installer (`install.py`):** `_check_depthfusion_api_key()`
now detects values containing `your-real-key-here` (case-insensitive) and emits
a loud WARNING explaining that Haiku falls back to NullBackend. `_recommend_mode_from_gpu()`
treats a placeholder as "no key" and recommends `local` instead of `vps-cpu`, preventing
the installer from confidently configuring a mode that can't actually run its
flagship features. Regression for 2026-04-24 incident where the placeholder shipped in
`~/.claude/depthfusion.env` lived for ~4 weeks undetected — the factory's
NullBackend fallback was silent enough that recall returned BM25-only results
and the knowledge graph accumulated 0 entities across 171 sessions. 11 unit tests
added under `TestPlaceholderKeyGuard`.

**Review-gate findings on the three new scripts (2 High, 2 Medium, 2 Low):**
- `ciqs-weekly.service`: `%i` specifier expanded to empty string in
  non-template unit (filename became `-YYYY-MM-DD.md` with leading dash).
  Replaced with static `weekly-` prefix.
- `ciqs_weekly.py`: `detect_regressions` and `format_report` hardcoded
  `/ 7` divisor for availability math, breaking correctness when
  `--window-days != 7`. Both now consume `window_days` from the
  aggregated dict.
- `ciqs_compare.py`: added `_reset_summ_for_testing()` so the lazy
  module cache can be cleared in future tests that inject a fake
  summariser (no current test does, but the trap is now defused).
- `mine_session_prompts.py`: `dropped_project_filter` stat was
  initialised but never incremented — now tracks filtered-out files.
  Summary line added to stderr output.
- `mine_session_prompts.py`: removed shadowed `sk-ant-…` redaction
  pattern — the preceding `sk-…` branch matched first, making it
  dead code.

### Changed

Nothing changed in library runtime behaviour. All additions are scripts
(not importable from the package); no existing tests touched.

### Added (install tooling)

**Bundled installer for research tools (`scripts/install-research-tools.sh`):**
- Mode-agnostic shell installer for the research-tools bundle (session-
  history miner + weekly regression monitor). Detects prerequisites
  (`python3`, `depthfusion` importable, `systemctl --user` available);
  installs systemd user units idempotently via `cmp -s` compare-then-
  copy; runs initial mining pass; prints next-scheduled-run time.
- Graceful fallback to cron guidance when `systemctl --user` isn't
  usable (headless VPSes without user lingering enabled).
- Supports `--dry-run` for preview and `--skip-miner` for timer-only
  reinstalls. 7 smoke tests covering syntax, flags, dry-run side-effect
  absence, idempotency check presence, and systemd fallback logic.

**Quickstart guides for two install paths:**
- `docs/install/README.md` — decision overview: which path to pick,
  when to run both (parallel-comparison plan)
- `docs/install/vps-cpu-quickstart.md` — complete CPU-only install
  path (~10 min)
- `docs/install/vps-gpu-quickstart.md` — complete GPU install path
  (~4 hrs including vLLM + Gemma download). Cross-references the
  GPU migration runbook for data-migration scenarios.

The two quickstart guides share the same research-tools installer
invocation — what differs is the `pip install` extras (`[vps-cpu]`
vs `[vps-gpu]`), the `--mode` flag for `depthfusion.install.install`,
and the GPU path's vLLM systemd service setup (root-level, distinct
from the user-level weekly timer).

---

## [v0.6.0] — unreleased

### Removed

- Removed the deprecated `--mode=vps` installer alias. Use `--mode=vps-cpu` instead. (S-56)
- Removed deprecated `vps-tier1` and `vps-tier2` pyproject extras. Migrate to `local`, `vps-cpu`, or `vps-gpu`. (S-57)

---

## [v0.6.0a1] — 2026-04-21

**Theme:** benchmark infrastructure, pre-migration preparation, and
backlog hygiene. No runtime-behaviour changes — the library behaves
identically to v0.5.2 unless operators explicitly opt in to the new
opt-in features (FallbackChain direct construction, dogfood telemetry
collection, CIQS harness runs).

Why alpha: the FallbackChain class (S-44 AC-3/AC-4) is implemented,
tested, and ready, but not yet wired into the factory's default
dispatch. v0.6.0 stable will flip the chain on by default for
vps-gpu mode, gated initially on `DEPTHFUSION_FALLBACK_CHAIN_ENABLED`
and then default-on. Alpha signals "new public classes are stable to
depend on; integration into defaults is forthcoming."

### Added

**`FallbackChain` backend wrapper (S-44 AC-3, AC-4 / E-19):**
- `src/depthfusion/backends/chain.py` — ordered `LLMBackend` wrapper
  that catches the three typed fallback errors (`RateLimitError`,
  `BackendOverloadError`, `BackendTimeoutError`) from the primary
  and transparently falls through to the next healthy link.
- Emits `backend.runtime_fallback` events per transition (distinct
  metric name from the factory's construction-time `backend.fallback`).
  Both gated on the same `DEPTHFUSION_BACKEND_FALLBACK_LOG` env var.
- Canonical cascade once wired: `FallbackChain([Gemma, Haiku, Null])`.
- Cached MetricsCollector instance on the emission hot path — no
  repeated stat syscalls under overload waves.
- 27 tests (`test_chain.py`) covering construction, health semantics,
  each typed-error variant, non-fallback exception propagation,
  unhealthy-skip, exhaustion with full chain names, 3-link cascade,
  event emission on/off, and H-1 health-race regression.

**CIQS benchmark harness (S-63 / E-26):**
- `scripts/ciqs_harness.py` — two-subcommand runner (`run`, `score`)
  driving the 5-category battery. Category A (retrieval-only) is
  fully auto-executed via `depthfusion.mcp.server._tool_recall`;
  B/C/D/E emit a scoring template for operator/judge-model filling.
- `scripts/ciqs_summarise.py` — bootstrap CI aggregation over N
  scored runs. Linear-interpolated percentile + 5000-resample
  bootstrap at seed=1729 for determinism.
- `docs/benchmarks/prompts/ciqs-battery.yaml` — machine-readable
  5-category battery (17 topics total, per-dimension rubrics with
  0/5/10 anchors, composite weights declared).
- `docs/benchmarks/README.md` — three-stage flow methodology.
- 33 unit tests covering percentile, bootstrap CI, normalisation,
  category grouping, report formatting, and template parsing.

**Capture-mechanism eval sets (S-64 / E-26):**
- `docs/eval-sets/README.md` + three per-set READMEs — schemas,
  labelling protocol, inter-rater-agreement guidance, edge cases.
- 6 seed JSON fixtures across three sets (decision-extraction,
  dedup, negative) pinning each schema.
- `scripts/eval_decision.py`, `scripts/eval_dedup.py`,
  `scripts/eval_negative.py` — heuristic-extractor + BOW-cosine
  precision/recall reporters. Self-contained (no embedding backend
  dependency). Target thresholds report PASS/FAIL against S-45
  AC-1, S-48 AC-2, S-49 AC-2.

**Dogfood telemetry runbook (S-65 T-204 / E-26):**
- `docs/runbooks/dogfood-telemetry.md` — protocol for validating
  the v0.5.1/v0.5.2 observability layer by running on real work
  for ≥ 1 week. Covers prereqs, daily usage, end-of-week
  aggregation incantations (jq + `MetricsAggregator`), field-level
  analysis checklists, triage rubric, report template.

**GPU VPS migration runbook (E-19 ops support):**
- `docs/runbooks/gpu-vps-migration.md` — end-to-end handover:
  snapshot + rollback tarball, SCP to new host, `[vps-gpu]`
  install, vLLM systemd service, installer auto-probe + smoke
  test, data restore, 5-step validation, per-probe
  troubleshooting, rollback procedure, first-week tasks.

**Darkroom Amber design prototype (docs/design):**
- `docs/design/prototype/design_handoff_depthfusion_landing/` —
  self-contained HTML + README from a Claude design handoff
  implementing the install UX brief at
  `docs/design/install-ux-prompt.md`. Sodium-safelight palette,
  Fraunces typography, accessibility-first (`prefers-reduced-motion`
  honoured at both CSS and JS layers).

### Changed

**Backlog reconciliation (docs-only, one-pass):**
- E-14 (CIQS Data-Gap Closure) `[active]` → `[done]` — code-complete
  since v0.5 absorbed v0.3.1 fixes; S-30 benchmark ACs migrated to E-26.
- E-15 (Performance Measurement Framework) `[active]` → `[done]`
  — authoring complete; T-93 (harness automation) delivered as S-63.
- E-19 (v0.5 GPU-Enabled LLM Routing) `[backlog]` → `[done]` — both
  S-43 and S-44 code-complete with 41 + 61 tests; live-GPU benchmarks
  migrated to E-26 as S-66.
- E-20 (v0.5 Capture Mechanisms) `[active]` → `[done]` — all five
  mechanisms code-complete; benchmark-blocked precision/recall ACs
  migrated to E-26.
- E-21 (v0.5 Retrieval Quality Enhancements) `[backlog]` → `[done]`
  — S-50/S-51/S-52 all landed across v0.5 release arc.
- New E-26 (Benchmark Harness & Evaluation Data) — consolidates all
  benchmark-blocked ACs into one measurement workstream with stories
  S-63 (harness), S-64 (gold sets), S-65 (dogfood), S-66 (GPU baseline).
- S-25 T-73 ("sentence-boundary trimming not yet implemented") flipped
  `[x]` — `_trim_to_sentence()` has been in `mcp/server.py:224` since
  v0.3.1; status was stale.

### Fixed

**Review-gate fixes on v0.6.0a1 scripts (three Highs + three Mediums):**
- `scripts/ciqs_harness.py` `cmd_score` output-path derivation: naïve
  `str.replace("-raw.jsonl", ...)` was a no-op on non-matching paths,
  silently overwriting the input file. Extracted `_derive_scored_path()`
  that validates the `-raw` suffix and raises `ValueError` otherwise.
- `_SECTION_HEADER` regex loosened from `[A-E]` to `[A-Z]` — future
  F+ categories will not be silently skipped.
- Docstring on `parse_scoring_template` corrected to match code:
  non-integer scores are silently skipped (regex filter), only
  out-of-range raises.
- `ciqs_summarise.py` report table header now uses the actual
  confidence level instead of hardcoded "95% CI".
- Eval scripts: `from collections import Counter` moved to module
  top; `sys.exit(2)` from library functions replaced with
  `raise ImportError` handled by `main()`.
- `FallbackChain` `_next_healthy_name` race eliminated (H-1); event
  "to" field now uses next-by-index instead of re-probed health.
- `BackendExhaustedError.chain` now always carries the full backend
  list (was `[]` when all unhealthy); message distinguishes tried
  vs skipped.
- Fallback-event emission tests no longer silently skip when metrics
  dir doesn't resolve under `$HOME` — mock the collector directly.

### Not yet shipped (targets for v0.6.0 stable)

- **Factory wiring of FallbackChain** — currently opt-in via direct
  construction. v0.6.0 stable will switch `get_backend` on vps-gpu
  mode to return `FallbackChain([Gemma, Haiku, Null])` for LLM
  capabilities, gated initially on `DEPTHFUSION_FALLBACK_CHAIN_ENABLED`.
- **Live-GPU benchmarks** (S-66) — gated on executing the migration.
- **Post-migration dogfood report** (S-65 T-205 first pass).
- **3-run CIQS baseline** for local + vps-cpu (S-63 T-201) — calendar-
  blocked on operator time.
- **Full gold-set curation** (S-64 T-202 remainder): 50 decision sessions,
  30 dedup pairs, 40 negatives — labelling labour.

### Added

**`FallbackChain` backend wrapper (S-44 AC-3, AC-4 / E-19):**
- `src/depthfusion/backends/chain.py` — ordered `LLMBackend` wrapper
  that catches `RateLimitError` / `BackendOverloadError` /
  `BackendTimeoutError` from the primary and transparently falls
  through to the next healthy link. Emits `backend.runtime_fallback`
  events per transition (distinct metric from the factory's
  construction-time `backend.fallback`).
- Protocol-conformant (`runtime_checkable` verified in tests) so it's
  a drop-in replacement anywhere an `LLMBackend` is expected.
- Canonical cascade for vps-gpu once wired: `FallbackChain([Gemma, Haiku, Null])`
  — on Gemma 503 → Haiku; on Haiku 429 → Null returns safe defaults.
- Respects `DEPTHFUSION_BACKEND_FALLBACK_LOG` env var (default on) to
  enable/disable event emission, mirroring the factory-level gate.
- 24 tests (`tests/test_backends/test_chain.py`) covering construction,
  health semantics, each typed-error variant, non-fallback exception
  propagation, unhealthy-skip, exhaustion ordering, 3-link cascade,
  and event emission on/off.

**GPU VPS migration runbook (E-19 ops support):**
- `docs/runbooks/gpu-vps-migration.md` — end-to-end handover from a
  current vps-cpu installation to Hetzner GEX44 (NVIDIA RTX 4000 SFF
  Ada) running `vps-gpu` mode. Covers: pre-migration snapshot with
  rollback tarball, SCP to new host, package install with `[vps-gpu]`
  extras, vLLM systemd service, installer with auto-probe + smoke
  test, per-probe troubleshooting, validation checklist (health,
  recall, capture, latency, CIQS), rollback procedure, post-migration
  first-week tasks.

### Changed

**Backlog reconciliation (docs-only):**
- E-19 status `[backlog]` → `[done]` — both stories code-complete for
  ~3 releases; status drift carried over from earlier epics. Live-GPU
  benchmark ACs (S-43 AC-2/AC-3, S-44 AC-2) now referenced from E-26
  as their measurement home.
- S-44 AC-3 and AC-4 ticked upon FallbackChain delivery.
- New story S-66 opened under E-26 for the post-migration 3-run CIQS
  baseline that unblocks the benchmark ACs.

### Not yet shipped (targets for v0.6.0 stable)

- **Factory wiring of FallbackChain** — currently opt-in via direct
  construction. v0.6.0 stable will switch `get_backend` on vps-gpu
  mode to return `FallbackChain([Gemma, Haiku, Null])` for LLM
  capabilities, gated initially on `DEPTHFUSION_FALLBACK_CHAIN_ENABLED`
  and then flipped to default-on.
- **Live-GPU benchmarks** (S-66) — gated on executing the migration.
- **Post-migration dogfood report** (S-65 T-205 first pass).

---

## [v0.5.2] — 2026-04-21

**Theme:** observability depth, two dead-path wirings fixed, interactive
install UX, and a web-UX design brief.

Patch release landing three focused improvements on top of v0.5.1:
per-capability latency measurement in the recall stream, a pair of
pre-existing "method defined but never called" gaps closed (fusion
gates + vector search), an interactive installer that auto-detects GPU
and recommends the right mode, a `vps-gpu`-specific smoke test, and a
comprehensive Claude-design brief for an animated web install UX.

Test count: 991 → 1003 (+12 net new — crossed the 1000-test milestone).
Quality: mypy 0 errors, ruff 0 errors — unchanged from v0.5.1's clean
baseline.

### Added

**Per-capability latency in recall metrics (S-61 / E-24):**
- `_tool_recall` threads a mutable `perf_ms: dict[str, float]` through
  `_tool_recall_impl`. Phases time themselves with `time.monotonic()`
  brackets; `perf_ms` gets an entry only when the phase actually ran.
- `record_recall_query()`'s `latency_ms_per_capability` field now
  populated from this dict. In v0.5.1 it shipped as an always-empty dict.

**Interactive install mode auto-select (S-62 / E-25):**
- `python -m depthfusion.install.install` with no `--mode` probes GPU
  via `detect_gpu()`, prints a recommendation banner, and either prompts
  (interactive shells) or auto-accepts (`--yes` flag or non-tty shells).
- Recommendation logic: NVIDIA GPU detected → `vps-gpu`; no GPU but
  `DEPTHFUSION_API_KEY` set → `vps-cpu`; otherwise → `local`.
- Explicit `--mode=X` preserves v0.5.1 behaviour (no banner, no probe).

**`vps-gpu`-specific smoke test (S-62 / T-197):**
- `install/smoke.py::run_vps_gpu_smoke()` — three-probe check
  (`nvidia-smi`, `sentence-transformers` import, `LocalEmbeddingBackend.embed()`
  roundtrip). Runs after `install_vps_gpu` writes the env file. Failure
  is a warning (not fatal) — install completes, operator can re-run
  the smoke test after fixing the gap without redoing the install.

**Claude design prompt for web install UX:**
- `docs/design/install-ux-prompt.md` — 326-line design brief for an
  animated landing page / onboarding wizard. Covers hero animation
  ("LLM memory before vs after DepthFusion" with compaction event as
  the dramatic moment), three mode selector cards with hardware-probe
  recommendation, 4-step deployment walkthrough, tier-specific value
  callouts with progressive disclosure, and a live metrics stream
  widget. Self-contained — a designer using Claude's design mode can
  paste it and produce a production UX.

### Changed

- `_tool_recall_impl` signature gains `perf_ms: dict | None = None`
  keyword argument for callers that want per-capability timing.
- `install.main()` — `--mode` no longer required; new `-y/--yes` flag
  for non-interactive auto-accept.
- `pyproject.toml` version `0.5.1` → `0.5.2`.

### Fixed

- **Fusion gates dead-path.** S-51 added `apply_fusion_gates` to
  `RecallPipeline` but `_tool_recall_impl` never called it —
  `DEPTHFUSION_FUSION_GATES_ENABLED=true` was a silent no-op in
  production. v0.5.2 wires the call between BM25 scoring and
  reranking.
- **Vector search dead-path.** T-130 added `apply_vector_search` to
  `RecallPipeline` but `_tool_recall_impl` never called it — the
  GPU's embedding backend couldn't actually participate in recall.
  v0.5.2 wires the call (gated on `DEPTHFUSION_VECTOR_SEARCH_ENABLED`)
  with `rrf_fuse` against BM25 results. Graceful degradation when
  backend returns None (NullBackend / missing sentence-transformers).

### Deprecated

No new deprecations. `--mode=vps` alias and `vps-tier1`/`vps-tier2`
extras from v0.5.0 remain deprecated and are scheduled for v0.6.0
removal per E-23.

### Test metrics

- **1003 passed, 1 skipped** (crossed 1000-test milestone).
- **Ruff:** 0 errors on `src/` and `tests/`.
- **Mypy:** 0 errors on `src/depthfusion` (74 source files).
- Test count delta since v0.5.1: 991 → 1003 (+12 net new).

### New environment variables (v0.5.2 additions)

| Variable | Default | Purpose |
|---|---|---|
| `DEPTHFUSION_VECTOR_SEARCH_ENABLED` | `false` | Opt-in to `apply_vector_search` in the recall path. When enabled, the query + block embeddings are fused with BM25 via RRF. Requires a real embedding backend (LocalEmbeddingBackend on vps-gpu, ChromaDB on vps-cpu Tier 2). |

---

## [v0.5.1] — 2026-04-21

**Theme:** observability surface + integration, RLM task-budget wrapper,
discovery pruner, I-8 compliance wiring, and zero-error quality baseline.

v0.5.1 is a significant feature addition over v0.5.0. Primary work: E-22
(Observability & Hygiene) landed with all four stories — the structured
metrics streams from S-53, the discovery pruner from S-55, the Opus 4.7
task-budget wrapper from S-54, and the integration layer from S-60 that
wires the streams into every capture mechanism and the recall tool. In
addition, S-58 completed the I-8 `config_version_id` wiring left as a
TODO in v0.5.0, and S-59 retired every pre-existing mypy and ruff error
so the default lint + type check commands run clean for the first time
in the v0.5 release window.

Test count delta: 887 → 986 (+99 net new). Quality: mypy 0 errors,
ruff 0 errors — unchanged from v0.5.0's post-S-59 baseline but
preserved through all subsequent feature work.

### Epic summary

| Epic                        | Change in v0.5.1                      |
|-----------------------------|----------------------------------------|
| E-22 Observability & Hygiene| **CLOSED** — all 4 stories (S-53, S-54, S-55, S-60) landed |
| E-23 v0.6 Cleanup           | S-58 (I-8 wiring) + S-59 (mypy/ruff) landed; S-56/S-57 stay for v0.6 proper |
| E-19 GPU Routing            | Unchanged (still benchmark-blocked)    |
| E-20 Capture Mechanisms     | Unchanged (still benchmark-blocked)    |
| E-21 Retrieval Quality      | Unchanged (still benchmark-blocked)    |

### Added

**Structured metrics streams (S-53, T-163..T-165):**
- `MetricsCollector.record_recall_query()` — writes per-query records to
  `YYYY-MM-DD-recall.jsonl` with `backend_used`, `backend_fallback_chain`,
  `latency_ms_per_capability` (empty in v0.5.1; filled in v0.6 per-cap
  latency refactor), `total_latency_ms`, `result_count`, `event_subtype`,
  `config_version_id`.
- `MetricsCollector.record_capture_event()` — writes per-write records to
  `YYYY-MM-DD-capture.jsonl` with `capture_mechanism`, `project`,
  `session_id`, `write_success`, `entries_written`, `file_path`,
  `event_subtype`, `config_version_id`. Unknown mechanisms flagged with
  `capture_mechanism_known: false` but still preserved on disk (forensics).
- `MetricsAggregator.backend_summary()` — per-`capability::backend_name`
  rollup: `count`, `measured_count` (distinct from `count` when latency
  samples are sparse), avg/p50/p95 latency, error_count, error_rate;
  plus per-capability fallback-chain union and overall error rate.
- `MetricsAggregator.capture_summary()` — per-mechanism write rate and
  entries-written totals; unknown mechanisms surfaced separately.
- Module-level `_VALID_EVENT_SUBTYPES` enum including `sla_expiry_deny`
  per DR-018 I-19 ratification.
- `_append_jsonl()` helper in the collector — all structured streams
  acquire `fcntl.flock(LOCK_EX)` on a fresh OFD to serialise concurrent
  writers (gate entries exceed 4 KiB PIPE_BUF so `O_APPEND` atomicity
  alone is insufficient). Numpy-safe `_json_default` for serialisation.

**Production emission wiring (S-60, T-186..T-191):**
- `capture/_metrics.py` — NEW shared `emit_capture_event()` helper used
  by every capture mechanism (extractors + dedup + git hook +
  confirm_discovery).
- `_tool_recall` refactored into a thin wrapper around
  `_tool_recall_impl`; measures total latency via `time.monotonic`,
  probes backend routing via new `_detect_current_backends()` helper
  (skipped on error path for efficiency), emits `recall_query` per call.
- `_tool_confirm_discovery` passes `capture_mechanism="confirm_discovery"`
  to `write_decisions` so emits re-bucket under the higher-level tool
  label — one event per logical operation, not double-counted.
- `dedup.dedup_against_corpus` emits a dedicated event when it runs and
  finds zero duplicates — the metrics stream distinguishes "dedup ran,
  no dupes" from "dedup never ran".
- Git post-commit hook uses a LOCAL `_emit_capture_event` wrapper with
  its own try/except on top of the shared helper — defense in depth so
  a metrics failure can NEVER block a developer's git commit.

**Discovery pruner (S-55, T-169..T-171):**
- `capture/pruner.py` — NEW. `PruneCandidate` frozen dataclass +
  `identify_candidates()` + `prune_discoveries()`. Two heuristics: age
  threshold (default 90d via `DEPTHFUSION_PRUNE_AGE_DAYS`) and
  `.superseded` suffix from S-49 dedup.
- `depthfusion_prune_discoveries` MCP tool (13th tool, always enabled).
  Two-phase: `confirm=False` (default) returns candidates; `confirm=True`
  moves files to `~/.claude/shared/discoveries/.archive/`. Never deletes.
  Archive collisions get timestamp-suffix names.

**RLM task-budget wrapper (S-54, T-166..T-168):**
- `CostEstimator.budget_tokens_for_ceiling(ceiling_usd, model)` —
  translates USD cost ceiling to token budget using model input pricing.
  Docstring explicitly names the output-heavy overshoot hazard (up to
  5× for opus) so operators see the caveat in their IDE.
- `RLMClient.run()` probes the SDK surface for the Anthropic task-budgets
  beta. Dual gate: `DEPTHFUSION_RLM_TASK_BUDGET_ENABLED=true` AND SDK
  exposes `anthropic.task_budget` or `anthropic.types.TaskBudget`.
  `inspect.signature(rlm.RLM.__init__)` confirms rlm accepts the
  `task_budget_tokens` kwarg before passing it. Falls back to v0.4.x
  post-hoc estimation when either gate fails.
- Shipped as a "best-effort wrapper without CIQS claim" per build plan
  §TG-13 kill-criterion — dormant until Anthropic SDK ships the beta.

**I-8 compliance wiring (S-58, T-178..T-180):**
- `GateConfig.version_id()` — deterministic 12-char hex hash of the
  config tuple, attached to every gate-log record. Removes the
  `TODO(I-8)` marker left in v0.5.0 `apply_fusion_gates`.
- Auditors can now reproduce any historical gate decision by looking
  up the `config_version_id` in the gate log against an archived
  config snapshot.

### Changed

- `decision_extractor.write_decisions()` accepts a `capture_mechanism`
  parameter (default `"decision_extractor"`) so wrappers can re-bucket
  metrics under their own mechanism name.
- `_DISCOVERIES_DIR` module-level constants in `decision_extractor`,
  `negative_extractor`, and `git_post_commit` replaced with
  `_default_discoveries_dir()` runtime helpers — fixes a freeze-at-
  import bug where tests couldn't redirect via `monkeypatch.Path.home`.
- `MCP tool count: 12 → 13` (new `depthfusion_prune_discoveries`).
- `pyproject.toml` version `0.5.0` → `0.5.1`.

### Fixed

- **S-59 mypy/ruff cleanup.** Retired 6 pre-existing mypy errors and 5
  pre-existing ruff errors. Both `mypy src/depthfusion` and
  `ruff check src/ tests/` now exit clean for the first time in the v0.5
  release window. Added `types-PyYAML>=6.0.0` to `[dev]` extras.
- **Gate-log config_version_id defaults to empty string.** v0.5.0
  shipped with a `TODO(I-8)` marker and empty `config_version_id` on
  every record; v0.5.1 populates it from `GateConfig.version_id()`
  deterministically.
- **Dedup `no supersessions` observability gap.** v0.5.0 dedup emitted
  nothing when it ran and found zero duplicates. v0.5.1 emits a
  dedicated `write_success=True, entries_written=0` event so the
  metrics stream distinguishes the common case from "dedup never ran."
- **Review gate findings across 4 stories** — 22 issues total (0
  Critical, 6 High/Important, 13 Medium, 3 Low) all caught and fixed
  pre-commit. See individual commit messages for the breakdown.

### Deprecated

No new deprecations. `--mode=vps` alias and `vps-tier1`/`vps-tier2`
extras from v0.5.0 remain deprecated and are scheduled for v0.6.0
removal per E-23.

### Security

- Gate log + recall_query log + capture log streams all record
  `query_hash` (sha256[:12]) only — raw query text is never written to
  disk in any observability stream.
- Prune tool never deletes files; always moves to `.archive/` with
  timestamp-suffix collision handling.
- Anthropic SDK probe for task-budget beta requires explicit env var
  opt-in — a silent SDK upgrade cannot accidentally activate the wrapper.

### Test metrics

- **986 passed, 1 skipped** (sentence-transformers integration test).
- **Ruff:** 0 errors on `src/` and `tests/`.
- **Mypy:** 0 errors on `src/depthfusion` (74 source files).
- Test count delta since v0.5.0: 887 → 986 (+99 net new).

### New environment variables (v0.5.1 additions)

| Variable | Default | Purpose |
|---|---|---|
| `DEPTHFUSION_PRUNE_AGE_DAYS` | `90` | Age threshold for the discovery pruner (S-55). |
| `DEPTHFUSION_RLM_TASK_BUDGET_ENABLED` | `false` | Opt-in to the Anthropic task-budgets beta wrapper (S-54). |

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
