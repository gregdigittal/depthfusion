# DepthFusion v0.7 — Roadmap (data-dependent)

> **Status:** draft at v0.6.0a1 tag (2026-04-21). **Subject to revision** based on post-migration field data.
> **Authoring principle:** plan only what's knowable now; hold open the parts that depend on v0.6 outcomes. Revisit after the dogfood pass (S-65 T-205) completes and the GPU migration lands.
> **Owner:** DepthFusion library maintainer.

---

## 0. Status at v0.6.0a1

**Shipped in the v0.5 → v0.6 arc:**
- Complete library surface for three install modes (local / vps-cpu / vps-gpu)
- Full observability layer (4 JSONL streams, backend + capture aggregators)
- All 6 LLM capabilities with provider-agnostic backend protocol
- `FallbackChain` (opt-in; factory wiring gated on v0.6.0 stable)
- Benchmark harness + eval-set scaffolding (E-26 S-63/S-64 frameworks)
- Comprehensive docs: install UX prototype, migration runbook, dogfood runbook

**Test baseline:** ~1050 unit + integration tests; mypy 0 errors; ruff 0 errors.

**What v0.6.0 stable needs before it can cut:**
1. Factory wiring of `FallbackChain` as default on vps-gpu mode (gated env var → default-on)
2. Successful GPU VPS migration (at least one instance running `vps-gpu` for ≥ 7 days)
3. First post-migration 3-run CIQS baseline (S-66)
4. Dogfood report from at least one week of real usage (S-65 T-205)

Until these land, main is at `v0.6.0a1` and `v0.6.0` remains unreleased.

---

## 1. Knowable v0.7 scope (independent of v0.6 outcomes)

These items have no field-data dependency and can be planned now.

### 1a. Library-surface leftovers

| Story | Effort | Priority | What it does |
|---|---|---|---|
| **S-39** (E-17) | L | P3 | ChromaDB entity-collection backend for the knowledge graph — lets Tier 2 exploit vector search over entity embeddings. The store protocol is ready; this is a new implementation. |
| **S-59 AC-3** | XS | P3 | CI / pre-commit hook that runs `mypy src/depthfusion` alongside `ruff` in the gate. Currently mypy is a local discipline, not enforced. |

**Total library work locked in for v0.7: 1 L + 1 XS story.** Everything else is scenario-dependent.

### 1b. Ongoing operational work (tracked but not blocking v0.7)

| Story | What needs to happen | Blocks |
|---|---|---|
| S-63 T-201 | Execute 3-run CIQS baseline for `local` + `vps-cpu` | Nothing technical — calendar-bound |
| S-64 T-202 curation | Label 50 + 30 + 40 real examples across the three gold sets | Nothing — labelling labour |
| S-65 T-205 | Run the dogfood protocol for ≥ 7 days | Scenario 3 trigger below |
| S-66 | Post-migration 3-run CIQS on vps-gpu | Scenario 1 validation |

These complete independently of v0.7 feature dev; their outcomes feed scope decisions (§3).

---

## 2. Explicitly out of scope for v0.7

These were considered and ruled out so future revisits don't re-open the question:

### E-16 (SkillForge Integration) — **not v0.7 library work**

S-32 through S-36 describe features that SkillForge *consumes* from DepthFusion's API. DepthFusion's library side (recall, recursive_llm_call, attention-weighted retrieval) already exposes what's needed. The integration work lives in the SkillForge repository — from DepthFusion's perspective this is stability and API contract discipline, not new development.

**Action:** document the API contract v0.6 offers, publish a SkillForge-consumer migration note if any v0.6 changes were breaking, then hand off. DepthFusion v0.7 does not take E-16 stories as its own.

### Multi-tenant / multi-user features

DepthFusion is designed for a single-operator Claude Code installation. Adding multi-tenant concerns (workspace isolation beyond project-scoping, per-user API keys, ACLs) would reshape large parts of the architecture. Not on the v0.7 roadmap; would need its own design doc and a dedicated release line.

### Alternative LLM providers beyond current three (Haiku / Gemma / Null)

The backend protocol is stable and supports adding new providers (OpenAI GPT, local llama.cpp, etc.) as community contributions. v0.7 does not proactively add a fourth provider — but it accepts PRs that do, provided they ship with ≥ 15 tests and factory dispatch coverage.

### Breaking changes to JSONL stream schemas

