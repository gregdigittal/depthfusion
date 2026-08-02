# DepthFusion Architectural Review — TRINITY Verifier
**Date:** 2026-07-11 · **Reviewed at:** v2.1.1 (main) · **Method:** code-first, every claim checked against source

---

## Executive Summary

DepthFusion is a competently engineered BM25-over-markdown recall system wrapped in the vocabulary of a cognitive memory architecture. The core that actually runs by default — file-based session capture, a clean dependency-free BM25 kernel (`src/depthfusion/retrieval/bm25.py`), FTS5 indexing, telemetry, and a well-gated MCP tool registry — is solid, tested, and honest about its degradation paths. But the README's status line describes a system where the Knowledge Graph, the E-31 cognitive layer (7 flags, all default OFF at `core/config.py:120-127`), Mamba B/C/Δ gating (`DEPTHFUSION_FUSION_GATES_ENABLED`, default false at `retrieval/hybrid.py:217`), cognitive scoring (default false at `hybrid.py:316`), HNSW vectors (mode-dependent, absent in default `local` mode), and Redis-backed fabric streaming are all "active." None of them are, out of the box. Worse, one of the 30 advertised MCP tools (`recommend_model`) has **no dispatcher and raises `ValueError` on every invocation** — verified live during this review. The headline CIQS score of ~95-97 is a projection resting on default-off features, extrapolated from a saturated n=8 goldset and n=4 latency samples. DepthFusion today is a good local retrieval tool with an aspirational architecture stapled on top; the gap between the two is the central finding of this report.

---

## Section 1: Claims vs Reality

### Claim 1: "30 canonical MCP tools (17 always-on, 9 feature-flagged, 3 bridge, 1 model-intelligence)"
**Verdict: ⚠️ PARTIAL — 30 registered, 29 dispatchable. One tool is broken.**

- The registry contains exactly 30 tools (`mcp/tools/_registry.py`, TOOLS dict — counted programmatically: 30 keys).
- `_TOOL_FLAGS` (`_registry.py:207-246`) gates 9 tools: `publish_context` (router_enabled), 3 graph tools (graph_enabled), and 5 E-31 tools (cognitive_retrieval / decision_memory / operational_memory). Arithmetic checks out.
- **But `recommend_model` is advertised and has no dispatch branch.** `_dispatch_tool` in `mcp/server.py:207-266` ends at `depthfusion_list_providers` then `raise ValueError(f"No dispatcher for {tool_name}")`. The handler exists (`mcp/tools/recommender_tools.py:58` `_tool_recommend_model`) and authz maps it (`mcp/authz.py:103`) — it was simply never wired. Verified live:
  ```
  >>> _dispatch_tool('recommend_model', {...}, cfg, None)
  ValueError: No dispatcher for recommend_model
  ```
  The HTTP path shares the same broken dispatcher (`http_server.py:43` imports `_process_request`). Every client that calls the advertised "model recommendation engine" via MCP gets an exception. This shipped in v2.x and no test caught it — see Section 3.
- Related: the README (line 13) lists `record_model_telemetry` alongside `recommend_model` as part of the tool surface. It is a Python function (`mcp/tools/telemetry_tools.py:21`), **not** a registered MCP tool — it is absent from TOOLS and `_TOOL_FLAGS`.

### Claim 2: "3482+ tests passing · 0 ruff · 0 mypy"
**Verdict: ✅ TRUE (test count and ruff verified; mypy not re-verified)**

- `pytest --collect-only`: **3530/3577 collected (47 deselected)** — the claim of 3482+ holds and has grown since it was written. 211 test files under `tests/`.
- `ruff check src tests`: "All checks passed!" — verified during this review.
- A 42-test subset (bm25 + scorer) passed in 1.58s. Full-suite pass not re-run here, but CI (`.github/workflows/ci.yml`, `nightly.yml`) exists. mypy taken on faith.
- Caveat: test *count* is not test *quality* — see Section 3.

### Claim 3: "OIDC+PKCE authentication, device enrollment, RBAC, ACL records"
**Verdict: 🔒 BEHIND FLAG — real code, off by default, only meaningful on the HTTP path**

