# V2 Merge-Gate Checklist & Release / Rollback Documentation

**Date:** 2026-06-18  
**Purpose:** Validate all gates are green before merging `v2-enterprise` to `main`, execute tagged release, and document rollback procedures.

---

## Part A: Pre-Conditions Checklist

All four gates must be declared **PASS** before proceeding to merge. This section enumerates the explicit gate criteria and their completion status.

### G1 — Phase 1 → Phase 2 Gate

**Gate Status:** [x] PASS  
**Declaration Date:** 2026-06-11  
**Reference:** `docs/plans/G1-gate.md`

#### G1 Criteria Summary

| Criterion | Story / Task | Status | Evidence |
|-----------|--------------|--------|----------|
| **C1** — OIDC login against Entra ID test tenant | S-156 (Identity) | [x] PASS | v2/lane-a-authz identity package; route-walker test green |
| **C2** — ACL schema present (not fully migrated) | S-156 (Identity) | [x] PASS | 0001_acl_columns.sql, 0002_roles.sql merged; dry-run green |
| **C3** — DocumentParser protocol merged | S-169 (Document Ingestion Framework) | [x] PASS | T-590/T-591/T-592 (45 tests pass) |
| **C4** — Tauri shell boots on Mac and Windows | S-180/S-181/S-182 (Desktop UI Shell) | [x] PASS | T-628 typed IPC/CSP, T-630 token vault; binaries built |
| **C5** — Sync v2 design docs complete and reviewed | S-166 (Sync v2 design) | [x] PASS | T-581 + T-582 committed; Fable-5 review passed |
| **C6** — sync.sh frozen (R-1 enforcement) | S-166 (Sync v2 design) | [x] PASS | T-588 deployed; sync.sh exits non-zero |
| **C7** — CI green on v2-enterprise | — | [x] PASS | Merge sequence per docs/v2/merge-plan.md; ruff + mypy clean |

**G1 VERDICT:** [x] PASS — All epics E-49, E-53, E-56, E-52 design complete; unlock Phase 2.

---

### G2 — Phase 2 → Phase 3 Gate

**Gate Status:** [ ] PENDING  
**Expected Completion:** TBD  
**Reference:** `docs/plans/G2-gate.md`

#### G2 Criteria Summary

| Criterion | Story / Task | Status | Evidence |
|-----------|--------------|--------|----------|
| **C1** — SharePoint delta sync ingests pilot site with ACL fidelity | S-173/S-174/S-175/S-176 (SharePoint Graph Integration) | [ ] — | Pilot site ingestion + delta token persistence |
| **C2** — Security-trimmed query API passes ACL leak test-suite | S-163/S-164/S-165 (Security-Trimmed Retrieval) | [ ] — | `test_acl_leak` suite (E-51, S-163) 0 failures |
| **C3** — UI search works online (Tauri + VPS backend) | S-183/S-184/S-185 (UI Search & Recall) | [ ] — | mTLS round-trip < 3s on LAN |
| **C4** — ACL backfill complete (V2-DEC-002 enforced) | S-160 (RBAC / ACL Enforcement) | [ ] — | All pre-V2 docs have `acl_allow` ≥ `[greg]` |
| **C5** — Sync v2 live (R-1 clean handoff complete) | S-167/S-168 (Sync v2 build) | [ ] — | Hub process running; ≥1 successful sync cycle |
| **C6** — CI green on v2-enterprise | — | [ ] — | All tests pass; `test_acl_leak` + `test_security_trim` explicit |

**G2 VERDICT:** [ ] PENDING — Epics E-50, E-51, E-54, E-57, E-52 build must complete before G2 declaration.

---

### G3 — Phase 3 → Phase 4 Gate

**Gate Status:** [ ] PENDING  
**Expected Completion:** TBD  
**Reference:** `docs/plans/G3-gate.md`

#### G3 Criteria Summary

