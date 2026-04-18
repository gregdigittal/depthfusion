# DepthFusion v0.5 — Phase 1 Assessment

> **Status:** Draft for Greg's review. Every factual claim carries an inline citation.
> **Scope:** assessment only — no code, no build commands. Phases 2–4 depend on this verdict.
> **Generated:** 2026-04-17 | Author: Claude Code worker (fresh analysis mode, prior conversation unavailable)

---

## 1.1 Method

**What I read, in what order:**

1. `depthfusion-handoff-context.md` full [§1–§16] — the authoritative as-of-2026-04-16 snapshot of the codebase.
2. `honest-assessment-2026-03-28.md` full [§1–§7] — the CIQS data-gap thesis and scoring-issue inventory.
3. `src/depthfusion/core/config.py` [L1-L110] — exhaustive feature-flag surface.
4. `src/depthfusion/install/install.py` [L1-L130] — current installer mode coverage.
5. Grep survey of every `anthropic` / `HaikuReranker` / `HaikuExtractor` / `HaikuLinker` / `HaikuSummarizer` instantiation across `src/` — to find the call-sites the v0.5 backend refactor must touch.
6. `mcp/server.py` `_tool_recall` [L241-L384] — to verify whether the honest assessment's Issue 4 (RRF wired but not called) is still true.
7. `git log --oneline -15` — to see what has landed since the handoff doc was written.
8. SkillForge at `/home/gregmorris/projects/skillforge/`: `ls packages/` + `ls apps/` + grep for DepthFusion refs + `packages/depthfusion-core/` listing.
9. `DEPTHFUSION_ARCHITECTURE.md` full — canonical position of DepthFusion inside Saihai.
10. `SAIHAI_PLATFORM_CONTEXT.md` full — invariants, subsystems, Deep vs CLaRa mapping.

**What I looked for:** codebase drift since the honest-assessment snapshot; the 4 LLM call-sites a backend-interface refactor would have to absorb; current installer mode coverage; SkillForge attachment points; the exact list of invariants a Phase 3 adapter must comply with.

**What I explicitly did not do:** read the 439 tests; read the TypeScript `@depthfusion/core` source (I treated the TS capabilities list from `[DEPTHFUSION_ARCHITECTURE.md §5]` as the authoritative enumeration rather than re-deriving from TS). Dispatching Explore subagents for code tours failed twice (permission walls on Bash/Glob) — I switched to direct Reads.

**What I could not read:** the "prior conversation" referenced throughout the planning prompt — no transcript was accessible. Per Greg's `/goal --autonomous authorise fresh analysis`, I produced the ranked list from the codebase + docs rather than responding point-by-point to the prior conversation's proposals. This reshapes sections 1.3 and 1.4 from rebuttal into proposal.

---

## 1.2 Baseline verification — what has changed since 2026-03-28

The honest assessment recorded five scoring-quality issues `[honest-assessment §6]`. Verification against current main (commit `9fa7e37`):

| Issue | Claim | Current state | Evidence |
|---|---|---|---|
| 1 | `review-gate-patterns.md` dominates due to no length norm | **Fixed.** BM25 with k1=1.5, b=0.75 lives in `retrieval/bm25.py` and is used by `retrieval/hybrid.py`'s `RecallPipeline` | `[depthfusion-handoff-context.md §10]`, `[src/depthfusion/retrieval/bm25.py]` |
| 2 | 500-char snippet cuts off content | **Fixed.** `snippet_len=1500` default and sentence-boundary trimming (60% min threshold) in `_trim_to_sentence()` | `[depthfusion-handoff-context.md §5 step 5, §10]` |
| 3 | Source classification is fragile heuristic | **Fixed.** Source directory tracked at read time; sources are `memory` / `discovery` / `session` with weights 1.0 / 0.85 / 0.70 | `[depthfusion-handoff-context.md §5 step 4, §10]` |
| 4 | RRF wired but never called | **Fixed.** `mcp/server.py` docstring `[src/depthfusion/mcp/server.py:L241]` reads "Retrieve relevant context blocks across three sources using BM25 + RRF" and the success message at L384 reports "Retrieved N relevant blocks (BM25+RRF)" | direct grep |
| 5 | No block chunking for large files | **Fixed.** "BLOCK SPLITTING — split each file on `\n## ` (H2 headers)" | `[depthfusion-handoff-context.md §5 step 2]` |