- The identity module is substantive, not vaporware: 14 files including `token_validator.py` (232 lines of explicit RS256 validation with algorithm pinning, `kid`→JWKS key selection, exp/nbf skew, iss/aud/nonce checks — `token_validator.py:1-35`), `jwks_cache.py`, `oidc_client.py`, `device_registry.py`, `principal_store.py`, `service_account.py`.
- But it only bites when the HTTP surfaces are on, and `mcp_http_enabled` and `rest_api_enabled` both default **False** (`config.py:128,131`). Default stdio sessions run with `principal=None` (`server.py` `_process_request` docstring). So "authentication" describes an optional transport, not the default deployment.
- Deployable today? Yes, if you enable the flags and configure `DEPTHFUSION_OIDC_*` — the plumbing exists. Deployed by default? No.

### Claim 4: "Tauri desktop app (macOS + Windows)"
**Verdict: ✅ TRUE (shippable; shipping verified via releases)**

- `app/` is a real Vite+React+Tauri project (v2.1.1, `app/package.json`), `app/src-tauri/` has Cargo.toml, tauri.conf.json, capabilities.
- `.github/workflows/release-desktop.yml` builds universal-apple-darwin + x86_64-pc-windows-msvc on version tags; `gh release list` shows v2.1.1 published 2026-07-11 (plus older releases, though three of the last five are **Drafts** — v2.1.1/v2.1.0/v2.0.1 drafts suggest the release pipeline needs manual finishing).

### Claim 5: "CIQS Overall ~95-97 (projected)"
**Verdict: ❌ FALSE as a capability claim; ⚠️ PARTIAL as an honestly-labelled projection**

- README line 66 labels it "Estimated (projection)" with "Medium-high" confidence — credit where due.
- But the **measured** data stops at ~85 (v0.3.0 local, README line 41-44). Everything above 85 — the v1.0.0 "~94-96" and v2.0.0 "~95-97" rows — is extrapolation from features that are **off by default** (E-31 cognitive flags all False, `config.py:120-127`; fabric requires Redis; PRECEDED_BY edges require the graph, `graph_enabled=False` at `config.py:83`). A projection about a configuration no default user runs, validated against no end-to-end eval, is not a score. It's a roadmap number wearing a benchmark's clothes.
- The README's own footnote (line ~52) concedes the micro-benchmark "is **not** a production-quality measure." The status line at the top of the README does not carry that disclaimer.

### Claim 6: "BM25 micro-benchmark: precision@1 = 1.000, precision@5 = 1.000"
**Verdict: ⚠️ PARTIAL — real numbers, vacuous test**

- `scripts/benchmark.py` exists and runs against `tests/fixtures/recall_goldset.jsonl` — which contains **exactly 8 queries** (verified: `wc -l` = 8), each with its own per-query micro-corpus of 3-N hand-curated docs. A fresh BM25 index is built per query over only that query's tiny corpus.
- At n=8 with all metrics at 1.000, the benchmark has zero discriminating power — the README's own footnote admits this verbatim ("at n=8 with perfect scores there is no discriminating power left"). It's a smoke test, correctly framed in the fine print, incorrectly implied as evidence in the summary claims. The n=4 recall-latency sample (37-372 ms) has the same problem: it's an anecdote, not a distribution.

### Claim 7: "Event Graph Fabric (E-46)"
**Verdict: ⚠️ PARTIAL — graph-append works without Redis; streaming does not**

- `core/event_store.py` defines a `StreamBackend` Protocol and `RedisStreamBackend`. Redis is an optional extra (`pyproject.toml` `fabric = ["redis>=5.0"]`).
- Without `DEPTHFUSION_REDIS_URL`, `_get_fabric_store()` builds `EventStore(graph=..., stream=None)` (`mcp/tools/_state.py:136-139`) — events append to the graph, but `subscribe_stream()` **raises RuntimeError** (`event_store.py:541-545`). So "multi-agent memory fan-out," the headline fabric capability, requires Redis, which is not installed by default. And since the graph itself is behind `graph_enabled=False`, the fabric's storage substrate is also off by default. The "Fabric" in default config is a write-only log to a disabled graph.

### Claim 8: "SkillForge SF-2 + Mamba B/C/Δ + HNSW vector layer active"
**Verdict: ❌ FALSE as stated — none are active by default**

