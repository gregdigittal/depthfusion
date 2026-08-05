# Rectification Plan — DepthFusion v2.1.1 → v2.2 and beyond

> Date: 2026-07-11
> Author: architecture rectification pass
> Scope: close the gap between README/CIQS claims and default-config code reality.

---

## Verified code state (as of this pass)

| Finding | Verified state |
|---|---|
| `recommend_model` dispatch | **FIXED.** `server.py:98` imports `_tool_recommend_model`; `server.py:267-268` dispatches `depthfusion_recommend_model`. No parity test yet. |
| Dispatcher | 30-way `if/elif` chain in `server.py:209-270`, terminal `raise ValueError(f"No dispatcher for {tool_name}")`. Parallel sources of truth: `tools/_registry.py` (`_TOOL_FLAGS` L207, `TOOL_SCHEMAS` L264), `authz.py`. No consistency check. |
| Rogue env gates | `hybrid.py:217` reads `DEPTHFUSION_FUSION_GATES_ENABLED`, `hybrid.py:316` reads `DEPTHFUSION_COGNITIVE_SCORING` — both via raw `os.environ`/`os.getenv`, **not** on `DepthFusionConfig`. |
| `depthfusion_status` | `tools/system.py::_tool_status` reports only 4 flags (`rlm_enabled`, `router_enabled`, `session_enabled`, `fusion_enabled`) + `enabled_tools`. ~16 other config flags and both rogue gates are invisible. |
| Consolidator | `cognitive/consolidator.py`, 60 lines. `_token_similarity` = Jaccard over `.split()` token sets. Returns candidate tuples only — **no write path**. Default-off. |
| CacheManager (Fernet) | Zero production call sites (`grep "CacheManager("` across `src/` minus tests/module = empty). |
| Cognitive scorer | `cognitive/scorer.py`, 56 lines, 8 weights. In default config 4 inputs are hard-0.0 (`regime_match`, `graph_proximity`, `historical_usefulness`, `workflow_intent`); `recency` defaults 0.5, `confidence` 0.7. No producers wired, no ablation. |
| Releases | `v2.1.1` published (Latest). **Stuck as Draft:** a duplicate `v2.1.1`, `v2.1.0`, `v2.0.1`, `v1.1.0`. |
| Goldset | `tests/fixtures/recall_goldset.jsonl`, **n=8**. Mining infra present: `scripts/mine_session_prompts.py`, `scripts/generate_synthetic_corpus.py`, `scripts/ciqs_harness.py`, `scripts/benchmark.py` (253 lines, BM25-only, local, precision@k). |
| Highest IDs | Epics → `E-66`. Stories → `S-218`. Tasks → `T-759`. New work starts at `E-67 / S-219 / T-760`. |

---

## Philosophy

The decision rule for every gap is binary and applied per claim: **either make the claim true in the default configuration a normal user runs, or delete the claim from the headline and demote it to a clearly-labelled "behind a flag" / "roadmap" tier.** No claim may live in the README headline while its code path is default-off, unmeasured, or unwired. "Active" is reserved for features that execute in the shipped default config. Everything else is "Behind flag" (exists, tested, opt-in) or "Projected" (designed, not yet measured). Where a feature is genuinely valuable but not safe-by-default, we tier it under a named profile (`server`/`research`) rather than describing it as universal. The CIQS number specifically may only cite measured values on a stated config; projections move to a Roadmap section. This converts an honesty problem into an engineering backlog.

---

## Wave 0 — Must-fix before next release (1–2 days)