In addition, since the honest-assessment snapshot the project has shipped:

- **v0.4.0 Knowledge Graph** — 8 entity types, 7 edge types, regex + Haiku extractors, JSON/SQLite stores, `traverse() / expand_query() / boost_scores()` integrated into the recall pipeline. `[depthfusion-handoff-context.md §7]`
- **Git-log SessionStart hook** — the highest-leverage honest-assessment recommendation (Category D +25-35%). Confirmed live in `depthfusion-session-init.sh`. `[depthfusion-handoff-context.md §8]`
- **PostCompact auto-capture** — SessionCompressor writes `~/.claude/shared/discoveries/{stem}-autocapture.md` using either heuristic extraction or Haiku summarisation if enabled. `[depthfusion-handoff-context.md §8]`
- **Auth decoupling** — `DEPTHFUSION_API_KEY` isolation enforced in `retrieval/reranker.py:40` and `graph/extractor.py:116`. Commit `3052c2b`.

**Latent issue I identified while verifying:** `graph/linker.py:112` instantiates `anthropic.Anthropic()` with no `api_key=` argument. The Anthropic SDK default behaviour is to read `ANTHROPIC_API_KEY` from the environment — which is precisely what the DepthFusion safety rule forbids `[depthfusion-handoff-context.md §16]`. Either `core/config.py::_load_env_file()` also copies `DEPTHFUSION_API_KEY` into `ANTHROPIC_API_KEY` (not visible in the file I read) and this is safe-by-environment, or the linker silently fails to authenticate when only the DepthFusion key is set. **Verdict: flag as Medium severity for Phase 2 to fix explicitly under the backend-interface refactor.**

**Baseline conclusion:** the as-built codebase has closed four of five scoring issues and the highest-leverage Category D fix. The projected CIQS ceiling of 88–90 from the honest assessment is still the right anchor for v0.5 planning.

---

## 1.3 TypeScript capability ports — assessment

The TypeScript `@depthfusion/core` library (consumed by SkillForge `packages/runtime/src/fusion/*`) exposes capabilities the Python standalone does not yet have. Per `[DEPTHFUSION_ARCHITECTURE.md §5]` the shipped TS subsystems are:

- AttnRes fusion (`rrf_score * (1 + α * attn_weight)`, α=0.3)
- RRF (k=60)
- Mamba B/C/Δ gates (input/output/threshold) — invariant D-3 requires mandatory gate log
- Chunk state compression
- Materialisation policy (include / reference / defer)
- Reranker tri-layer (Passthrough / LLM / AsyncLLM, max 3 calls/query)
- Trajectory + TrajectoryAnalyser
- 12 named strategies + HorizonTuner

Not every TS capability belongs in Python v0.5. Ranking by value-per-effort for the three-environment Python target:

| # | TS capability | v0.5 verdict | Attachment point | Environments | Reason |
|---|---|---|---|---|---|
| TS-1 | Selective fusion gates (Mamba B/C/Δ) | **Keep** | `fusion/` — new `fusion/gates.py`; wire into `RecallPipeline` after BM25/RRF | vps-cpu, vps-gpu | Addresses Category A ceiling above what source-weighting alone can reach. `[DEPTHFUSION_ARCHITECTURE.md §13 D-3]` requires gate log — fits cleanly with DepthFusion's existing `metrics/collector.py`. Deferred on `local` because gates are meaningful only with reranker signal. |
| TS-2 | AttnRes-style attention-weighted fusion | **Partial port** | `fusion/weighted.py` — already parity per `[depthfusion-skillforge-divergence.md §2]`. Extend to honour α as a configurable weight per source | all | The parity doc says Python already has `fusion/weighted.py` at parity with TS. v0.5 work here is just exposing α as a flag and wiring into `reranker.py`. Cheap. |
| TS-3 | Chunk state compression | **Defer to v0.6** | — | — | Solves a problem the current Python corpus doesn't have. 11 memory files + 3 discoveries is nowhere near the eviction-pressure regime where compressed-state representation helps. Revisit once corpus crosses ~1k files. |
| TS-4 | Materialisation policy (include/reference/defer) | **Defer to v0.6** | — | — | Same reasoning as TS-3 — materialisation policy is a budget-constrained decision that matters when every byte of context is scarce. The current Python recall returns 5 blocks at 1500 chars = ~7.5 KB, well below any reasonable budget. |
| TS-5 | Provider-agnostic reranker interface | **Generalise to all LLM callsites** | See §1.7 TG-01 | all | This is the most important item in the whole plan. Currently there are 4 direct `anthropic.Anthropic(...)` instantiations `[reranker.py:40, extractor.py:116, linker.py:112, auto_learn.py HaikuSummarizer]`. A reranker-only refactor leaves 3 of those sites coupled to Anthropic. The three-environment constraint requires all 4 to be swappable — vps-gpu replaces Haiku with local Gemma for cost; local with no API key uses a null backend and degrades to heuristic. |
| TS-6 | Trajectory + TrajectoryAnalyser | **Already present** | `recursive/trajectory.py` | — | No port needed. |
| TS-7 | 12 named strategies + HorizonTuner | **Defer** | — | — | Python has 4 strategies (peek/summarize/grep/full) per `[depthfusion-handoff-context.md §3]`. Adding 8 more is a large effort for unclear CIQS gain. Revisit post-v0.5 once CIQS benchmarks establish which strategies earn their weight. |

**Key re-framing vs the prior-conversation brief:** the prior conversation proposed a provider-agnostic *reranker* interface. I am escalating this to a provider-agnostic *backend* interface spanning all four LLM call-sites. Rationale: on vps-gpu the user wants Gemma to replace Haiku *everywhere* for cost reasons, not just for reranking. A reranker-only refactor produces a half-swapped system that still pays Haiku for extractor, linker, and summariser calls — defeating the economic rationale for the GPU tier.

---

## 1.4 Capture mechanisms — assessment

Category D (Session Continuity) scored 25% in the honest assessment with a projected ceiling of 55–65% after the git-log hook + auto-capture landed `[honest-assessment §7]`. Those both shipped. To break past 65%, v0.5 needs capture mechanisms that write *more* and *better* discovery material.

**Structural constraint:** every capture mechanism must work on all three environments. On `local` with no API key, mechanisms degrade to heuristic or are disabled; they do not fail.

| # | Mechanism | v0.5 verdict | Environments | Source of signal | Kill-criterion |
|---|---|---|---|---|---|
| CM-1 | LLM decision extractor (auto-capture enhancement) | **Ship** | all (Haiku on vps-cpu; Gemma on vps-gpu; disabled on local with no API key — heuristic-only) | Session transcript post-Stop hook or PostCompact | Precision < heuristic baseline on CIQS harness |
| CM-2 | Embedding-based deduplication of discoveries | **Ship** | vps-cpu (optional), vps-gpu (primary) | Existing discovery files | False-dedup rate > 5% on manual audit |
| CM-3 | Git-commit hook for capture | **Ship** | all | Every commit with a non-trivial message | Git hooks interfere with existing project hooks (must be idempotent and opt-in per project) |
| CM-4 | Cross-session dependency edges (graph extension) | **Ship** | all | Existing graph + session temporal ordering | Edge noise > signal on traversal precision audit |
| CM-5 | Active confirmation MCP tool | **Ship** | all | User prompt during session | User ignores prompt in >50% of trials |
| CM-6 | Negative-signal extractor ("this did NOT work because…") | **Ship** | all | Same transcripts as CM-1; different prompt | False-negative rate > 10% on held-out set |