- Mamba B/C/Δ gates: implemented in `fusion/gates.py` (a genuinely nice module — see Section 2), but gated by `DEPTHFUSION_FUSION_GATES_ENABLED` which defaults **"false"** (`retrieval/hybrid.py:217`).
- Cognitive scoring in the recall path: `DEPTHFUSION_COGNITIVE_SCORING` defaults **"false"** (`hybrid.py:316`).
- HNSW: `retrieval/hnsw_store.py` exists, but the default mode is `local` (`utils/mode.py:17` — unset env falls back to "local"), and `local` mode is explicitly BM25-only (`pyproject.toml`: `local = []`, no hnswlib). HNSW activates only in vps-gpu/mac-mlx modes with the extra installed, and degrades silently to BM25 otherwise (`_state.py:113-124`).
- "Active" here means "code exists and is reachable if you flip 2-3 env vars and install extras." That is not what "active" means to a reader.

### Claim 9: "MemoryConsolidator (autonomic loop)"
**Verdict: ⚠️ PARTIAL — 60 lines of Jaccard-style token overlap, DRY-RUN, behind a default-off flag**

- `cognitive/consolidator.py` is **60 lines total**. The "near-duplicate detection" is `len(ta & tb) / max(len(ta), len(tb))` over lowercase whitespace-split token sets (`consolidator.py:53-59`), O(n²) pairwise. The "archive candidates" check is a date comparison.
- It never mutates anything (README line 520: "observes what it would merge or archive but never mutates") and sits behind `autonomic=False` (`config.py:127`). Calling a disabled, dry-run, token-set-overlap scan an "autonomic loop" is the single largest naming-to-substance gap in the codebase. DRY-RUN as a rollout strategy is defensible; marketing it in the status line as a shipped capability is not.

### Claim 10: "Fernet cache encryption, OS keychain token vault"
**Verdict: ❌ FALSE for the cache in production; ⚠️ PARTIAL for keychain**

- `cache/manager.py` implements Fernet encryption competently (ephemeral-key warning at `manager.py:61-73` is good hygiene). But **nothing in production `src/` instantiates `CacheManager`** — a repo-wide grep for `CacheManager(` outside `cache/` and tests returns nothing. It is a tested library module with no call site. "Fernet cache encryption" describes code that never executes in any deployed path.
- `identity/device_keychain.py` exists; its only non-test consumer in the Python tree is `authz/policy_snapshot.py`. The Tauri app presumably handles its own keychain on the Rust side. The claim is directionally real but its Python-side integration is thin.

### Claim 11: "Multi-Provider Context Bridge (E-48)"
**Verdict: ✅ TRUE**

- Real parsers for ChatGPT, Gemini, DeepSeek plus generic (`parsers/chatgpt.py`, `gemini.py`, `deepseek.py`, `generic.py`), fixtures for each (`tests/fixtures/*-sample.json`), and three always-on bridge tools (`depthfusion_bridge`, `depthfusion_ingest_conversation`, `depthfusion_list_providers` — `_registry.py:242-244`, dispatched at `server.py:259-264`). This one delivers what it says.

### Claim 12: Feature flag defaults — what % of the described system is on?
**Verdict: the default install runs roughly a third of the described architecture**

Default-ON: fusion, session, rlm, router, session_selective, ambient_capture, auto_recall, fts (`config.py:79-148`). Default-OFF: `graph_enabled`, `haiku_enabled`, `tagger_llm`, all 7 E-31 cognitive flags, `rest_api_enabled`, `api_public`, `mcp_http_enabled` (`config.py:83-131`), plus env-only gates `DEPTHFUSION_FUSION_GATES_ENABLED` and `DEPTHFUSION_COGNITIVE_SCORING` (both false, `hybrid.py:217,316`), plus mode-gated vector search (default `local` = BM25-only). Of the 30 tools, 8 are invisible by default (3 graph + 5 E-31). **The default experience is: BM25 + FTS5 recall over markdown, session capture, telemetry, bridge ingestion. The graph, cognition, vectors, gates, fabric streaming, HTTP, and auth are all opt-in.** The README status line presents the union; users get the intersection.

---

## Section 2: Architectural Strengths