Observability stream formats are now consumed by `backend_summary()`, `capture_summary()`, external `jq` pipelines in the dogfood runbook, and whatever operators have wired up. Schema changes require a v1.0 or deliberate schema-versioning story. v0.7 may ADD fields (backward-compat) but MUST NOT remove or rename existing ones.

---

## 3. Scenario-dependent scope (resolved post-v0.6 field data)

Three scenarios frame the v0.7 decision. The dogfood pass and GPU migration outcomes determine which path.

### Scenario A — smooth migration, clean dogfood ✅

**Signal:** GPU migration validates cleanly per §4 of `docs/runbooks/gpu-vps-migration.md`. Dogfood report surfaces no Critical or High issues. CIQS Category A delta on vps-gpu meets or exceeds the +3 target (closes S-43 AC-2).

**v0.7 shape:** polish + release hardening. Short release cycle.

Likely scope additions:
- Wire `FallbackChain` as vps-gpu default (flip `DEPTHFUSION_FALLBACK_CHAIN_ENABLED` default)
- Cut v0.6.0 stable → v0.7.0 (promoted from alpha)
- S-39 + S-59 AC-3 from §1a
- 3-5 polish stories from dogfood findings (formatted as E-27 or inline)
- Document the SkillForge API contract for handoff (§2)

**Estimated duration:** 4-6 weeks post-migration.

### Scenario B — migration surfaces real problems 🟡

**Signal:** GPU migration hits a blocker (vLLM crashes, latency blowout, memory issues, CIQS delta below target). Rollback required or only partial success.

**v0.7 shape:** GPU hardening release. Delayed `FallbackChain` default wiring.

Likely scope additions:
- Root-cause analysis of the migration blocker → dedicated stories
- Better health probes in `LocalEmbeddingBackend` / `GemmaBackend`
- Retry/backoff tuning in the HTTP client
- Model-load timeout handling + vLLM monitoring hooks
- Possibly: a `HybridBackend` that splits capabilities between local Gemma and cloud Haiku based on observed latency rather than static config
- Migration runbook updated with lessons learned

**Estimated duration:** 8-12 weeks; scope depends on severity.

### Scenario C — dogfood surfaces instrumentation gaps 🟠

**Signal:** Dogfood report identifies Critical or High findings in the observability layer: lying fields, missing signals, wrong aggregation, or silent failures.

**v0.7 shape:** observability v2 release. May include schema additions (safe) but MUST NOT rename existing fields (§2 constraint).

Likely scope additions:
- Add fields identified as "wish-we-had-it" to recall/capture streams
- Fix "lying field" cases (classification logic bugs)
- Possibly: new streams for phases currently invisible (graph traversal, recursive LLM calls)
- Aggregator enhancements: rolling windows, per-project breakdowns, anomaly detection
- S-39 deferred to v0.8 (ChromaDB entity graph is lower priority than fixing instrumentation truths)

**Estimated duration:** 6-8 weeks; tighter if fixes, broader if new streams.

---

## 4. Decision points

| When | Decision | Owner |
|---|---|---|
| Migration day + ≤ 2 weeks | Scenario A, B, or mixed? | Operator observation + CIQS run |
| Dogfood week 1 complete | Scenario C engaged? | Dogfood report findings |
| v0.7 work begins | Scope frozen | Backlog entries created for chosen scenario |
| Mid-v0.7 | Scenario shift? (rare but possible) | Progress check; replan if fundamental assumptions changed |

Until the first decision point, v0.7 scope is the §1 locked items ONLY. Speculative work on §3 items is wasted effort if the scenario turns out to be different.

---

## 5. Release cadence posture

- **v0.6.0a1** (tagged 2026-04-21) — current, no runtime changes vs v0.5.2
- **v0.6.0 stable** — gated on §0 items 1–4; tagged when all four land
- **v0.7.0 alpha** — opens when Scenario is chosen; scope frozen at the first decision point
- **v0.7.0 stable** — gated on scenario's definition-of-done plus a dogfood pass on the chosen changes
- **v0.8+** — genuinely speculative; do not plan until v0.7 ships

Targeting roughly one minor release every 4-8 weeks, with alpha gated on library-surface stability and stable gated on field validation. No artificial calendar pressure.

---

## 6. What this doc is not

- Not a commitment. Scope is provisional until decision points.
- Not a feature request form. New scope requests go through `backlog-intake.md` in the project rules.
- Not a sales pitch. The scenarios are genuinely possible; the plan accounts for them honestly.

Revisit at every decision point in §4. Significant updates to this doc should be committed with a `docs(plan): v0.7 roadmap — {what changed}` message so the revision history is traceable.