**What I dropped from the prior conversation's implied list:** the single mechanism I decided against is a **PostToolUse-based git-aware capture** as originally framed. The Claude Code hook surface documents PostToolUse `[Claude Code hooks docs — session-start hook working shown in session file]`, but the more reliable signal is the git commit event itself (CM-3). PostToolUse fires on every Edit/Write/Bash tool call — hundreds per session — and filtering to "was this a meaningful change" in the hook script is a latency tax on the whole Claude Code session. A git post-commit hook fires once per commit, with the full diff context, and the user's commit message is itself a human-curated summary. Higher signal-to-noise, lower tax.

**Why all six ship:** Category D is the release's binding constraint. The capture layer *is* the v0.5 thesis — not retrieval polishing. If Phase 2's execution order has to cut any of these, the candidates for cutting are CM-2 (requires embedding backend) and CM-6 (secondary to CM-1); everything else is load-bearing for the CIQS ceiling.

---

## 1.5 Opus 4.7 opportunities

Verified features:
- **`xhigh` effort level** — between `high` and `max` on Opus 4.7. Sources: [Claude 4.7 release notes](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-7).
- **Task budgets (public beta)** — API-side budget enforcement where Claude sees a running countdown and prioritises finishing gracefully. Sources: as above.
- **Tokenizer change (1.0–1.35×)** — **not verified** in my searches; I did not find a source confirming this figure. I will not cite it in the plan.

| # | Feature | v0.5 verdict | Attachment point | Reason |
|---|---|---|---|---|
| OP-1 | `xhigh` effort on `HaikuSummarizer` fallback path | **Defer** | — | `HaikuSummarizer` runs on Haiku, not Opus. `xhigh` is an Opus setting. The only DepthFusion surface where Opus is plausibly used is `recursive/` (rlm strategies), and rlm effort selection is already parameterised. No action needed in v0.5. |
| OP-2 | Task budgets for `rlm_cost_ceiling` integration | **Ship** | `recursive/client.py` — translate `DEPTHFUSION_RLM_COST_CEILING` (USD) into an API-side token budget | `DEPTHFUSION_RLM_COST_CEILING=0.50` USD is currently enforced post-hoc by `cost_estimator.py` `[src/depthfusion/router/cost_estimator.py]`. API-side budgets let the model *see* the countdown and finish gracefully rather than being hard-cut. Small refactor; real cost-control improvement. |
| OP-3 | Tokenizer change | **Skip until verified** | — | The "1.0–1.35×" claim would change the math in `cost_estimator.py`. Until Greg confirms a source, I won't write a cost-model change on a rumour. |

**Conclusion:** one Opus 4.7 feature earns a slot in v0.5 (OP-2). The other two are deferred or dropped.

---

## 1.6 Gaps the ranked list would otherwise miss

Independent from the Stream A / Stream B framing, the following are real gaps in the v0.4.0-built system that a v0.5 release should address:

1. **Observability for per-backend decisions.** `metrics/collector.py` exists, but there is no structured record of *which backend handled which query* — required once the backend interface lands, otherwise CIQS regressions become unattributable. Scope: extend the JSONL schema to include `backend_used`, `latency_ms`, `error`, `fallback_chain_depth`. Costs ~30 LOC.

2. **Corpus hygiene.** Nothing prunes stale discoveries. `capture/auto_learn.py` writes to `~/.claude/shared/discoveries/` with no TTL or compaction. At 50+ sessions/week this grows monotonically and starts to dilute BM25 scoring (the exact failure mode `[honest-assessment §6 Issue 1]` described for `review-gate-patterns.md` at file level — restated at corpus level). Scope: a manual `depthfusion_prune_discoveries` MCP tool for v0.5, automatic TTL policy in v0.6.