| Criterion | Story / Task | Status | Evidence |
|-----------|--------------|--------|----------|
| **C1** — Offline cache demonstrated end-to-end | S-186/S-187/S-188 (Offline Cache) | [ ] — | Demo: ≥50 documents cached; offline query successful; LRU eviction works |
| **C2** — Export controls active | S-189/S-190/S-191 (Export & Compliance Controls) | [ ] — | Blocked for unpermissioned users; permitted users get formatted output |
| **C3** — BI endpoints security-trimmed | S-177/S-178/S-179 (BI Connector) | [ ] — | All BI paths apply security_trim; ACL leak test passes |
| **C4** — Audit log active and queryable | S-192/S-193/S-194 (Audit Log & Compliance) | [ ] — | Immutable records for: sign-in, search, export, ACL change, sync |
| **C5** — Zero P0 defects open | — | [x] PARTIAL | V2-DEC-001 (expression_eval.py DoS) resolved 2026-06-11 (commit 6a7ec67) |
| **C6** — CI green on v2-enterprise | — | [ ] — | All suites pass; coverage ≥80% |

**G3 VERDICT:** [ ] PENDING — Epics E-58, E-59, E-55, E-60 must complete; V2-DEC-001 is resolved (C5 unblocks lane halt).

---

### G4 — Phase 4 → Merge to main Gate

**Gate Status:** [ ] PENDING  
**Expected Completion:** TBD  
**Reference:** `docs/plans/G4-gate.md`

#### G4 Criteria Summary

| Criterion | Story / Task | Status | Evidence |
|-----------|--------------|--------|----------|
| **C1** — Full E-61 security suite green; pen-test findings addressed | S-198/S-199/S-200/S-201 (Performance + Pen-Test) | [ ] — | T-684 (fix criticals + regression) + pen-test report resolved |
| **C2** — Migration guide validated on VPS clone | S-203 (Migration Tooling) | [ ] — | T-694 (production-copy migration rehearsal + report) green |
| **C3** — Pilot validation complete | S-204 (Structured Pilot) | [ ] — | T-696 (pilot execution + feedback triage) + success metrics met |
| **C4** — v2.0.0 tag criteria met | — | [ ] — | CHANGELOG.md + migration-guide-v2.md + decision ADRs + git tag |
| **C5** — Full test suite green on v2-enterprise pre-merge commit | — | [ ] — | Unit + integration + E2E + security + ACL leak + offline + export + BI security |
| **C6** — No P0 defects open; P1 defects accepted or resolved | — | [ ] — | Zero open P0; all P1 either resolved or accepted |

**G4 VERDICT:** [ ] PENDING — Epics E-61, E-62, E-63 must complete; this is the final gate. On PASS, merge and apply tag `v2.0.0`.

---

## Part B: Deepseek + Gemini Consensus Review

This section documents the **Fable-5 vendor-isolation review** of the final merge diff before v2.0.0 is tagged. The review enforces that developers (Opus/Sonnet, lane-specific) are NOT the same vendors as reviewers.

### Review Mandate

**Principle:** Different vendors catch different bug classes. Deepseek (cost-efficient reasoning, alternative semantics) + Gemini (large-context analysis, cross-file patterns) act as independent reviewers of the merge diff to identify issues the lane devs missed.

**Scope:** The complete diff from `main..v2-enterprise` at the time G4 is declared PASS.

### Review Instructions (Template)

Deepseek and Gemini are NOT to merge the code; they are to audit it adversarially. The following instruction block is issued to both reviewers in parallel:

---

**INSTRUCTION BLOCK: V2 Merge Diff Consensus Review**

**Reviewers:** Deepseek (DS), Gemini (GM)  
**Task:** Consensus adversarial review of `v2-enterprise` merge diff before tag and production deployment.

**Scope:**
```bash
git diff main..v2-enterprise --stat
```

**Deliverables per reviewer:**
1. **Security findings** (injection, auth bypass, data leakage, DoS surface)
2. **Architecture fit** (does the diff align with E-49 through E-63 design intent?)
3. **Test coverage** (are critical paths tested? Any untested migrations?)
4. **Performance concerns** (are there N+1 patterns, unbounded iterations, or blocking calls in hot paths?)
5. **Operational readiness** (is the rollback plan sufficient? Are migrations idempotent?)

**Grading Criteria:**
- [ ] **PASS** — No blocking issues; non-blocking items documented as post-v2.0.0 backlog
- [ ] **FIX_REQUIRED** — Blocking issue(s) identified; require fix before merge
- [ ] **NEEDS_WORK** — Multiple issues suggest diff is not ready; recommend rebase and re-review

**Consensus threshold:** Both reviewers must agree to PASS or FIX_REQUIRED. If they disagree, escalate findings to human arbitration (Fable-5 PM) with both positions documented.

