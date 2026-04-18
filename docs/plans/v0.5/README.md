# DepthFusion v0.5 Planning Artefacts — Index

> **Read first:** this file. **Decide next:** DR-017 §6 per-legacy-invariant verdicts in DR-018 (see §5 below).
> **Status (2026-04-17):** planning complete; 1 human-decision blocker remains before implementation kickoff.

---

## What's in this folder

| File | Size | Read-time | What it answers |
|---|---|---|---|
| `01-assessment.md` | 3,264 words | 12 min | *What should v0.5 contain?* Baseline verification vs the honest-assessment snapshot, a 15-feature ranked list with kill-criteria, verdict = "Proceed with 5 caveats." |
| `02-build-plan.md` | 3,450 words | 15 min | *How does v0.5 get built?* 15 task groups (TG-01 → TG-15), dependency-ordered merge plan, foundational `LLMBackend` protocol, three-mode installer (`local` / `vps-cpu` / `vps-gpu`), observability schema, CIQS benchmark gate. |
| `03-skillforge-integration.md` | 3,100+ words | 14 min | *How does DepthFusion plug into SkillForge?* Two-adapter reality (Adapter A shipped; Adapter B new), full 15-invariant compliance table with DR-017 §6-compliant per-row scope classification (20% Provisional, under threshold), four failure modes, evolution path to Saihai core module. |
| `04-rollout-runbook.md` | 2,104 words | 9 min | *How does v0.5 actually deploy?* 5-step rollout (standalone → SF adapter → local → vps-cpu → vps-gpu), concrete shell commands, per-step acceptance + recovery paths (all ≤ 10 min rollback). |
| `README.md` | (this file) | 3 min | Index + commit guidance. |

**Also relevant (outside this folder):**
- `/home/gregmorris/projects/skillforge/docs/research/DR-018_LEGACY_INVARIANT_REINSTATEMENT.md` — DRAFT, needs Greg's per-legacy-invariant decision (5 yes/no/modify calls).

---

## TL;DR per file

**`01-assessment.md` — Should we do this?**
Yes. CIQS baseline is recoverable (88–90 realistic ceiling today; 90–94 after v0.5). Four of the five honest-assessment scoring issues are already fixed in code. Data gap (Category D) is the remaining binding constraint — capture mechanisms are the highest-leverage v0.5 work. 15 features ranked; 2 TS capability ports explicitly deferred to v0.6 (chunk-state compression, materialisation policy) because the Python corpus is too small to need them.

**`02-build-plan.md` — What exactly are we building?**
One foundational refactor (TG-01 `LLMBackend` protocol covering all 4 LLM call-sites — not just the reranker), one installer change (TG-02 three-mode), three new backends (Haiku, Gemma, Null), six capture mechanisms, a graph schema extension, observability extensions, and a prune tool. `FLAG=false` byte-identical output is the load-bearing regression test. CIQS benchmark runs per TG; >2 points regression blocks merge.

**`03-skillforge-integration.md` — How does DF become a Saihai subsystem?**
Python DF source imports nothing from SF. A new TypeScript package `packages/skillforge-depthfusion-mcp-adapter/` holds all the bridging — the first concrete implementation of the `McpAdapter` pattern sketched in `SAIHAI_OPENCODE_GAP_CLOSURE_PLAN.md §1.1 Gap 3`. Compliance verified against all 15 DR-017 invariants + the 8 DepthFusion D-1…D-12 invariants. 3 of 15 rows Provisional (I-9, I-10, I-11) — each names the specific legacy invariant in limbo and the specific contract change under DR-018's recommended verdicts. Gap 4 (context compaction) is acknowledged as Deep-owned but deferred to v0.6 alongside SF Phase C.

**`04-rollout-runbook.md` — How do we actually ship it?**
Five sequential steps: DF standalone release (3 mode smoke tests), Adapter B impl + test suites, SF+DF local, SF+DF vps-cpu, SF+DF vps-gpu on the GEX44. Each step's rollback is ≤ 10 min. GPU step is gated on GEX44 provisioning. First week of post-rollout has daily CIQS runs + subjective shadow-testing per environment.

**`DR-018` (draft, skillforge repo) — The only remaining planning blocker.**
DR-017 §6 asks Greg to decide per-legacy-invariant whether each of #3/#4/#5/#6/#7 is (a) de-escalated, (b) absorbed, or (c) reinstated as a new I-N. DR-018 assembles the evidence: all five have live automated enforcement in production code + tests + schema. Recommended verdict is (c)×4 + (b)×1. Once decided, a subset of acceptance criteria in Phases 2 + 3 become unconditional; nothing blocks.

---

## What Greg needs to decide

1. **DR-018 verdicts** (§4 of that doc). 5 decisions, each a one-liner. Once locked, downstream amendments cascade automatically.
2. **Whether to commit these planning docs before or after DR-018 is resolved.** The docs are internally consistent and §6-compliant as-written; committing now freezes a planning artefact and lets Adapter B implementation start on TG-01/TG-02 (which are §6-independent).
3. **BACKLOG.md integration.** A proposed BACKLOG entry for v0.5 is being generated alongside this README — once available, it's a manual paste-in by Greg.
4. **CHANGELOG bootstrap.** DepthFusion does not yet have a CHANGELOG.md. Release-process.md references it in the pre-release checklist. v0.5 is a natural moment to introduce it. A starter entry is in `CHANGELOG-draft.md` alongside this folder.

---

## Suggested reading order

- **If you have 10 minutes:** read this README + `01-assessment.md §1.7 ranked feature list` + `01-assessment.md §1.8 verdict`.
- **If you have 30 minutes:** add `02-build-plan.md §2.2 (backend interface)` + `03-skillforge-integration.md §3.3 (compliance table)`.
- **If you have an hour:** all four plan docs + DR-018 draft. Decide DR-018 verdicts.

---

## Change log for this folder

| Date | Change | Author |
|---|---|---|
| 2026-04-17 | Initial four-phase deliverable (01, 02, 03, 04) + DR-018 draft | Claude Code worker, fresh-analysis session |
| 2026-04-17 | Phase 3 §3.3 rewritten against DR-017 §4 + §6 | Claude Code worker, post DR-017 publication |
| 2026-04-17 | Phase 3 §3.2 + §3.4 + §3.9 patched with SF source references (Capability Router, adapter resolver, closure-plan Gap 3/4) | Claude Code worker |
| 2026-04-17 | This README + CHANGELOG-draft + commit drafts added | Claude Code worker, parallel-task run |
