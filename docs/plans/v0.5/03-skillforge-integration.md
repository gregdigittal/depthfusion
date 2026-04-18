# DepthFusion v0.5 — Phase 3 SkillForge Integration Spec

> **Status:** Draft for Greg's review. DepthFusion source never imports SkillForge.
> **Scope:** integration seams for the Python standalone DepthFusion into SkillForge. The TypeScript `@depthfusion/core` library is already integrated (see §3.1) — this document concerns the second adapter.
> **Depends on:** `01-assessment.md` (feature list) + `02-build-plan.md` (v0.5 surface)
> **Generated:** 2026-04-17

---

## 3.1 Integration model — the two-adapter reality

The planning prompt referenced a single adapter. Source inspection shows the reality is two adapters, because there are two DepthFusion implementations per `[DEPTHFUSION_ARCHITECTURE.md §2]`:

| Adapter | Status | What it bridges | Lives in |
|---|---|---|---|
| **Adapter A** — `SkillForgeDepthFusionAdapter` | **Already shipped** | TS `@depthfusion/core` library → SF runtime fusion / context layer | `packages/runtime/src/fusion/adapter.ts` (tested at `adapter.test.ts:L18`) |
| **Adapter B** — `skillforge-depthfusion-mcp-adapter` | **New in v0.5** (this document) | Python standalone DepthFusion (MCP server + 15 tools + persistent corpus + auto-capture + knowledge graph) → SF runtime | New package: `packages/skillforge-depthfusion-mcp-adapter/` |

**Why two adapters, not one:** per `[DEPTHFUSION_ARCHITECTURE.md §2]`, the TS library is stateless algorithm primitives (RRF, Mamba gates, AttnRes) and the Python standalone is the persistent-memory + hooks + graph surface. SF runtime already consumes the algorithms directly via `@depthfusion/core/scoring` and `@depthfusion/core` type imports `[packages/runtime/src/fusion/scoring.ts:L7, types.ts:L17]`. What SF runtime lacks is a way to *query the Python corpus* (memory files, discoveries, graph) through DF's MCP server. Adapter B fills this gap.

### The rule

**Python DepthFusion source imports nothing from SkillForge.** `pip install depthfusion` works standalone; `pnpm install` of the SF monorepo pulls Adapter B as a TypeScript package that calls DepthFusion's MCP server over stdio JSON-RPC.

If the `depthfusion` Python package is not installed on the SF host, Adapter B's `healthCheck()` returns `unavailable` and any SF skill step that targets DepthFusion degrades per the failure-mode protocol in §3.7.

### Why structured integration with deep seams, not thin adapter

A thin adapter (raw JSON-RPC passthrough) cannot enforce SF's governance invariants (I-4 StepExecutorRegistry, I-10 ACS floors, I-8 gate decision logging). A full deep integration (DepthFusion as an SF subsystem) would require DF to import SF — violating D-9 `[DEPTHFUSION_ARCHITECTURE.md §13]` and making standalone install impossible. Structured integration with deep seams = Adapter B owns the governance translation in one direction: SF governance calls into DF; DF never reaches back.

---

## 3.2 Adapter B surface

### Package location

`/home/gregmorris/projects/skillforge/packages/skillforge-depthfusion-mcp-adapter/`

Peer to `packages/depthfusion-core/`, not nested under it. Reasoning: `depthfusion-core` is a pure library (D-2 stateless); the MCP adapter holds transport state (subprocess handle, session ID, connection pool) and is not pure.

### Language: TypeScript

Per `[DEPTHFUSION_ARCHITECTURE.md §2]` SF packages are TypeScript (pnpm workspace + turbo). A TypeScript adapter calling Python over JSON-RPC via stdio keeps the package-manager story clean — Python is an install-time dependency declared in the adapter's `package.json` postinstall script (or documented as a user prerequisite).

### Public API