1. **The registry/flag pattern is the right design.** `_TOOL_FLAGS` (`_registry.py:207-246`) maps every tool to a config attribute or None; `get_enabled_tools()` derives the advertised surface from config. Single source of truth for *visibility* — the failure (Section 3) is that dispatch doesn't share it.
2. **BM25 kernel is exemplary small-core engineering.** `retrieval/bm25.py` (130 lines): zero dependencies, smoothed Robertson IDF, and `rank_with_mask()` (T-572) does ACL pre-filtering *before* scoring rather than post-filtering — correct both semantically and for performance, with the equivalence documented in the docstring.
3. **Security posture on the wire is genuinely careful.** `token_validator.py` pins RS256, rejects `none`/HMAC (algorithm-confusion), takes keys only via JWKS `kid` lookup, never from the token (`token_validator.py:9-19`). `http_server.py:10-11` binds 127.0.0.1 by default with an explicit warning about 0.0.0.0 — compliant with loopback-by-default discipline. Redis in the fabric extra is documented loopback-only (`pyproject.toml` fabric comment).
4. **Graceful degradation is systematic, not ad hoc.** HNSW init failure → BM25-only with logged fallback (`_state.py:113-124`); gates fail-open on math errors (`gates.py` header: "retrieval correctness > gate signal"); unknown mode → local with warning (`utils/mode.py:25-30`). The system errs toward returning results.
5. **Config is one testable dataclass.** `DepthFusionConfig` (`config.py:70-76`) instantiates directly for tests with no env side effects; `from_env()` is the only env reader. Clean.
6. **The README's fine print is unusually honest.** The micro-benchmark footnote explicitly disclaims its own headline numbers. The three-measurement disambiguation (README ~line 37) is better epistemics than most projects manage. The problem is the status line contradicts the footnotes.
7. **CI breadth**: 11 workflows including sbom.yml, security.yml, nightly.yml, benchmark.yml — regression infrastructure exists even if the eval corpus doesn't.

---

## Section 3: Architectural Problems

### 3.1 The dispatcher is hand-maintained and it already failed
`server.py:207-266` is a 30-way if/elif chain, maintained by hand, parallel to `_registry.py` TOOLS, `_TOOL_FLAGS`, `authz.py` capability map, and TOOL_SCHEMAS. Four registries, one wiring point, no consistency check. Result: `recommend_model` was added to three of the four and shipped broken. This is not a hypothetical smell — it is a live production defect in the flagship E-64 feature.

### 3.2 Test quality: 3530 tests missed a tool that throws on every call
`tests/test_budget.py`, `tests/integration/test_recommender.py`, and `tests/test_mcp_authz.py` all exercise `recommend_model` — as a *function* or as an *authz entry*. Nothing tests the MCP dispatch path end-to-end for all registered tools. 3530 tests that unit-test every leaf but never assert "every advertised tool is callable" is the definition of shallow coverage at the integration seam. The count is real; the coverage of the contract surface is not.

### 3.3 The cognitive layer is a weighted sum with dead inputs
`cognitive/scorer.py` is 56 lines: eight hand-tuned weights, a dot product, a clamp. Fine as a component — but in the default `ScoringContext`, `regime_match`, `graph_proximity`, `historical_usefulness`, and `workflow_intent` default to 0.0 and have no producers wired in default config (graph off, cognitive flags off). Five of eight "components" of the "8-component cognitive scoring" are structurally zero for every default user. The weights (`semantic: 0.25, lexical: 0.18, ...`) have no empirical justification anywhere in the repo — no ablation, no eval that could distinguish these weights from any others (the n=8 goldset saturates regardless).

### 3.4 Naming inflation as complexity debt
"Event Graph Fabric" = an append log to a disabled graph plus an optional Redis stream. "Autonomic loop" = a disabled dry-run token-overlap scan. "Mamba B/C/Δ" = three threshold filters (well-built ones — `gates.py` is good code) borrowing the prestige of a state-space architecture for what is cosine/BM25 thresholding. Each name writes a check the default configuration can't cash. This is debt because every future contributor must first discover that the impressive noun maps to a modest function.