**Output format (JSON):**
```json
{
  "reviewer": "deepseek|gemini",
  "timestamp": "2026-06-18T...",
  "findings": [
    {
      "category": "security|architecture|testing|performance|operational",
      "severity": "critical|high|medium|low",
      "title": "...",
      "description": "...",
      "recommendation": "..."
    }
  ],
  "verdict": "PASS|FIX_REQUIRED|NEEDS_WORK",
  "consensus_summary": "..."
}
```

**Deadline:** Review must complete within 24 hours of G4 PASS declaration.

---

### Consensus Decision

Once both Deepseek and Gemini complete their reviews:

1. **Verdicts match (both PASS):** Proceed to Part C (tagged release).
2. **Verdicts match (both FIX_REQUIRED or NEEDS_WORK):** File remediation tasks; do NOT merge until fixed.
3. **Verdicts disagree:** Fable-5 PM runs `code-decisions-debate` between the two; final verdict is the debate outcome.

Record the consensus and debate outcome (if any) before proceeding to Part C.

---

## Part C: Tagged-Release Steps

### Prerequisites (must all be PASS)

- [x] G1 PASS
- [ ] G2 PASS
- [ ] G3 PASS
- [ ] G4 PASS
- [ ] Deepseek + Gemini consensus PASS (Part B)

### Release Procedure

**1. Final CI verification on `v2-enterprise` HEAD**

```bash
cd /home/gregmorris/projects/depthfusion

# Verify branch is clean and at expected commit
git status
git log --oneline -1
# Expected: v2-enterprise branch at the final merge commit

# Run full test suite
make test   # or: pytest tests/ -v --cov=src/ --cov-fail-under=80

# Verify lint and type-check
ruff check src/ tests/
mypy src/

# Expected: All pass; coverage ≥ 80%
```

**2. Update CHANGELOG.md**

Add a new entry at the top of `CHANGELOG.md` documenting all V2 changes grouped by epic:

```markdown
## [2.0.0] — 2026-06-18

### Added

**E-49: Identity Foundation (S-156, S-157, S-158)**
- OIDC authentication against Entra ID with device-code flow
- Principal-scoped authorization with `require_principal` dependency injection
- Token validation and JWKS signature verification

**E-50: RBAC / ACL Enforcement (S-159, S-160, S-161, S-162)**
- ACL schema migration (acl_allow, classification columns)
- Per-store ACL enforcement in retrieval paths
- `security_trim` filter applied at query layer

**E-51: Security-Trimmed Retrieval (S-163, S-164, S-165)**
- ACL leak test suite and security regression gates
- Cross-principal isolation verification
- API returns 403 for unauthorized queries

**E-52: Sync v2 Design & Build (S-166, S-167, S-168)**
- Sync v2 hub-and-spoke architecture with change-log cursor model
- Record envelope schema: payload + ACL + classification + tombstones
- LWW conflict policy with server-authority exception for classification

**E-53: Document Ingestion Framework (S-169, S-170, S-171, S-172)**
- DocumentParser protocol with quarantine store
- Generic fallback parser (text, markdown, HTML)
- Thread-safe concurrent ingest workers

**E-54: SharePoint Graph Integration (S-173, S-174, S-175, S-176)**
- Delta sync connector for SharePoint sites
- ACL fidelity from Graph API permissions
- Delta token persistence and re-sync on reconnect

**E-55: BI Connector (S-177, S-178, S-179)**
- BI query endpoints with security_trim applied
- Service principal with least-privilege scopes
- Response serialization strips acl_allow and raw classification

**E-56: Desktop UI Shell (S-180, S-181, S-182)**
- Tauri-based desktop application (Mac universal + Windows x64)
- OIDC sign-in with token vault (OS keychain / Windows DPAPI)
- Typed IPC layer with CSP security headers

**E-57: UI Search & Recall (S-183, S-184, S-185)**
- Online search integration with VPS backend over mTLS
- Result rendering with classification badges
- "No results" state and error handling

**E-58: Offline Cache (S-186, S-187, S-188)**
- Client-side caching of ≥50 documents
- Offline query serving without network
- Cache invalidation on delta sync reconciliation
- LRU eviction within configured limit (default 500 MB)

**E-59: Export & Compliance Controls (S-189, S-190, S-191)**
- Export-to-file action with permission and classification checks
- Formatted output (PDF/DOCX) with classification header/footer
- DLP-style regex rule configuration for SSN/PII blocking
- Audit event logged on export

**E-60: Audit Log & Compliance (S-192, S-193, S-194)**
- Immutable audit log table for: sign-in, search, export, ACL change, sync
- Admin UI / CLI query by principal, event type, date range
- Records include: timestamp (UTC), principal, device, event type, resource, classification

**E-61: Performance + Penetration Testing (S-198, S-199, S-200, S-201)**
- Internal penetration test: token forgery/replay, IDOR, ACL bypass, cache extraction, clock-rollback, export bypass, sync impersonation
- All critical findings remediated and regression-tested
- Performance profiling and optimization for V2 workloads

**E-62: Documentation + Migration Guide (S-202, S-203, S-204)**
- Migration guide for V1 → V2 upgrade (dry-run mode, rollback procedure)
- Documentation of ACL schema changes and legacy-data backfill
- Structured pilot execution support

**E-63: Integration Validation + Pilot + Merge (S-205, S-206)**
- Merge-gate checklist and release / rollback documentation
- ACL migration rehearsal on production-copy with second-principal leak verification
- Bulk ACL grant/revoke drill

### Security

- Resolved V2-DEC-001: expression_eval.py DoS guards (F2/F3/F5/F6) implemented and tested
- All OWASP Top 10 items mitigated or accepted with ADR
- Pen-test criticals addressed; no unmitigated HIGH/CRITICAL findings

### Migration

- See `docs/migration-guide-v2.md` for step-by-step upgrade instructions
- Dry-run mode available; idempotent rollback procedure documented
- Production migration rehearsed on VPS clone

---
```