```typescript
// packages/skillforge-depthfusion-mcp-adapter/src/index.ts
// SIGNATURE — not implementation

export interface DepthFusionMcpClient {
  recall(query: string, options: RecallOptions): Promise<RecallResult>;
  publishContext(item: ContextItem): Promise<void>;
  runRecursive(strategy: RecursiveStrategy, args: RecursiveArgs): Promise<RecursiveResult>;
  graphTraverse(entity: string, depth: number): Promise<TraversalResult>;
  getTierStatus(): Promise<TierStatus>;
  healthCheck(): Promise<HealthReport>;
}

export class McpAdapterRecallStepExecutor implements StepExecutor {
  type = 'depthfusion.recall';
  async execute(step: SkillStep, ctx: ExecutionContext): Promise<StepResult> { ... }
}

// Adapter B is the first concrete implementation of the McpAdapter pattern
// specified in SAIHAI_OPENCODE_GAP_CLOSURE_PLAN.md §1.1 Gap 3 (MCP Client path).
// It is NOT a CapabilityProvider / fusion-strategy plugin — that slot is
// already filled by Adapter A (SkillForgeDepthFusionAdapter in runtime/fusion/adapter.ts
// via DEFAULT_FUSION_STRATEGY in runtime/router/index.ts:L24).
// Adapter B attaches at the adapter-resolver path, not the fusion-strategy path.
//
// SECURITY-PARITY CONTRACT (per DR-018 §4 ratification of legacy #4 as I-17):
// Adapter B's backend fallback chain preserves TLS, API-key handling, and
// audit-record uniformity across all backend selections (Haiku / Gemma / Null).
// Invocation context is indistinguishable at the ACS layer regardless of which
// backend handled the call — i.e. the security properties of a Gemma-routed
// rerank match those of a Haiku-routed rerank, and both emit uniform audit
// records. This is I-17 compliance for the Adapter B surface.
export class DepthFusionMcpAdapter implements McpAdapter {
  readonly transport = 'mcp-stdio';
  supports(capability: Capability): boolean { ... }
  healthCheck(): Promise<HealthReport> { ... }
  // Adapter-interface capability methods (I-9) mapped to MCP tool calls:
  recall(...): Promise<...>;
  graphTraverse(...): Promise<...>;
  // ...
}

export function registerDepthFusionExecutors(
  registry: StepExecutorRegistry,
  adapterResolver: AdapterResolver,
  acs: AccessControlService,
  invocationLog: InvocationLog
): void { ... }
```

### Bridged surfaces

