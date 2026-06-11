# DepthFusion V2 — Lane Merge Plan

> **Purpose:** Defines the sequence and prerequisites for merging all four V2 lane branches  
> into `v2-enterprise`, and then merging `v2-enterprise` into `main`.  
> **Owner:** Fable-5 PM + program lead  
> **Related:** `docs/plans/G1-gate.md` through `docs/plans/G4-gate.md`, `docs/v2/pilot-checklist.md`

---

## Overview

The V2 program uses a phase-gate model. Each gate unlocks the next phase of work. Lane branches
are merged into `v2-enterprise` at gate boundaries, not continuously. This prevents half-finished
features from breaking the integration branch.

```
v2/lane-a-authz  ──────┐
v2/lane-b-ingest ──────┤  merge at G1/G2 boundary
v2/lane-c-ui     ──────┤  merge at G2/G3 boundary  
v2/lane-d-platform ────┘  merge at G3/G4 boundary

v2-enterprise (integration) ──────────────────────── merge to main at G4 PASS
```

---

## Phase 1 → Phase 2 (G1 gate)

**Gate file:** `docs/plans/G1-gate.md`

### What must be true before G1 can be declared

| Criterion | Responsible lane | Status |
|-----------|-----------------|--------|
| C1: OIDC login against Entra ID test tenant | Lane A (`v2/lane-a-authz`) | Pending |
| C2: ACL schema present (principal + device tables, acl_allow/classification columns defined) | Lane A | Pending |
| C3: DocumentParser protocol merged | Lane B (`v2/lane-b-ingest`) | Done (evidence in G1-gate.md) |
| C4: Tauri shell boots on Mac and Windows | Lane C (`v2/lane-c-ui`) | Pending |
| C5: Sync v2 design docs complete and reviewed | Lane D (`v2/lane-d-platform`) | Done (evidence in G1-gate.md) |
| C6: sync.sh frozen (R-1 enforcement) | Lane D | Done (commit 1bf5573) |
| C7: CI green on v2-enterprise merge commit | All lanes | Pending (runs on merge) |

### Merge sequence at G1

All merges go to `v2-enterprise`. Merge conflicts must be resolved on the lane branch before
merging (never resolve conflicts directly on `v2-enterprise`).

**Order matters** — Lane A must land first because it introduces the `identity/` and `authz/`
packages that Lane B's ACL-stamping code depends on.

```
Step 1: Merge v2/lane-a-authz → v2-enterprise
    Prereqs: C1 and C2 evidence filled in G1-gate.md
    Command:
        git checkout v2-enterprise
        git merge --no-ff v2/lane-a-authz -m "merge(v2): lane-a-authz at G1"
    Verify: pytest tests/ -x -q --cov=src/depthfusion --cov-fail-under=80
    Verify: ruff check src/ tests/ && mypy src/

Step 2: Merge v2/lane-b-ingest → v2-enterprise
    Prereqs: Step 1 complete; C3 evidence filled
    Command:
        git merge --no-ff v2/lane-b-ingest -m "merge(v2): lane-b-ingest at G1"
    Verify: pytest tests/ -x -q --cov=src/depthfusion --cov-fail-under=80

Step 3: Merge v2/lane-c-ui → v2-enterprise
    Prereqs: Step 2 complete; C4 evidence filled
    Note: Lane C contains the Tauri app (app/ subdirectory). Python tests are
    unaffected; Tauri build is validated by tauri-build.yml CI.
    Command:
        git merge --no-ff v2/lane-c-ui -m "merge(v2): lane-c-ui at G1"
    Verify: pytest tests/ -x -q  (Python suite)
    Verify: CI tauri-build.yml passes on the merged commit

Step 4: Merge v2/lane-d-platform → v2-enterprise
    Prereqs: Step 3 complete; C5, C6 evidence filled
    Command:
        git merge --no-ff v2/lane-d-platform -m "merge(v2): lane-d-platform at G1"
    Verify: pytest tests/ -x -q --cov=src/depthfusion --cov-fail-under=80

Step 5: Run integration smoke test
    bash scripts/integration_smoke_test.sh
    Expected: "V2 Integration Smoke Test: PASSED"

Step 6: Run v2-gate-review workflow
    Args: { gate: "G1", criteria: ["C1","C2","C3","C4","C5","C6","C7"] }
    On PASS: record decision, fork Phase 2 worktrees
    On FAIL: identify blocking criteria, file remediation tasks
```

### Post-G1 branch state

After G1 declaration:
- `v2-enterprise` contains all four lane branches merged
- Phase 2 worktrees forked from `v2-enterprise` HEAD
- R-1 enforcement confirmed on all enrolled devices (`sync.sh` exits non-zero)
- Phase 2 stories (E-50, E-51, E-54, E-57) unlocked in the backlog

---

## Phase 2 → Phase 3 (G2 gate)

**Gate file:** `docs/plans/G2-gate.md`

### Key Phase 2 deliverables

| Epic | Title | Lane | Phase |
|------|-------|------|-------|
| E-50 | Authorization Model — RBAC + Record ACLs + Classification | A | 2 |
| E-51 | Security-Trimmed Retrieval & Query API v2 | A | 2 |
| E-54 | SharePoint Connector | B | 2 |
| E-55 | BI Layer | B | 2 |
| E-57 | Search & Citation UI | C | 2 |
| E-52 build | Sync v2 replication engine | D | 2 |