### 0.1 Dispatcher parity test
- **Problem:** `recommend_model` slipped through because nothing asserts that every advertised tool is dispatchable. The 30-way elif chain drifts from `TOOL_SCHEMAS`/`_TOOL_FLAGS` silently.
- **Fix:** Add `tests/mcp/test_dispatch_parity.py`. Enumerate keys from `TOOL_SCHEMAS` (and `_TOOL_FLAGS`) in `tools/_registry.py`; for each, assert `_dispatch(tool_name, {}, config)` does **not** raise `ValueError("No dispatcher…")`. Assert the reverse too: every `elif tool_name == "…"` branch in `server.py` has a matching `TOOL_SCHEMAS` key (parse the dispatch function's AST or a hand-maintained `DISPATCHABLE` set). Fail on any asymmetry.
- **Acceptance test:** `pytest tests/mcp/test_dispatch_parity.py` green; deliberately deleting the `recommend_model` branch makes it red.
- **Files:** `tests/mcp/test_dispatch_parity.py` (new); optionally extract a `DISPATCHABLE: frozenset[str]` in `server.py` to make the set introspectable.

### 0.2 Rogue env gates → `DepthFusionConfig`
- **Problem:** `DEPTHFUSION_FUSION_GATES_ENABLED` and `DEPTHFUSION_COGNITIVE_SCORING` are read raw in `hybrid.py`; they bypass config, can't be reported, and can't be set by profile.
- **Fix:** Add `fusion_gates_enabled: bool = False` and `cognitive_scoring: bool = False` to `DepthFusionConfig` (`core/config.py`), each loaded from its existing env var name in the config's `from_env` path (preserve the current env names for back-compat). In `hybrid.py:217,316`, replace the raw `os.environ` reads with `self._config.fusion_gates_enabled` / `.cognitive_scoring` (thread config into `HybridRetriever` if not already present; it is constructed with config elsewhere).
- **Acceptance test:** unit test sets `config.fusion_gates_enabled=True` and asserts gates run without the env var present; sets env var only and asserts `from_env` still honours it.
- **Files:** `core/config.py`, `retrieval/hybrid.py`, `tests/retrieval/test_hybrid_gates_config.py` (new).

### 0.3 `depthfusion_status` reports all effective flags
- **Problem:** status shows 4 of ~20 flags; the two rogue gates are invisible. Operators can't tell what's actually on.
- **Fix:** Rewrite `_tool_status` (`tools/system.py`) to emit an `effective_flags` object built by reflecting over `DepthFusionConfig` boolean/selector fields (`dataclasses.fields`), including the two newly-added gates and the backend selectors (`reranker_backend`, `embedding_backend`, etc.). Group into `on_by_default`, `behind_flag`, `backends`, `install_mode`. Keep the existing top-level keys for back-compat.
- **Acceptance test:** call `_tool_status(config)`; assert `fusion_gates_enabled` and `cognitive_scoring` keys present and reflect config; assert count of reported flags == count of config flag fields.
- **Files:** `tools/system.py`, `tests/mcp/test_status_flags.py` (new).

### 0.4 README status-line rewrite (three honest lists)
- **Problem:** headline describes the union of all features; users get the intersection (~1/3). "Active" used for default-off things.
- **Fix:** Replace the status line with three explicit lists:
  - **On by default:** FTS/BM25 hybrid recall, RRF fusion, session selection, RLM router, decay/salience, ambient capture, auto-recall, model recommender.
  - **Behind a flag / profile:** knowledge graph (`graph_enabled`), fusion gates (Mamba B/C/Δ), cognitive scoring, HNSW, Event Graph Fabric streaming, MemoryConsolidator, OIDC/RBAC, Fernet offline cache, LLM backends (reranker/extractor/linker/summariser/embedding).
  - **Projected (not yet measured):** CIQS 95–97 target, SkillForge SF-2 recursive scoring.
- **Acceptance test:** README review; every feature named in the old headline appears in exactly one of the three lists; no default-off feature is called "active".
- **Files:** `README.md`.

### 0.5 Release pipeline: clear stale drafts
- **Problem:** duplicate `v2.1.1`, plus `v2.1.0`/`v2.0.1`/`v1.1.0` stuck as Draft — confusing provenance.
- **Fix:** Decide per draft: the duplicate `v2.1.1` draft → delete (`gh release delete`). Superseded historical drafts (`v2.1.0`, `v2.0.1`, `v1.1.0`) → either publish (if they were real releases) or delete. Add a release-publish step to CI so future tags auto-publish rather than landing as drafts (`gh release edit <tag> --draft=false` or `softprops/action-gh-release` with `draft: false`).
- **Acceptance test:** `gh release list` shows no unexpected `Draft` rows; CI dry-run on a test tag produces a published (non-draft) release.
- **Files:** `.github/workflows/release.yml` (or equivalent), manual `gh` cleanup.

---

## Wave 1 — Make the default experience match the claims (1–2 weeks)

### 1.1 Named configuration profiles
- **Problem:** ~20 booleans × 6 backend selectors × 4 install modes + 2 rogue gates = an untestable combinatorial space. No named presets.
- **Fix:** Add `DepthFusionConfig.from_profile(name)` with four presets:
  - `minimal` — BM25/FTS only, no LLM backends, no graph. (CI default.)
  - `standard` — default on-by-default set (today's shipped default). 
  - `server` — adds graph, OIDC/RBAC, REST API, Fernet cache, Event Graph Fabric (Redis assumed).
  - `research` — server + fusion gates + cognitive scoring + LLM backends (the "full-flag" config CIQS projections rest on).
  Profiles set the flag block declaratively; individual env vars still override. Surface active profile name in `depthfusion_status`.
- **Acceptance test:** `from_profile("research")` enables fusion gates + cognitive scoring; `from_profile("minimal")` disables all LLM backends; round-trip test that a profile + override yields the expected effective config.
- **Files:** `core/config.py`, `core/profiles.py` (new), `tools/system.py`, docs.

### 1.2 CacheManager wiring — or delete the claim
- **Problem:** Fernet `CacheManager` has zero call sites; "encrypted offline cache" is a library, not a feature.
- **Fix (preferred):** Wire it as an HTTP response cache on `GET /api/v1/search` in `http_server.py` — key by `(principal, query, top_k, scope)`, TTL-bounded, Fernet-encrypted at rest, enabled only in `server` profile (`rest_api_enabled` + a `cache_enabled` flag). On cache miss, run retrieval and populate.
- **Fix (fallback):** If wiring is deferred, remove "encrypted offline cache" from README's active/behind-flag lists and mark it "roadmap".
- **Acceptance test:** integration test hits `/api/v1/search` twice with identical args under `server` profile; second call served from cache (assert cache hit metric / no retrieval call); ciphertext on disk is not plaintext-readable.
- **Files:** `mcp/http_server.py`, `cache/manager.py` (wiring only), `core/config.py`, `tests/http/test_search_cache.py` (new).

### 1.3 Consolidator: real similarity + write criteria, or rename
- **Problem:** "autonomic consolidation loop" is 60 lines of Jaccard-over-`split()` in permanent DRY-RUN with no write path.
- **Fix (preferred):** (a) Replace token Jaccard with embedding cosine similarity (reuse the embedding backend; fall back to Jaccard only when embeddings unavailable). (b) Define a concrete write-mode criterion: merge only when `cosine ≥ 0.92` **and** both memories share scope **and** neither is pinned **and** the merge has been surfaced/confirmed (or auto-merge only above a stricter `0.97` with an audit-log entry). (c) Keep DRY-RUN as the default; graduation criteria in Wave 3.
- **Fix (fallback):** Rename to "maintenance script", remove "autonomic loop" from README, document it as an operator-run tool.
- **Acceptance test:** near-duplicate pair with cosine 0.95 is flagged; paraphrase with low token overlap but high cosine is now caught (was missed by Jaccard); pinned memories never appear as candidates.
- **Files:** `cognitive/consolidator.py`, `tests/cognitive/test_consolidator_embeddings.py` (new), README.

---

## Wave 2 — Measure before claiming (2–4 weeks)

### 2.1 Expand goldset to ≥200 queries
- **Problem:** `recall_goldset.jsonl` is n=8, saturated (everything scores ~1.0), zero discriminating power. CIQS ~85 measured is on this.
- **Fix:** Use `scripts/mine_session_prompts.py` to extract real queries from session history and `scripts/generate_synthetic_corpus.py` to build realistic multi-document corpora (not n=8 micro-corpora). Target ≥200 queries with graded relevance labels (0/1/2), spanning recall, decision-lookup, incident-lookup, cross-project. Store as `tests/fixtures/recall_goldset_v2.jsonl`; keep the old one for regression.
- **Acceptance test:** goldset has ≥200 entries with graded labels; score variance across the set is non-trivial (std-dev > 0.05 on the primary metric) — i.e., it discriminates.
- **Files:** `tests/fixtures/recall_goldset_v2.jsonl` (new), harness updates.

### 2.2 Add MRR@10 and nDCG@5
- **Problem:** `benchmark.py` reports precision@k only; can't distinguish rank quality, and a saturated goldset makes precision meaningless.
- **Fix:** Add `MRR@10` and `nDCG@5` (graded-relevance) to the harness alongside precision@k. Emit all three per-query and aggregate.
- **Acceptance test:** harness outputs MRR@10 and nDCG@5 in the JSON; unit test with a hand-crafted ranking verifies both metrics against known values.
- **Files:** `scripts/benchmark.py` (+ `scripts/ciqs_harness.py`), `tests/scripts/test_metrics.py` (new).

### 2.3 A/B: default vs full-flag config
- **Problem:** the CIQS 95–97 projection assumes the cognitive layer + gates + graph improve retrieval — never measured against default.
- **Fix:** Run the ≥200 goldset through `standard` profile vs `research` profile. Report the delta on MRR@10 / nDCG@5. This is the load-bearing experiment: it tells us whether the default-off features are worth turning on.
- **Acceptance test:** a committed report (`.agent-hub/outputs/ab-standard-vs-research-<date>.md`) with per-metric deltas and significance (paired test over 200 queries).
- **Files:** `scripts/ciqs_compare.py` (extend), report artifact.

### 2.4 README numbers = measured only
- **Fix:** Replace CIQS "95–97" headline with the measured `standard`-profile number and (if 2.3 shows a win) the measured `research`-profile number. Move the 95–97 target into a "Roadmap" section with the hypothesis and current gap.
- **Acceptance test:** no unqualified CIQS number in the README headline; every number cites a profile and a date.
- **Files:** `README.md`.

---

## Wave 3 — Make the claimed features real by default (4–8 weeks)

### 3.1 Cognitive scoring — wire the dead inputs
- The 4 hard-0.0 inputs need producers: `regime_match` (requires a regime classifier — currently none), `graph_proximity` (requires `graph_enabled` + a proximity query against the KG), `historical_usefulness` (requires wiring the recall-feedback store, which exists per S-72, into scoring), `workflow_intent` (requires an intent tagger). Path: (a) enable graph in `research` profile so `graph_proximity` has a producer; (b) feed `FeedbackStore` outcomes into `historical_usefulness`; (c) ship `regime_match`/`workflow_intent` behind their own flags or drop those weights and re-normalise. Run an **ablation** (per input) on the Wave-2 goldset before enabling any weight by default.
- Deliverable: ablation report ranking which of the 8 weights actually move MRR/nDCG.

### 3.2 Mamba B/C/Δ fusion gates by default
- Cost to enable: added latency per recall + the fail-open complexity already in `hybrid.py`. Decision gated on Wave 2.3 A/B — enable in `standard` only if the delta is positive and latency budget holds; otherwise keep in `research`.

### 3.3 Event Graph Fabric — the no-Redis story
- Either implement a file-based stream backend (mirror the existing `bus_backend: "file"` pattern in `core/config.py`) so streaming works without Redis, or document Redis as a hard requirement and tier the feature strictly under the `server` profile. Do not describe it as universal.

### 3.4 MemoryConsolidator — graduate from DRY-RUN
- Define graduation criteria: after N≥5 consolidation cycles on real data, measured false-positive merge rate < X% (e.g. <2%) against a human-labelled sample. Only then flip a `consolidator_write_enabled` flag (default off) and, later, on in `server` profile. Every auto-merge writes an audit-log entry and is reversible.

### 3.5 OIDC/RBAC in the `server` profile
- Make Entra/OIDC + RBAC (E-49/E-50) actually run under `server` profile with clear setup docs (`docs/server-auth-setup.md`), not just exist as code. Acceptance: a documented end-to-end login → security-trimmed query works on a fresh `server` install.

---

## Wave 4 — CIQS ≥ 95 for real (ongoing)

- **Eval infrastructure gap:** the harness must run on a realistic corpus at scale, in CI, weekly (the `ciqs-weekly.timer`/`.service` already exist — point them at goldset_v2 and the new metrics). Track MRR@10/nDCG@5 trend over time, alert on regression.
- **Corpus realism gap:** n=8 per-query micro-corpora are the core problem. Real CIQS must run against session-derived corpora with distractors and graded relevance (Wave 2.1). A saturated goldset can never show 95 vs 85.
- **Hypothesis ranking (most-likely-to-move-the-needle first):** (1) graph_proximity + historical_usefulness in cognitive scoring (real signal, currently dead); (2) LLM reranker backend (`reranker_backend=haiku|gemma`); (3) fusion gates; (4) embedding-based recall vs BM25-only. Test each as an isolated ablation against goldset_v2.
- **Measurement harness target shape:** deterministic, profile-parameterised, graded-relevance, ≥200 queries, three metrics, paired significance testing, committed artifact per run, CI-gated regression threshold. Only a number produced by this harness may appear in the README.

---

## BACKLOG.md additions

New epic `E-67` and stories `S-219+`, tasks `T-760+`. Waves 0–2 written to the separate additions file for PM review before merge. Waves 3–4 tracked here as headline stories to schedule later.

- **E-67: Claims-Reality Rectification** (new epic, `[active]`) — everything above.
- Wave 3 → `S-228…` (cognitive-input producers, gate-by-default decision, fabric no-Redis backend, consolidator graduation, server-profile auth).
- Wave 4 → `S-233…` (CI eval harness on goldset_v2, corpus realism, ablation ranking).

Full Wave 0–2 story/task text is in `rectification-backlog-additions.md`.