### 3.5 Configuration sprawl hides the product
~20 boolean flags plus 6 backend selector strings plus 4 install modes plus env-only gates that bypass `DepthFusionConfig` entirely (`DEPTHFUSION_FUSION_GATES_ENABLED` and `DEPTHFUSION_COGNITIVE_SCORING` are read raw in `hybrid.py:217,316` — not even fields on the config dataclass, so `depthfusion_status` can't report them). There is no single answer to "what is running right now?" The combinatorial config space (2^20 × 4 modes) is untestable; the tested path is the default path; the *described* path is not the default path.

### 3.6 Deployment gap: library-only "features"
`CacheManager` (Fernet) has zero production call sites. `record_model_telemetry` is claimed as a tool but isn't one. The consolidator can't write. Features exist on a spectrum from "wired and on" to "wired and off" to "exists but unreachable" — the README flattens all three into present tense.

### 3.7 The "projected" CIQS problem
A rigorous evaluation would need: (a) a goldset of **≥200 queries** mined from real session corpora (the machinery exists — `scripts/mine_session_prompts.py`, `scripts/generate_synthetic_corpus.py`); (b) a **shared realistic corpus** (hundreds of discovery/memory files), not per-query 3-doc micro-corpora where BM25 trivially wins; (c) graded relevance with **MRR/nDCG**, not precision@k on planted answers; (d) an **A/B between default config and full-flag config** — because right now there is no evidence the cognitive layer, gates, or graph *improve* retrieval at all; (e) latency distributions at n≥100, not n=4. Until then, "~95-97 projected" should not appear within a status line that begins "3482+ tests passing" — the juxtaposition launders a guess with the credibility of a measurement.

---

## Section 4: Refactoring Priorities (ranked)

1. **Fix `recommend_model` dispatch today** (add the branch in `server.py`, import `_tool_recommend_model`), and **replace the elif chain with a registry-driven dispatch table**: `_DISPATCH: dict[str, Callable]` living next to TOOLS in `_registry.py`. Add one parametrized test: *for every key in TOOLS, dispatch with minimal args and assert no `ValueError("No dispatcher...")`*. Add a second asserting TOOLS, `_TOOL_FLAGS`, authz map, and dispatch table have identical key sets. ~2 hours of work; eliminates the entire drift class.
2. **Rewrite the README status line** as three lists: *Measured & on by default* / *Implemented, behind flag X* / *Projected*. Delete the word "active" for anything requiring env vars. The footnotes already contain the honest version — promote them.
3. **Build the real eval before building anything else retrieval-related.** 200+ query goldset over a shared corpus, MRR/nDCG, default-vs-full-flags A/B. This is the only way to learn whether the E-31 layer, gates, and graph earn their complexity — right now their retrieval value is unmeasured, and they might be neutral or negative.
4. **Move the rogue env gates into `DepthFusionConfig`** (`DEPTHFUSION_FUSION_GATES_ENABLED`, `DEPTHFUSION_COGNITIVE_SCORING`, `DEPTHFUSION_REDIS_URL`) and make `depthfusion_status` report the full effective flag state. Then collapse the flag space into 3-4 named **profiles** (e.g., `minimal`, `standard`, `server`, `research`) so the tested configurations are enumerable.
5. **Delete or commit — pick one per orphan.** CacheManager: wire it into a real path (HTTP response cache? recall cache?) or move it out of the claims. Consolidator: either implement embedding-based similarity with a concrete write-mode graduation criterion ("after N dry-run cycles with false-positive rate < X"), or demote it to a maintenance script. Fabric streaming: define the no-Redis story (file-based stream backend exists as a pattern — `bus_backend: "file"` already does this for the context bus) or document Redis as required.
6. **Fix the release pipeline** — three of the last five GitHub releases are stuck in Draft (v2.0.1, v2.1.0, v2.1.1 drafts alongside the published v2.1.1). Either automate publish or delete the stale drafts.
7. **Decide what the stdio principal story is.** `_process_request`'s docstring says unauthenticated stdio calls "will be rejected... (currently none are)" — the comment contradicts observable behavior (stdio works). Either the docstring is stale or the legacy shim path deserves explicit documentation; ambiguity in the auth model is the worst state.

---

## Section 5: Verdict

DepthFusion is two systems sharing a repo. System one — the default install — is a well-built, defensively coded, genuinely useful BM25+FTS5 recall layer with clean session capture, honest degradation, real multi-provider ingestion, and a strong security posture on its network edges. It deserves its 3530 tests and clean linters. System two is an architecture of aspirations — cognitive scoring with dead inputs, an autonomic loop that may not act, a fabric that needs Redis it doesn't ship with, a graph that's off, and a 95-97 score that no one has measured — described in the present tense as if it were system one. The single live bug found (an advertised tool that throws `ValueError` on every call, uncaught by 3530 tests) is the perfect synecdoche: the parts were built and tested individually; the claim surface was never verified as a whole. The fix is not more features. It is a registry-driven dispatcher, a real evaluation corpus, three honest lists in the README, and the discipline to delete or finish every module that currently exists only to be named. Do those four things and system one's genuine quality stops being undermined by system two's borrowed vocabulary.

---
*Evidence gathered 2026-07-11 against working tree at commit cd9bf44 (main). All file:line references verified by direct read or executed check. Live verification: `recommend_model` dispatch failure reproduced in-process; pytest collection (3530/3577); ruff clean; goldset n=8 confirmed; release drafts confirmed via `gh release list`.*
