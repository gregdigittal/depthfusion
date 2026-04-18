# Commit Message Drafts — v0.5 Planning Artefacts

> **Status:** drafts for Greg's review. The prompt explicitly reserved commits to Greg; these are ready-to-paste suggestions, not executed commits.
>
> Conventional Commits format per `~/.claude/rules/git-workflow.md`. Co-author footer included.

---

## Option A — single commit (simplest)

Use when Greg wants one reviewable unit covering the full planning output.

```
docs(v0.5): add v0.5 release plan, DR-018 draft, and changelog bootstrap

Four-phase planning deliverable for DepthFusion v0.5:
- docs/plans/v0.5/01-assessment.md — 15-feature ranked list + proceed verdict
- docs/plans/v0.5/02-build-plan.md — 15 task groups, dependency-ordered merge plan
- docs/plans/v0.5/03-skillforge-integration.md — Adapter B spec, DR-017 §6-compliant
  invariant-compliance table (20% Provisional, under §6.1 threshold)
- docs/plans/v0.5/04-rollout-runbook.md — 5-step rollout with ≤10 min rollback paths
- docs/plans/v0.5/README.md — index + TL;DR per file + Greg-decision checklist
- docs/plans/v0.5/CHANGELOG-draft.md — proposed CHANGELOG.md bootstrap

Plus DR-018_LEGACY_INVARIANT_REINSTATEMENT.md staged in the skillforge repo
(separate commit in that repo) enumerating evidence for per-legacy-invariant
DR-017 §6 resolutions.

Planning only — no source changes in this commit. Implementation begins at
TG-01 (backend provider interface) once Greg ratifies the plan and resolves
DR-017 §6 per legacy invariant.

Refs: E-16 (SkillForge Integration), honest-assessment-2026-03-28.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## Option B — two commits (separates planning from changelog bootstrap)

Use when Greg wants to commit the CHANGELOG introduction as its own logical change so it can be cherry-picked to history cleanly.

### Commit 1 — planning docs

```
docs(v0.5): add four-phase release plan + SkillForge integration spec

Four-phase planning deliverable covering:
- Feature assessment with 15-feature ranked list (01-assessment.md)
- Build plan: 15 task groups, dependency-ordered (02-build-plan.md)
- SkillForge integration: two-adapter reality, DR-017 §6-compliant
  invariant-compliance table (03-skillforge-integration.md)
- Rollout runbook: 5-step deployment with recovery paths (04-rollout-runbook.md)
- Index + TL;DR + Greg-decision checklist (README.md)

Every factual claim citation-bound; 3 of 15 invariant rows marked Provisional
with specific legacy-invariant dependencies (DR-018 in skillforge repo enumerates
resolution evidence).

Refs: E-16 (SkillForge Integration)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 2 — CHANGELOG bootstrap

```
chore(changelog): bootstrap CHANGELOG.md with v0.5 unreleased + backfill stubs

docs/release-process.md pre-release checklist references CHANGELOG.md but no
file existed. This commit introduces CHANGELOG.md at the repo root following
the Keep-a-Changelog convention with inline T-/S-/E- backlog references.

Includes:
- [Unreleased] v0.5 planning section (documents current planning work)
- [v0.4.0] + [v0.3.1] + [v0.3.0] stubs — Greg to backfill from git log

Draft lives at docs/plans/v0.5/CHANGELOG-draft.md until Greg ratifies the
format.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## Option C — three commits (finest-grained, matches git-workflow.md's "one logical change per PR")

Use when Greg is preparing a PR and wants reviewable atomic units.

### Commit 1 — assessment + build plan + rollout

```
docs(v0.5): add assessment, build plan, and rollout runbook

Three-document deliverable with no SkillForge dependency:
- 01-assessment.md: 15-feature ranked list, proceed-with-caveats verdict
- 02-build-plan.md: 15 task groups, LLMBackend protocol, three-mode installer
- 04-rollout-runbook.md: 5-step deployment, per-step rollback paths

Refs: E-14 (CIQS Data-Gap Closure), E-17 (Tech Debt)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 2 — SkillForge integration

```
docs(v0.5): add SkillForge integration spec with DR-017 §6 compliance

03-skillforge-integration.md specifies:
- Two-adapter reality (Adapter A already shipped; Adapter B new in v0.5)
- Full DR-017 §4 pre-flight results (cross-ref, legacy-numbering audit,
  firewall self-consistency, enforcement inventory, contradictions)
- 15-invariant compliance table with inline per-row §6 scope classification
  per DR-017 §6.1; 3 Provisional (20%, under ~30% threshold) with specific
  legacy-invariant dependencies named
- Gap-closure plan cross-reference; Gap 3 MCP Client pattern realised as
  first concrete McpAdapter implementation
- 6 failure modes with recovery paths
- Evolution path to Saihai core module

Upstream dependency: DR-018 draft staged in skillforge repo (separate PR).

Refs: E-16 (SkillForge Integration)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 3 — index + CHANGELOG bootstrap + commit drafts

```
docs(v0.5): add README index, CHANGELOG bootstrap, and commit drafts

Session auxiliaries:
- README.md: index + TL;DR + Greg-decision checklist
- CHANGELOG-draft.md: proposed CHANGELOG.md bootstrap with Keep-a-Changelog
  format and inline T-/S-/E- backlog references
- commit-drafts.md: this file — commit message options

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## SkillForge repo — DR-018 commit (separate repo, separate PR)

```
docs(research): add DR-018 draft — legacy invariant reinstatement

DRAFT for Greg's per-legacy-invariant decision per DR-017 §6.3.
Not locked. Enumerates production-code enforcement evidence for each of
legacy #3/#4/#5/#6/#7 (5 files + 3 tests; 2+ files + 3 tests + schema;
router-entry + 4 tests; schema-layer; schema-layer) and proposes:
- I-16 (GSCI confirmation-before-mutation) — (c) reinstate
- I-17 (ACS invocation-context-invariant) — (c) reinstate
- I-18 (unavailable-optimiser default is highest-quality) — (c) reinstate
- I-19 (approval SLA fail-closed) — (c) reinstate
- Legacy #7 (immutable config) — (b) absorb into I-11

Recommended mix: (c)×4 + (b)×1. Greg may override any per §4.

Refs: DR-017 §6

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## BACKLOG.md update (optional, Greg's preference)

If Greg wants the BACKLOG.md update to land alongside the planning docs:

```
docs(backlog): add E-18 v0.5 release epic with S-41..S-55 stories

Adds v0.5 epic structure per docs/plans/v0.5/02-build-plan.md TG-01..TG-15.

Stories mirror the build plan's task groups 1:1; tasks T-115 onwards map
to files touched per TG. Status [backlog] — no work started.

Refs: docs/plans/v0.5/02-build-plan.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

(Content of the BACKLOG addition is generated separately by a subagent and
held in `docs/plans/v0.5/backlog-addition-proposal.md` for Greg's review before
merging into BACKLOG.md.)

---

## Recommendation

**Option B** (2 commits) is the cleanest balance of atomicity and reviewability. The CHANGELOG bootstrap is genuinely a separate concern — once it's in, every future release benefits; keeping it distinct from planning docs makes that clear.

For the SkillForge repo: one standalone commit for DR-018 draft.

For BACKLOG.md: defer until Greg has read the BACKLOG-addition proposal (generated in parallel by the subagent).