| SF surface | DF contribution | Bridge mechanism | File reference |
|---|---|---|---|
| StepExecutorRegistry (I-4) | Recall + graph traversal as step types | `registerExecutor('depthfusion.recall', ...)` etc. | `packages/runtime/src/executor/` |
| ACS (I-10) | DF recall result honours `min_quality_score` from the enclosing skill | Adapter B wraps recall response with ACS-compatible `QualityReport` | `packages/acs/src/floor-enforcer.ts`, `packages/runtime/src/plugins/plugin-host.ts:L67-L70` |
| Adapter Resolver (I-9) | DF offers recall / graph / recursive / publish capabilities via MCP transport | Adapter B is the first `McpAdapter` — the generic transport sketched in `SAIHAI_OPENCODE_GAP_CLOSURE_PLAN.md §1.1 Gap 3 MCP Client` | No existing file yet — Adapter B creates the pattern |
| Capability Router | Adapter B does **not** plug here directly — Adapter A (`SkillForgeDepthFusionAdapter` in `packages/runtime/src/fusion/adapter.ts`) already occupies the fusion-strategy slot at `runtime/router/index.ts:L24, L108` | N/A for Adapter B — separate concern | `packages/runtime/src/router/index.ts:L68 (CapabilityRouter class), L71 (route method)` |
| InvocationLog (I-11) | Every DF call logged with hash chain | Adapter B writes one `DepthFusionCall` record per recall; hash chain unbroken | `packages/db/prisma/schema.prisma:L237, L1059`, `packages/review/src/acs-integration.ts:L66` |
| Context Bus (SF runtime) | DF graph entities published as context items | Adapter B subscribes to the bus and republishes relevant DF graph nodes | `packages/channels/` |
| Plugin Host (I-10 enforcement point for legacy #9) | Adapter B registers as a runtime plugin; cannot override ACS floor | Standard SF plugin contract; enforcement at plugin-host pre-execute | `packages/runtime/src/plugins/plugin-host.ts:L67-L70` |

---

## 3.3 Invariant compliance

Canonical source: `docs/research/DR-017_INVARIANTS_CANONICAL.md` (supersedes the partial list in `SAIHAI_PLATFORM_CONTEXT.md`). This section complies with DR-017 §4 (pre-flight checks) and §6 (per-row scope classification).

### 3.3.1 DR-017 §4 pre-flight results

**§4.1 textual consistency (DR-017 vs SAIHAI_PLATFORM_CONTEXT.md):** 9-invariant SAIHAI subset (I-1, I-2, I-4, I-5, I-8, I-10, I-12, I-13, I-15) is textually consistent with DR-017 §2. DR-017 is uniformly more scope-specific. No contradictions.

**§4.2 legacy-numbering audit** (grep of `Invariant #\d+` across SF repo): 11 legacy numbers appear in production code. Classifications: #1→I-1 (a); #2→I-2 (a); #3→(b) §6 limbo; #4→(b) §6 limbo; #5→(b) §6 limbo; #6→(b) §6 limbo; #7→(b) §6 limbo; #8→I-3 (a); #9→I-10+I-2 (a, partial-mapping caveat per DR-017 §5); #10→I-2 (a, subsumed); #11→I-11 (a). Full table (including test-case depth per invariant) is in **DR-018 §2**; consult that for authoritative enforcement characterisation. **Substantive §6 finding:** the 5 limbo legacy invariants each have concrete automated enforcement — #3 has 3 test cases in `gsci.test.ts:L89-L124`; #4 has 3 test cases in `scheduler.test.ts:L98,L107,L173` + schema constraints in `schema.prisma:L561,L583`; #5 has a 4-test-case describe-block in `opl/__tests__/policy-store.test.ts:L61` + runtime enforcement at `runtime/src/router/index.ts:L74`; #6 is schema-enforced at `schema.prisma:L439`; #7 is schema-enforced at `schema.prisma:L372`. DR-017 §6 interpretation (a) "deliberately de-escalated to operational" is materially weaker than it reads given this enforcement depth. Surfaced in DR-018 §4 for Greg's per-legacy-invariant decision.

**§4.3 firewall self-consistency:** all 15 invariants pass the I-1 firewall check. I-7 references model identities only within the Quality Validator (boundary-by-design), not a violation.

**§4.4 enforcement inventory:** best-effort; full table in planning-conversation transcript. Summary — I-2/I-3/I-5/I-10/I-11/I-15 are runtime-enforced; I-7/I-9/I-12 are test-enforced; I-1/I-4/I-6/I-14 are convention-enforced (candidates for automated checks).

**§4.5 contradictions:** none between DR-017 and this plan document. DR-017 resolves the prior NEED FROM GREG placeholders without introducing conflicts.

### 3.3.2 The 15-invariant compliance table (with inline per-row §6 scope classification)

Per DR-017 §6.1, each row below carries: (1) the invariant's canonical text summary, (2) Adapter B's compliance verdict, (3) inline §6-dependence classification naming the specific legacy invariant(s) in limbo (or "none" if independent), (4) a one-line note on how the row would change under §6 interpretations (b) / (c) where relevant.

| # | Invariant (DR-017 §2 summary) | Adapter B verdict | §6 status | Inline §6 analysis |
|---|---|---|---|---|
| **I-1** | Skill IR firewall — nothing above Layer 2 knows models; nothing below knows org structure | ✅ Complies | **Complete** | §6 independent. None of legacy #3 (GSCI write-safety), #4 (channel parity), #5 (OPL fallback target), #6 (SLA→DENY), or #7 (config immutability) bears on information-flow direction. Adapter B translates DF results without model identifiers — that claim is stable under all three §6 interpretations. |
| **I-2** | Tier 1 quality floors non-negotiable; FloorViolationError before execution; RL / OPL / plugins cannot downgrade; fallback models independently meet floor | ✅ Complies | **Complete** | §6 dependence on legacy #5 (OPL→MAX_QUALITY) is already internalised by I-2's explicit "RL routing, OPL optimisation, and plugin hooks cannot downgrade" clause. Under (a) the claim stands; under (b) #5 is a specific case of I-2's pre-existing language (not a scope change); under (c) a new I-N for #5 reinforces but does not change I-2. Adapter B's FloorViolationError pre-execution is verdict-stable. |
| **I-3** | Inspectable compiler output — every invocation logs Skill IR YAML, compiled payload, raw response, validation result, final output; `--dry-run` mandatory | ✅ Complies | **Complete** | §6 independent. I-3 captures audit artefacts **by-value** — the literal Skill IR YAML text, the literal compiled payload, the literal response — not by reference to the config active at invocation time. Legacy #7 (immutable config snapshots) governs how audit entries *reference* config (see I-11 and I-8); it does not bear on by-value content captures. Adapter B emits request + response + error + fallback-chain per TG-12 schema — verdict-stable across §6 interpretations for all three directions (a)/(b)/(c). |
| **I-4** | StepExecutorRegistry — extensible string discriminators, never closed enums | ✅ Complies | **Complete** | §6 independent. None of the 5 limbo items relates to registry extensibility. `type = 'depthfusion.recall'` is a namespaced string regardless of §6. |
| **I-5** | System prompt floor ≥ 15%; CLaRa `AdaptiveTokenAllocator` enforces | ✅ N/A for Adapter B directly | **Complete** | §6 independent. Adapter B does not allocate budget; CLaRa owns this. None of #3–#7 touches token-budget arithmetic. |
| **I-6** | Taint label propagation through execution graph; no silent taint loss across compression / summarisation / fusion / materialisation | ✅ Complies (post-TG-12 metrics schema extension) | **Complete** | §6 independent. Taint is a data-flow annotation; it doesn't turn on GSCI-confirmation semantics (#3), channel parity (#4), OPL fallback target (#5), SLA→DENY semantics (#6), or config immutability (#7). Adapter B carries taint from DF chunk metadata through to the SF step result — verdict-stable. |
| **I-7** | Zero-degradation portability — skill outputs on Model A benchmarkable against Model B via Quality Validator | ✅ Complies | **Complete** | Re-examined during §6.1 self-audit: I-7 is about benchmark harness capability, not about runtime fallback choices. Legacy #5 (OPL→MAX_QUALITY) governs the runtime fallback target; the benchmark harness runs offline with explicit model pinning and doesn't depend on OPL availability. Under (a)/(b)/(c) the portability claim is verdict-stable. |
| **I-8** | Gate decisions logged — all CLaRa/Deep gate decisions produce audit entries (B/C/Δ / materialisation / budget allocation) | ✅ Complies (post-TG-11) | **Provisional (Ratified)** | **Depends on legacy #7 (immutable config snapshots), mirroring I-11.** Classification flipped from Complete to Provisional in Option-B review round 1c (Claude reviewer High 2 accepted). **Ratified 2026-04-18 via DR-018 §4: legacy #7 absorbed into amended I-11, absorption extends to I-8 per DR-018 §3.5 scope note.** Outcome applied: gate-log records carry a `config_version_id` field (parallel to the I-11 InvocationLog record extension). See §3.3.5 action 2 (now unconditional). |
| **I-9** | Adapter interface standardised — a `FinanceAdapter` works identically whether backed by Saihai Finance or Xero; capability fallback to built-in when external lacks | ✅ Complies | **Provisional (Ratified)** | **Depends on legacy #4 (channel security parity).** **Ratified 2026-04-18 via DR-018 §4: legacy #4 reinstated as I-17 (ACS enforcement is invocation-context-invariant).** Outcome applied: Adapter B now pairs I-9 capability-parity compliance with explicit I-17 compliance. An explicit "Adapter B's backend fallback chain preserves TLS, API-key handling, and audit-record uniformity across all backend selections" assertion is now part of the Adapter B contract in §3.2 (security-parity clause added). See §3.3.5 action 3 (now unconditional). |
| **I-10** | ACS quality floor as hard backstop — GEPA mutations and Deep config changes below `min_quality_score` automatically rejected | ✅ Complies | **Provisional (Ratified)** | **Depends on legacy #5 (OPL unavailable → MAX_QUALITY default).** **Ratified 2026-04-18 via DR-018 §4: legacy #5 reinstated as I-18 (unavailable-optimiser default is highest-quality eligible).** Outcome applied: Adapter B's backend fallback chain for rerank/extract/summarise is quality-ranked, not cost/latency-ranked. TG-01 AC-01-4 is amended (new AC-01-8 added) to require quality-descending fallback order. Cost/latency optimisation applies only within the set of backends at or above the current tier's min_quality_score. See §3.3.5 action 1 (now unconditional). |
| **I-11** | Audit logging — CLaRa / Router / ACS decisions produce audit entries; Tier 1 `InvocationLog` hash-chained **synchronously** before response returned | ✅ Complies | **Provisional (Ratified)** | **Depends on legacy #6 (Tier 1 SLA→DENY) and #7 (immutable config snapshots).** **Ratified 2026-04-18 via DR-018 §4: #6 reinstated as I-19 (approval state machines fail-closed on SLA expiry); #7 absorbed into amended I-11.** Outcomes applied: (a) TG-12 metrics schema gains a distinct `sla_expiry_deny` event type in addition to the generic ACS-decision entry type — Adapter B emits the specific subtype when the DENY is SLA-expiry-driven; (b) Adapter B `DepthFusionCall` record carries a `config_version_id: string` field referencing the immutable config snapshot active at invocation time (+16 bytes per record; far cheaper than by-value snapshot capture). Same field also applied to gate-log records per I-8 ratification. InvocationLog hash-chain file pointers verified during §4.2: `packages/db/prisma/schema.prisma:237,1059` + `packages/review/src/acs-integration.ts:66`. See §3.3.5 actions 2 (now unconditional). |
| **I-12** | Deep statelessness — `@depthfusion/core` holds no cross-call state; in-session state is CLaRa, cross-session state is Deep Python | ✅ N/A for Adapter B | **Complete** | §6 independent. Applies to the TS library (Adapter A). Adapter B holds transport state (subprocess handle, session ID) explicitly — not a violation because I-12 scopes to `@depthfusion/core`, not to adapters. None of #3–#7 alters this scoping. |
| **I-13** | Zero SkillForge imports in Deep (`@depthfusion/core` never imports `@skillforge/*` or post-migration `@saihai/*`) | ✅ N/A for Adapter B | **Complete** | §6 independent. Applies to the TS library. Adapter B imports `@skillforge/*` legitimately because it is an SF-owned package; this does not touch the TS library's compliance. |
| **I-14** | Event-driven Evolution Engine ↔ CSIE — decoupled by `DiscoveryEvent` on BullMQ; no shared DB, no API bridge | ✅ N/A for Adapter B | **Complete** | §6 independent. Adapter B is neither Evolution Engine nor CSIE; it has no direct compliance obligation. Under any §6 resolution, the event-contract decoupling of Scout/CSIE remains structural and independent of Adapter B's surface. |
| **I-15** | HUMAN_DELEGATED Tier 3–4 only by default (DR-012); Enterprise OPL override for Tier 1 with GSCI compliance attestation (DR-016); Community/Team tiers cannot self-upgrade to HUMAN_DELEGATED for Tier 1–2 | ✅ N/A for Adapter B | **Complete** | Re-examined during §6.1 self-audit: legacy #6 (SLA→DENY) is a runtime behaviour for approval workflows; I-15 is an eligibility-gate invariant (pre-execution). These are orthogonal — I-15 governs *who can request* HUMAN_DELEGATED, #6 governs *what happens if approval stalls*. Adapter B produces no HUMAN_DELEGATED outcomes regardless of either resolution. Verdict-stable. |

### 3.3.3 Provisional-ratio self-audit per DR-017 §6.1 step 4

Count after Option-B reviewer round 1c: **4 Provisional (I-8, I-9, I-10, I-11) out of 15 = 26.7%**. Under the ~30% threshold; no §6.1 narrative paragraph required.

**Post-ratification status (2026-04-18):** all 4 Provisional rows carry a *Ratified* annotation per DR-018 §4. The rows retain their Provisional classification to preserve the §6.1 audit trail (they were design-time Provisional on specific legacy invariants) but their concrete row changes are now applied — see §3.3.5 action statuses. No row moved to Complete post-ratification because the §6.1 classification reflects *dependence*, not *unresolved-ness*; a row can be simultaneously "ratified" and "was-dependent-on-§6".

**First-pass re-examination (rows I initially marked Provisional and downgraded to Complete):**
- **I-2:** first draft flagged #5 dependence; re-examined because I-2's own text already subsumes "RL routing, OPL optimisation, and plugin hooks cannot downgrade" — #5's specific target (MAX_QUALITY) does not change I-2's verdict, only adds granularity elsewhere. Downgraded to Complete.
- **I-7:** first draft flagged #5 dependence via the portability-benchmark-requires-a-target logic; re-examined because benchmarks pin models explicitly and don't route through OPL. The portability claim is verdict-stable. Downgraded to Complete.
- **I-15:** first draft flagged #6 dependence via SLA; re-examined because I-15 is a pre-execution eligibility gate and #6 is a post-submission SLA behaviour — orthogonal concerns. Downgraded to Complete.

**Option-B review round 1c amendments (independent Claude-family reviewer):**
- **I-8 (flipped Complete → Provisional on #7):** reviewer's High 2 accepted. I-8 gate audit entries are in the same audit-needs-stable-config-reference class as I-11. If #7 resolves via (b) or (c), the resolution applies identically to I-8 records. Concrete row change: `config_version_id` field on gate-log records, parallel to the I-11 amendment.
- **I-3 (reviewer's High 1 partially accepted; row stays Complete but note sharpened):** reviewer argued I-3 should be Provisional on #7 by analogy to I-11. Counter: I-3 captures audit artefacts **by-value** (literal YAML text, literal compiled payload, literal response), whereas #7 governs how audit entries *reference* config by-identity. By-value captures don't need reference stability — they are stable by construction. I-3 stays Complete with a sharpened note explicitly distinguishing by-value capture from by-reference logging.

The 4 Provisional rows (I-8, I-9, I-10, I-11) each identify a specific legacy invariant with a specific mechanism by which the Adapter B contract would change. None is a generic "invariant set may be incomplete" note.

### 3.3.4 D-1…D-12 addendum (unchanged)

`[DEPTHFUSION_ARCHITECTURE.md §13]` defines a separate `D-*` namespace for Deep-specific invariants. Per DR-017 §7 cross-links, the D-* series is **not** superseded by DR-017. Adapter B compliance against D-*:

| # | Invariant | Applies to | Adapter B verdict |
|---|---|---|---|
| D-1 | Firewall compliance (zero model / org knowledge) | Both | ✅ Complies — Adapter B forwards abstractions, not model identifiers |
| D-2 | TS core stateless | TS only | N/A |
| D-3 | Gate log mandatory | TS only | N/A (TG-11 adds equivalent to Python; Adapter B forwards when present) |
| D-7 | Budget slice not negotiated | TS only | N/A |
| D-9 | Zero SkillForge imports in Deep TS | TS only | N/A — allows Adapter B's SF imports because Adapter B is a separate SF-owned package |
| D-10 | Mamba Section 10 gated | Both | ✅ Complies — TG-11 ports Mamba §5 only, not §10 |
| D-11 | Python MCP: local only | Python only | ✅ Complies — Adapter B calls DF's local MCP server, no external APIs for storage |
| D-12 | Raw verbatim default | Python only | ✅ Complies — DF corpus stores raw text |

### 3.3.5 Actions flowing out of this compliance pass

All five actions are **live** post DR-018 ratification (2026-04-18). Conditional language retained as historical context; current status annotated per row.

1. **TG-01 acceptance-criterion amendment.** **STATUS: Applied.** Per DR-018 ratification of legacy #5 as I-18, Phase 2 §2.2.4 now carries **AC-01-8: fallback chain is quality-ranked; cost/latency optimisation applies only within the set of backends at or above the current tier's min_quality_score**. AC-01-4 (fallback-chain triggers) remains as-is; AC-01-8 adds the ordering constraint.
2. **TG-12 metrics schema amendment.** **STATUS: Applied.** Per DR-018 ratification of legacy #6 as I-19 and legacy #7 absorbed into I-11, Phase 2 §2.5 JSONL schema now declares: (a) `event_subtype: "sla_expiry_deny" | "user_deny" | "acs_reject" | "ok" | ...` on decision-carrying entries; (b) `config_version_id: string` field on every `DepthFusionCall` record AND on every gate-log record (I-8 + I-11 joint scope).
3. **Adapter B contract amendment.** **STATUS: Applied.** Per DR-018 ratification of legacy #4 as I-17, the §3.2 `DepthFusionMcpAdapter` public contract now includes a security-parity clause: "Adapter B's backend fallback chain preserves TLS, API-key handling, and audit-record uniformity across all backend selections (Haiku / Gemma / Null). Invocation context is indistinguishable at the ACS layer regardless of which backend handled the call."
4. **Unconditional:** update the plan's Phase 2 OP-2 (task budgets) description to note that the resulting `RLMClient` records its `budget_remaining` on every step and the value flows into the same I-11 InvocationLog entry — this is an independent I-11 compliance obligation, not a §6-conditional one.
5. **Unconditional (from round 1c Medium finding):** §3.3.1 §4.2 summary matches DR-018 §2's test-count granularity — `opl/__tests__/policy-store.test.ts:L61` is an entire describe-block with 4 test cases for #5, not a single line. §3.3.1 references DR-018 §2 as the authoritative source for legacy-invariant enforcement depth.

---

## 3.4 SkillForge gap closure

Canonical source confirmed: `SAIHAI_OPENCODE_GAP_CLOSURE_PLAN.md` at `/home/gregmorris/projects/agent-mission-control/docs/Agent-mission-control-evolution/` (the `OPENCODE_VS_SKILLFORGE_GAP_ANALYSIS.md` referenced in the original planning prompt does not exist on disk; the closure plan §Inputs names `OPENCODE_SKILLFORGE_GAP_ANALYSIS.md` as its precursor). The closure plan itemises 14 gaps. DepthFusion v0.5 interacts with the following:

| Gap | Closure plan verdict | DepthFusion v0.5 interaction |
|---|---|---|
| **Gap 3 — MCP server + client** | P0 Server / P2 Client, Phase B. Split into outward + inward. §1.1 sketches `McpAdapter` as the generic inward transport. | **Adapter B is the first concrete implementation of the §1.1 MCP Client Adapter pattern.** This plan closes that gap from the DF side; SF may build other MCP-backed adapters later. |
| **Gap 4 — Context compaction for long sessions** | **Owned by Deep (Saihai Deep = DepthFusion)**, P1, Phase C. Core calls Deep's compaction service when tokens exceed threshold; compacted summaries feed CLaRa's HistoryScorer. | DepthFusion already has `session/compactor.py` and PreCompact/PostCompact hooks `[depthfusion-handoff-context.md §8]`. Adapter B must expose a `compact(...)` capability so Core can delegate. **Adds a v0.5 task group consideration** — see §3.4.1 below. |
| **Gap 5 — Rich plugin event system** | P2, Phase B infrastructure + Phase G expansion. | Not directly DF-owned. Adapter B subscribes to the event bus (§3.2 bridged surfaces table); DF's own auto-capture hooks publish DF-specific events back. |
| **Gap 6 — Session forking / undo / redo** | P3, Phase G. Reframed as flow version branching. | DF's `feedback.py` JSONL persistence `[depthfusion-handoff-context.md §3]` could plausibly feed a future diff-tracking layer but is NOT in v0.5 scope. |
| **Gap 1 — SDK with SSE streaming** | P0, Phase B. | Deferred from v0.5 per Phase 2 §2.8 out-of-scope; v0.6 work. Adapter B v0.5 uses request/response JSON-RPC, not SSE. |

Gaps 2, 7–14 do not have direct DF interactions relevant to v0.5.

### 3.4.1 Action flowing from Gap 4 ownership

The closure plan's assignment of **context compaction to Deep** means DepthFusion is the canonical owner of Saihai's compaction service. Adapter B's capability surface (§3.2 `DepthFusionMcpClient` interface) must therefore include a `compact(transcript, target_tokens)` method, even though TG-05 / TG-06 capture mechanisms are not framed that way in Phase 2. Two options:
- **(a) Add `compact` as an Adapter B capability in v0.5** — thin passthrough to DF's existing `SessionCompressor` (`src/depthfusion/capture/compressor.py` per `[depthfusion-handoff-context.md §3]`). Small delta, closes Gap 4 from the DF side.
- **(b) Defer to v0.6** — honest scoping: Gap 4 is a Phase C item (not v0.5) in the closure plan itself. Adapter B v0.5 exposes recall / graph / recursive / publish; `compact` lands alongside SF Phase C.

**Recommendation: (b).** v0.5 is already 15 task groups; Gap 4 ownership is acknowledged but not acted on. A Phase-C-aligned v0.6 release adds the `compact` capability to Adapter B. This is noted in §3.8 evolution path.

---

## 3.5 Deep vs CLaRa mapping

`[DEPTHFUSION_ARCHITECTURE.md §1]` names the Python standalone explicitly as **"Saihai Deep"**. `[SAIHAI_PLATFORM_CONTEXT.md CLaRa §6]` enumerates six subsystems inside CLaRa (SelectiveHistoryScorer, AdaptiveTokenAllocator, SelectiveContextPacker, BatchRecompute, TieredMemoryCache, MultiTimescaleHistoryBuffer).

**Decision: DepthFusion maps to Deep, not CLaRa.**

- CLaRa is **in-session** state (Hot/Warm/Cold cache, MultiTimescaleHistoryBuffer, session-start bootstrap).
- Deep is **cross-session** state (ChromaDB, SQLite, knowledge graph, discovery files).
- The two communicate per `[SAIHAI_PLATFORM_CONTEXT.md CLaRa §]`: CLaRa provides `ScoredMessage[] + BudgetAllocation + SessionBlock[]` to `@depthfusion/core`; Deep returns `FusedContextPayload + TrajectoryFeedback + MaterialisationResult`.

What this means for Adapter B:
- Adapter B does NOT bridge CLaRa-to-DepthFusion. That bridge is already in the TS `@depthfusion/core` library via the call-signature defined above.
- Adapter B bridges SF runtime (which consumes both CLaRa's output and DepthFusion's output) to the Python standalone.
- CLaRa remains untouched by v0.5.

**Practical implication:** when a SF skill requires cross-session memory, the execution path is:

```
SF skill step "depthfusion.recall" →
  Adapter B (TS) →
    JSON-RPC over stdio →
      DepthFusion MCP server (Python) →
        DepthFusionConfig.from_env() → RecallPipeline →
          { bm25 + embedding + reranker + fusion gates } →
            blocks →
  Adapter B wraps with QualityReport + ACS check →
    SF skill step consumes
```

Each hop has a clear ownership boundary. No hop imports the wrong side.

---

## 3.6 Testing strategy

### Unit tests (Adapter B)

Location: `packages/skillforge-depthfusion-mcp-adapter/src/__tests__/`

- Mock the MCP server JSON-RPC interface
- Verify: `McpAdapterRecallStepExecutor.execute()` wraps results with `QualityReport`
- Verify: I-10 `FloorViolationError` thrown when quality below skill's min_quality_score
- Verify: `DepthFusionRoutingProvider` registers correctly with the capability router
- Verify: InvocationLog entry per call, hash chain unbroken across 100 sequential calls

### Integration tests (real DF instance)

Location: `packages/skillforge-depthfusion-mcp-adapter/tests/integration/`

- Spawn an actual DF MCP server via subprocess (requires `pip install depthfusion` in CI image)
- Run a real recall against a fixture corpus of 5 memory files
- Verify: result shape matches the adapter's TS types exactly
- Verify: DF's Python MCP stdio protocol (JSON-RPC 2.0 per `[depthfusion-handoff-context.md §4]`) is correctly implemented by the adapter

### End-to-end tests (skill invocation)

Location: `apps/api/__tests__/e2e/depthfusion-recall.e2e.test.ts`

- Construct a minimal SF skill that includes a `depthfusion.recall` step
- Invoke via the api app
- Assert: step fires, adapter calls DF, result flows back through ACS, InvocationLog captures the call, final skill output includes the recalled chunks
- Use a fixture corpus isolated per test to avoid contamination

### CIQS benchmark parity

Deep integration should not degrade DepthFusion's CIQS performance compared to direct standalone use. A benchmark run inside SF (via the adapter) against a known corpus should match standalone numbers ±1 point per category.

---

## 3.7 Failure modes

| Scenario | Detection | Adapter B response | User-visible result |
|---|---|---|---|
| DepthFusion MCP server not started | `healthCheck()` returns `unavailable` | Log warning; disable depthfusion-typed skill steps; register a no-op executor that produces an empty result | Skill that uses `depthfusion.recall` completes with empty context; skill's fallback (if defined in Skill IR) activates |
| MCP server started but timing out | `recall()` exceeds configured timeout (default 10s) | Abort the call; return typed `TimeoutError` wrapped in a failed `StepResult` | Skill sees a non-fatal error; either retries or routes to fallback |
| Haiku rate-limited + Gemma OOM + no fallback | DF backend returns `BackendExhaustedError` | Propagate as failed `StepResult`; include the fallback chain in `error.details` so the user sees what was attempted | Skill can retry later or with a different quality floor |
| User uninstalls DepthFusion while SF is running | Next `healthCheck()` fails | Adapter B stops registering depthfusion executors; existing in-flight calls return `unavailable` | SF keeps running; skills with hard dependency on DF raise `DependencyMissingError` |
| DepthFusion version mismatch (SF expects v0.5 API, DF is v0.4) | `healthCheck()` returns a DF version string; adapter checks compatibility | Adapter B refuses to register executors; logs upgrade path | SF logs a startup warning; skills that would have used DF fall through to fallback |
| MCP protocol-level error (`result.isError===true`) | Response inspection | Treat as a caught exception per DF's protocol convention `[project-conventions.md MCP Client Pattern]`; emit typed error | Skill sees failed `StepResult` |

---

## 3.8 Evolution path to Saihai core module

Per the prompt, DepthFusion ultimately becomes a Saihai core module, not merely a SkillForge plugin. The migration sketch:

1. **v0.5 (this release):** DepthFusion standalone + Adapter B + Adapter A (already there). Python standalone installable via `pip install depthfusion`. SF integration via `pnpm install`.
2. **v0.6 (interim):** Consolidate capture mechanisms + cross-mode migration + streaming events. Adapter B gains stream support. No core-module move yet.
3. **v0.7 / Phase C exit:** DepthFusion Python becomes a first-class Saihai subsystem — still installable standalone (invariant D-9 unbroken), but also deployed as a long-running service inside the Saihai platform. Adapter B absorbs the long-running-service concerns (connection pooling, health endpoints, metrics scraping).
4. **Core module migration:** when Saihai reaches a maturity where "Deep" is a named architectural layer rather than a set of adapters, the SF runtime embeds `@depthfusion/core` directly and Adapter B shrinks to a thin MCP transport. DepthFusion source still imports nothing from SF.

**Trigger for core-module decision:** when the adapter's public API stabilises for 3 consecutive SF releases without breaking changes. Until then, adapter is the right layer.

---

## 3.9 Open questions requiring Greg

Resolved since first draft:
- ~~Missing invariants (I-3, I-6, I-7, I-9, I-11, I-14)~~ — **Resolved** by DR-017_INVARIANTS_CANONICAL.md §2; Phase 3 §3.3 complies with DR-017 §4 and §6.
- ~~InvocationLog hash-chain exact location~~ — **Resolved** via DR-017 §4.2 audit: `packages/db/prisma/schema.prisma:L237,L1059` + `packages/review/src/acs-integration.ts:L66`.
- ~~Plugin host contract~~ — **Partially resolved**: `packages/runtime/src/plugins/plugin-host.ts:L67-L70` is the enforcement point for legacy #9 pattern (jointly covered by I-10 + I-2). Adapter B registers as a runtime plugin there; full method-contract reading deferred to implementation-kickoff.
- ~~`OPENCODE_VS_SKILLFORGE_GAP_ANALYSIS.md` vs `SAIHAI_OPENCODE_GAP_CLOSURE_PLAN.md`~~ — **Resolved**: the closure plan at `/home/gregmorris/projects/agent-mission-control/docs/Agent-mission-control-evolution/SAIHAI_OPENCODE_GAP_CLOSURE_PLAN.md` is canonical; its §Inputs names `OPENCODE_SKILLFORGE_GAP_ANALYSIS.md` as the precursor (filename differs slightly from the planning-prompt's reference).
- ~~Capability Router interface~~ — **Resolved**: `packages/runtime/src/router/index.ts:L68` exports `class CapabilityRouter` with `route(skill: SkillIR, constraints?: RoutingConstraints): RoutingResult` at `L71`. Significant correction captured in §3.2: **Adapter B does not attach to the router** — the fusion-strategy slot is already occupied by Adapter A (`SkillForgeDepthFusionAdapter` via `DEFAULT_FUSION_STRATEGY`). Adapter B attaches at the adapter-resolver path per `SAIHAI_OPENCODE_GAP_CLOSURE_PLAN.md §1.1 Gap 3 MCP Client`.
- ~~SkillForge's own MCP client pattern~~ — **Resolved**: grep confirms no existing MCP/JSON-RPC client in `packages/runtime/src/` or `apps/api/src/`. The adapters directory contains only LLM-provider adapters (anthropic, openai, ollama, openrouter, claude-code). **Adapter B will be the first MCP client in SF**, implementing the `McpAdapter` pattern sketched in `SAIHAI_OPENCODE_GAP_CLOSURE_PLAN.md §1.1`.

Sole remaining blocker before Adapter B's first line of code:

1. **DR-017 §6 resolution per legacy invariant.** Each of legacy #3/#4/#5/#6/#7 may resolve independently per DR-017 §6.3. For Adapter B:
   - **#5 (OPL → MAX_QUALITY)**: resolution controls whether TG-01 AC-01-4 fallback chain is quality-ranked or cost-ranked (per §3.3.5 action 1).
   - **#6 (Tier 1 SLA → DENY)**: resolution controls whether TG-12 metrics schema needs a distinct `sla_expiry_deny` event type (action 2).
   - **#7 (immutable config snapshots)**: resolution controls whether Adapter B records a config snapshot per invocation or a version reference (action 2).
   - **#4 (channel security parity)**: resolution controls whether I-9 compliance implies TLS/key/audit parity across backend selections (action 3).
   - **#3 (GSCI write-safety)**: not in-scope for Adapter B directly (Adapter B does not write to GSCI-governed data), but may affect TG-05 decision-extractor if extracted decisions flow into a GSCI-governed sink. Flag for re-examination once TG-05's write target is precisely specified.

The plan document is complete and internally consistent. Item 1 is both a design signal (Phases 2 + 3 acceptance criteria mutate based on resolution direction) and a DR filing question (DR-018 drafted separately to surface evidence for Greg's per-legacy-invariant decision).