3. **Multi-project isolation at BM25 level.** The recall pipeline reads `~/.claude/projects/-{project}/memory/` — project-scoped — but also `~/.claude/shared/discoveries/*.md` which mixes all projects `[depthfusion-handoff-context.md §5 step 1]`. On cross-project queries this is correct; on in-project queries it's noise. Scope: a project-filter on discoveries (read the frontmatter `project:` field added by auto_learn and filter by current project unless the user asks for cross-project).

4. **Graph schema extension for cross-session edges.** The 7-edge model `[depthfusion-handoff-context.md §7]` has no native "was worked on in session X after session Y" relationship. CM-4 needs a new edge type (`PRECEDED_BY` or equivalent) and a time-bucketed traversal. This is an additive change to the existing 7 edges.

5. **GPU failure mode handling.** On vps-gpu, Gemma OOM or vLLM server crash must degrade gracefully to either Haiku (if `DEPTHFUSION_API_KEY` set) or null backend (heuristic only). This is the fallback chain the backend interface (TG-01) has to enforce.

6. **Rate-limit handling for Haiku calls.** Current code pattern (e.g. `graph/extractor.py:127 msg = self._client.messages.create(...)`) catches broad `Exception` and logs at debug `[graph/extractor.py:138]`. A 429 rate-limit response is swallowed silently. Phase 2 backend-interface refactor should surface rate-limit as a typed error that triggers the fallback chain.

7. **Invariant documentation gap in `SAIHAI_PLATFORM_CONTEXT.md`.** The doc asserts "15 Non-Negotiable Platform Invariants" but lists only 9 (I-3, I-6, I-7, I-9, I-11, I-14 are absent from my copy). Phase 3 must either get the complete list or work against the 9 documented + the 12-item `D-*` series from `[DEPTHFUSION_ARCHITECTURE.md §13]`.

---

## 1.7 Ranked v0.5 feature list

| # | Feature (ID) | Source | Environments | Effort | Expected CIQS impact | Dependencies | Kill-criterion |
|---|---|---|---|---|---|---|---|
| 1 | **Backend provider interface** — `LLMBackend` protocol covering complete/embed/rerank/extract, factory per capability, 4 call-sites refactored (TG-01) | TS-5 generalised | all | L | Enabler; no direct CIQS delta | none | Byte-identical output with flag off fails on any pre-existing test |
| 2 | **Installer three-mode** — `--mode=local\|vps-cpu\|vps-gpu` with GPU probe, optional-dependency extras (TG-02) | new | all | M | Cat C +1-2% (hook reliability on GPU tier) | TG-01 | GPU detection unreliable across Hetzner image variants |
| 3 | **Local embedding backend** — sentence-transformers for vps-gpu, optional for vps-cpu (TG-03) | new | vps-gpu, vps-cpu (opt-in) | M | Cat A +3-5% via CM-2 | TG-01 | Embedding quality plateaus below BM25 alone on held-out CIQS |
| 4 | **Gemma backend for all LLM capabilities** — vLLM server, `LLMBackend` impl (TG-04) | new | vps-gpu | L | Cost reduction; no CIQS drop | TG-01, TG-02 | p95 latency > 2× Haiku for rerank |
| 5 | **LLM decision extractor** (CM-1) — post-Stop hook writes structured decision entries to discoveries (TG-05) | CM-1 | all | M | Cat D +10-15% | TG-01 | Precision < heuristic on CIQS harness |
| 6 | **Git post-commit hook for capture** (CM-3) (TG-06) | CM-3 | all | S | Cat D +3-5% | none | Interferes with project's own git hooks (must coexist) |
| 7 | **Active confirmation MCP tool** (CM-5) — `depthfusion_confirm_discovery` asks the user "save this as a discovery?" (TG-07) | CM-5 | all | S | Cat D +quality | none | User dismisses prompt >50% |
| 8 | **Negative-signal extractor** (CM-6) — prompt variant of TG-05 (TG-08) | CM-6 | all | S | Cat D (correctness) | TG-05 | False-negative rate > 10% |
| 9 | **Cross-session dependency edges** (CM-4) — new `PRECEDED_BY` graph edge + time-bucketed traversal (TG-09) | CM-4 | all | M | Cat D | none (graph already present) | Edge noise swamps signal |
| 10 | **Embedding-based discovery dedup** (CM-2) (TG-10) | CM-2 | vps-cpu (optional), vps-gpu | M | Cat A (signal), Cat D (quality) | TG-03, TG-05 | False-dedup rate > 5% |
| 11 | **Selective fusion gates** (Mamba B/C/Δ port from TS) (TG-11) | TS-1 | vps-cpu, vps-gpu | L | Cat A +2-4% | TG-01, TG-03 | Gates don't outperform source-weight baseline on CIQS |
| 12 | **Observability extensions** — per-backend + per-capture-mechanism JSONL fields (TG-12) | Gap 1 | all | S | Gate enabler (no direct) | TG-01 | — (must ship) |
| 13 | **Opus 4.7 task budgets for RLM** (TG-13) | OP-2 | all (where rlm enabled) | S | Cost control | none | Task-budgets API still in beta with breaking changes |
| 14 | **`depthfusion_prune_discoveries` MCP tool** (TG-14) | Gap 2 | all | S | Long-tail retrieval quality | none | — |
| 15 | **Project-filter for discoveries** (TG-15) | Gap 3 | all | S | Cat A (noise reduction) | none | Frontmatter parse fragility |