### Merge sequence at G2

The G2 merge follows the same pattern as G1, with Phase 2 lane branches merged into
`v2-enterprise` in dependency order:

```
Step 1: Lane A Phase 2 (authz RBAC/ACL) — must land before Lane B Phase 2
Step 2: Lane B Phase 2 (SharePoint + BI)
Step 3: Lane C Phase 2 (Search + Citation UI)
Step 4: Lane D Phase 2 (Sync v2 build)
Step 5: Integration smoke test
Step 6: v2-gate-review G2
```

G2 additionally requires:
- Security-trimmed retrieval passes the ACL leak test-suite (G2 C2)
- SharePoint live crawl on the pilot site with correct permission filtering (G2 C1)
- Sync v2 bidirectional replication passes the 3-instance test (G2 C3)

---

## Phase 3 → Phase 4 (G3 gate)

**Gate file:** `docs/plans/G3-gate.md`

### Key Phase 3 deliverables

Phase 3 focuses on the full product surface (E-57 search UI complete, E-58 offline cache,
E-59 export controls) and the first enterprise validation milestone.

### Merge sequence at G3

Same pattern. Phase 3 lane branches merged in dependency order after G3 gate evidence is filled.
G3 additionally requires:
- Offline cache passes the revocation test matrix (G3 C4)
- Export controls pass the policy enforcement test (G3 C3)
- Audit log covers all event types with tamper-evidence (G3 C5)

---

## Phase 4 → main (G4 gate)

**Gate file:** `docs/plans/G4-gate.md`

### Prerequisites for merging to main

G4 is the production gate. Every prior gate (G1–G3) must be PASS before G4 can be declared.

| Check | Required |
|-------|----------|
| G1–G3 all declared PASS | Yes |
| Pen-test critical findings: 0 open | Yes |
| Pilot success metrics met (S-204) | Yes |
| V1→V2 migration rehearsed on production copy (S-206) | Yes |
| All docs complete (E-62) | Yes |
| Final consensus review by Deepseek + Gemini | Yes |
| Tagged release prepared | Yes |
| Rollback plan documented | Yes |

### Final merge to main

```
Step 1: Declare G4 PASS via v2-gate-review workflow
Step 2: Create tagged release candidate
    git checkout v2-enterprise
    git tag -a v2.0.0-rc1 -m "DepthFusion V2.0.0 release candidate"
Step 3: Open PR: v2-enterprise → main
    Title: "feat(v2): DepthFusion V2.0.0 — enterprise identity, SharePoint, desktop app"
    Include: migration guide, rollback runbook, G1–G4 gate evidence links
Step 4: Final consensus review
    Reviewers: Deepseek + Gemini automated; human DS/GM sign-off
    Any unresolved disagreements surfaced with pros/cons before merge
Step 5: Merge (squash disabled — preserve lane commit history)
    git checkout main
    git merge --no-ff v2-enterprise -m "feat(v2): DepthFusion V2.0.0"
Step 6: Tag and push
    git tag -a v2.0.0 -m "DepthFusion V2.0.0"
    git push origin main v2.0.0
Step 7: Publish release notes (see docs/release-process.md)
```

### Rollback plan (post-main-merge)

If a critical issue is discovered after the merge to main:

1. **Immediate:** Revert the merge commit on main (`git revert -m 1 <merge-sha>`) and push
2. **Data:** V2 data stores are forward-compatible; rollback does not require data migration
   *unless* the ACL backfill (step 3.2 in pilot-checklist) was irreversible
3. **Clients:** Desktop app can be reverted via the update channel (flip `active: false` in
   `tauri.conf.json` updater to block the V2 build from auto-updating)
4. **Monitoring:** Check `GET /v2/admin/audit` for any auth or ACL anomalies in the first 24h
5. **Communication:** Notify pilot participants; document the issue in `docs/decisions/`

---

## CI Requirements for Each Merge

Every lane merge to `v2-enterprise` must pass:

```yaml
# .github/workflows/ci.yml (excerpt)
jobs:
  test:
    - pytest tests/ -x -q --cov=src/depthfusion --cov-fail-under=80
  lint:
    - ruff check src/ tests/
    - mypy src/
  tauri-build:
    - (Lane C merges only) tauri-build.yml workflow
```

A merge is blocked if any of these checks fail. No exceptions. If the coverage floor
drops below 80%, the test suite must be updated before the merge proceeds.

---

## Conflict Resolution Policy

When a lane merge produces conflicts:

1. The **lane branch owner** resolves the conflict on the lane branch
2. Both the lane owner and the `v2-enterprise` maintainer must review the resolution
3. Conflicts in `src/depthfusion/mcp/server.py` require Fable-5 consensus review
   (file is owned by Lane A for the auth dispatch surface; other lanes append-only)
4. Conflicts in `tests/` are resolved by the lane that wrote the conflicting test
5. Conflicts in `BACKLOG.md` are resolved by taking the union of `[x]` entries
   (never un-check a completed task during merge resolution)