Commit the CHANGELOG update:

```bash
git add CHANGELOG.md
git commit -m "docs: update CHANGELOG for v2.0.0 release"
```

**3. Ensure migration-guide-v2.md is committed**

Verify that `docs/migration-guide-v2.md` exists and is complete:

```bash
ls -la /home/gregmorris/projects/depthfusion/docs/migration-guide-v2.md

# Expected: file exists with sections:
#   - Prerequisites
#   - Step-by-step migration
#   - Dry-run mode
#   - Rollback procedure
#   - Known issues
#   - Post-migration smoke test
```

If not present, this is a blocker for G4 completion (S-203 acceptance criteria).

**4. Verify all V2 decision ADRs are in docs/decisions/**

```bash
ls -la /home/gregmorris/projects/depthfusion/docs/decisions/ | grep -E "V2-DEC|ADR.*v2"

# Expected: at least:
#   - V2-DEC-001.md (expression_eval.py DoS — RESOLVED)
#   - ADR-S199-internal-pentest-plan.md (pen-test coverage)
#   - Any other V2-specific architectural decisions
```

**5. Merge v2-enterprise into main (merge commit, not squash)**

```bash
# Ensure main is up to date
git checkout main
git pull origin main

# Merge v2-enterprise with merge commit (preserves history)
git merge --no-ff v2-enterprise

# Expected: merge commit created, conflicts (if any) resolved
git log --oneline -3   # verify merge commit is at top
```

**6. Run ACL leak test suite immediately post-merge**

```bash
# Critical safety gate: confirm no ACL leak regression in merge
pytest tests/test_acl_leak.py -v

# Expected: all tests pass (0 failures)
```

**7. Apply the v2.0.0 tag**

```bash
# Tag on the merge commit (NOT pre-merge)
git tag -a v2.0.0 \
  -m "DepthFusion V2 Enterprise: OIDC + ACL + SharePoint + Offline

Merge commit: $(git rev-parse HEAD)
Build: v2-enterprise branch (Phase 1-4 complete)
Gates: G1-PASS (2026-06-11), G2-PASS, G3-PASS, G4-PASS
Pen-test: S-199 T-684 critical findings resolved
Migration: docs/migration-guide-v2.md validated on VPS clone
ACL leak test: post-merge suite green

See docs/migration-guide-v2.md for upgrade instructions.
See docs/decisions/ for decision ADRs and pen-test findings."

# Verify tag was created
git tag -v v2.0.0    # verify signed (if GPG-signed)
git show v2.0.0      # show tag details
```

**8. Push the tag to origin**

```bash
git push origin v2.0.0

# Verify push succeeded
git ls-remote origin refs/tags/v2.0.0

# Expected: output shows v2.0.0 tag at the merge commit SHA
```

**9. Create release notes and post to GitHub Releases (optional)**

```bash
# If using GitHub CLI:
gh release create v2.0.0 \
  --title "DepthFusion v2.0.0 — Enterprise Edition" \
  --notes-file RELEASE_NOTES.md

# Otherwise, go to https://github.com/gregmorris/depthfusion/releases/new
# and fill in manually using CHANGELOG.md excerpt above
```

---

## Part D: Rollback Plan

If critical issues are discovered after the v2.0.0 tag is applied but **before** production migration, OR if production migration fails, use the procedures below.

### Pre-Production Rollback (Tag Applied, No Migration Yet)

**Scenario:** v2.0.0 tag was created and pushed, but no production VPS has been migrated yet.

**Procedure:**

```bash
cd /home/gregmorris/projects/depthfusion

# 1. Delete the v2.0.0 tag locally
git tag -d v2.0.0

# 2. Delete the v2.0.0 tag from remote (requires push permission)
git push origin :refs/tags/v2.0.0

# 3. Revert the merge commit on main
# (assumes the merge commit is at HEAD; adjust if needed)
git revert -m 1 HEAD

# 4. Push the revert commit to main
git push origin main

# Expected: main now points to a revert commit that undoes the merge

# 5. File a remediation task in BACKLOG.md for the issue
# and schedule G4 re-evaluation once fixed
```

**Verification:**

```bash
# Confirm v2.0.0 tag is gone from remote
git ls-remote origin refs/tags/v2.0.0
# Expected: (no output)

# Confirm main is at the revert commit
git log origin/main --oneline -2
# Expected: most recent shows "Revert \"Merge branch v2-enterprise into main\""

# Confirm v2-enterprise branch is still intact for fixes
git log origin/v2-enterprise --oneline -1
# Expected: still at the pre-revert commit
```

---

### Production Rollback (Post-Migration)

**Scenario:** Production VPS was migrated to V2, but critical issues were discovered that require rollback to V1.

**Prerequisites:**

- A backup of the production VPS database snapshot taken **before** migration (required by G4 safety checks)
- The pre-migration database snapshot must be restorable (tested on a clone per G4 C2)
- The rollback procedure was documented in `docs/migration-guide-v2.md` (required by G4 C4)

**High-Level Rollback Steps:**

1. **Stop V2 services on production VPS:**
   ```bash
   ssh gregmorris@176.9.147.206
   
   sudo systemctl stop depthfusion-api
   sudo systemctl stop depthfusion-sync-hub
   sudo systemctl stop depthfusion-mcp
   ```

2. **Restore pre-migration database snapshot:**
   ```bash
   # This command is specific to the database (PostgreSQL, SQLite, etc.)
   # Example for PostgreSQL:
   sudo systemctl stop postgresql
   sudo rm -rf /var/lib/postgresql/14/main
   sudo tar -xzf /backups/depthfusion-pre-v2-migration-$(date +%Y-%m-%d).tar.gz \
     -C /var/lib/postgresql/14/
   sudo chown -R postgres:postgres /var/lib/postgresql/14/main
   sudo systemctl start postgresql
   
   # Verify the restoration
   psql -U depthfusion -d depthfusion -c "SELECT version();"
   ```

3. **Checkout v1 code on main:**
   ```bash
   cd /home/gregmorris/projects/depthfusion
   
   # Tag before the v2-enterprise merge is the v1 baseline
   # Find the tag (e.g., v1.5.1 or whatever the last v1 release was)
   git tag -l | grep "^v1\." | sort -V | tail -1
   # Expected output: (e.g.) v1.5.1
   
   # Check out that tag
   git checkout v1.5.1
   
   # Or, if not tagged, revert main to the commit before v2-enterprise merge
   git log main --oneline | grep "Merge branch v2-enterprise"
   # Find the commit hash
   git revert -m 1 <commit-hash>
   git checkout main
   ```

4. **Restart V1 services:**
   ```bash
   sudo systemctl start depthfusion-api
   sudo systemctl start depthfusion-mcp
   sudo systemctl status depthfusion-api
   ```

5. **Run V1 smoke tests:**
   ```bash
   # Verify login, search, and sync still work
   curl -X POST https://depthfusion.internal/auth/oidc \
     -H "Content-Type: application/json" \
     -d "{...test device code flow...}"
   
   # Or run integration tests on main
   pytest tests/test_integration/ -k "test_e2e_signin" -v
   ```

6. **Document the rollback in BACKLOG.md:**
   - File a P0 incident task explaining why rollback was needed
   - Schedule root-cause analysis
   - Plan remediation for the issue before attempting G4 re-evaluation

**Verification Post-Rollback:**

```bash
# Confirm V1 services are running
ps aux | grep depthfusion

# Confirm database is on pre-V2 schema
psql -U depthfusion -d depthfusion -c "\d acl_allow"
# Expected: ERROR: relation "acl_allow" does not exist (V1 schema does not have ACL columns yet)

# Confirm v2.0.0 tag is removed (if not already)
git tag -d v2.0.0
git push origin :refs/tags/v2.0.0
```

---

### Detailed Production Migration Procedure (from S-203, G4 C2)

The following procedure is extracted from `docs/migration-guide-v2.md` and documents the forward migration path (for reference during rollback planning).

**Migration Checklist:**

1. **Pre-migration**
   - [ ] Backup production database: `pg_dump depthfusion | gzip > /backups/depthfusion-pre-v2-$(date +%Y-%m-%d).sql.gz`
   - [ ] Test backup restoration on a clone
   - [ ] Announce maintenance window to users
   - [ ] Stop all sync clients (`sync.sh` already frozen; confirm no devices attempting sync)

2. **Migration**
   - [ ] Check out v2.0.0 tag on production VPS
   - [ ] Run dry-run: `depthfusion migrate v2 --dry-run`
   - [ ] Review planned changes
   - [ ] Run migration: `depthfusion migrate v2`
   - [ ] Verify migration log for errors

3. **Post-migration**
   - [ ] Start V2 services: `sudo systemctl start depthfusion-api depthfusion-sync-hub depthfusion-mcp`
   - [ ] Run smoke tests: sign-in, search, offline cache, export
   - [ ] Confirm all devices sync successfully to V2 hub
   - [ ] Monitor for error logs

4. **Rollback (if needed)**
   - [ ] Stop V2 services
   - [ ] Restore pre-migration database snapshot
   - [ ] Check out v1 tag and restart V1 services
   - [ ] File incident and schedule root-cause analysis

---

## Part E: Task Completion Marker

This section marks T-698 as complete in the backlog.

**Task:** T-698 (in S-205: Merge-gate review, S-205 AC-3)

**Status:** Will be marked [x] once this document is committed and reviewed.

**Verification Command:**

```bash
grep -c "T-698" /home/gregmorris/projects/depthfusion/BACKLOG.md
# Expected: 1 (the task appears once, marked [x])

# Verify the four gates are mentioned:
grep -E "G1|G2|G3|G4" /home/gregmorris/projects/depthfusion/docs/decisions/v2-merge-gate-checklist.md | wc -l
# Expected: ≥ 20 (gates appear multiple times throughout the checklist)
```

---

## Appendix: Reference Links

| Document | Purpose |
|----------|---------|
| `docs/plans/G1-gate.md` | Phase 1 → Phase 2 gate (PASS) |
| `docs/plans/G2-gate.md` | Phase 2 → Phase 3 gate (PENDING) |
| `docs/plans/G3-gate.md` | Phase 3 → Phase 4 gate (PENDING) |
| `docs/plans/G4-gate.md` | Phase 4 → Merge gate (PENDING) |
| `docs/v2/merge-plan.md` | Lane merge sequence and CI requirements |
| `docs/migration-guide-v2.md` | V1 → V2 production migration (required by G4 C2) |
| `docs/decisions/V2-DEC-001.md` | DoS mitigation (expression_eval.py guards) |
| `docs/decisions/ADR-S199-internal-pentest-plan.md` | Pen-test attack vectors and findings |
| `docs/decisions/pentest-findings-S199.md` | Detailed pen-test findings and remediations |
| `CHANGELOG.md` | Release notes for v2.0.0 |
| `BACKLOG.md` | Stories and tasks for E-49 through E-63 |

---

**Document Status:** Draft (awaiting gate completions G2-G4)  
**Last Updated:** 2026-06-18  
**Author:** Digittal Method V2 (Haiku dev, GLM review)