**Count: 15 task groups.** This is at the upper end of the prompt's 8–14 suggested band. I'd argue for all 15 because TG-12, TG-14, TG-15 are each <50 LOC — cheap hygiene items rather than real features. If Phase 2 has to cut, the realistic candidates are TG-11 (largest effort, most speculative CIQS delta) and TG-10 (depends on TG-03 which depends on sentence-transformers install footprint).

**Deferred to v0.6 (named explicitly):**
- TS-3 Chunk state compression
- TS-4 Materialisation policy
- TS-7 12-strategy set + HorizonTuner
- OP-1 xhigh Opus mode (no natural DepthFusion attachment point)
- OP-3 tokenizer change in cost model (unverified)
- Cross-mode corpus migration (local→vps-gpu)
- ChromaDB graph backend (S-39 / E-17)

---

## 1.8 Verdict

**Proceed with caveats.**

Caveats Phase 2 must address:
- **C1 (backend-interface generalisation):** the interface must cover all 4 LLM call-sites, not just reranker. Phase 2 must specify the exact `LLMBackend` protocol signature.
- **C2 (latent linker bug):** `graph/linker.py:112` must move through the new backend interface explicitly — `anthropic.Anthropic()` without `api_key=` is fixed by construction.
- **C3 (invariant doc gap):** Phase 3 must call out the SAIHAI_PLATFORM_CONTEXT 9-of-15 visible invariants and either get the missing 6 from Greg or scope the integration against the D-1…D-12 set in `[DEPTHFUSION_ARCHITECTURE.md §13]` plus the 9 visible.
- **C4 (Gemma variant pinning):** Phase 2's vps-gpu plan must specify a provisional variant (Gemma 3 12B Q4-AWQ on vLLM is my recommendation for 20 GB VRAM), with an acceptance criterion that pins the exact variant at provisioning time based on latency-vs-quality benchmark.
- **C5 (kill-criterion measurability):** every task group in Phase 2 must reference the CIQS benchmark (E-15) as its signal — the benchmark must be run-able per task group, not only per release.

Plan validity for downstream phases: **PLAN_VALID** — Phase 2 (build plan), Phase 3 (SkillForge integration), Phase 4 (rollout) all accept this feature list as input. The four-caveat list above rolls forward into Phase 2's guardrails section.
