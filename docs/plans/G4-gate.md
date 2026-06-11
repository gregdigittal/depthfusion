# G4 — Phase 4 → Merge to main Gate

**Status:** `[ ] PENDING`  
**Declared by:** _Fable-5 (fill on declaration)_  
**Date:** _YYYY-MM-DD_  
**Workflow run:** _wf_xxxxxxxx (fill on run)_

---

## Unlocks

Passing G4 allows:
- `v2-enterprise` branch merged to `main`
- `v2.0.0` release tag applied
- Migration guide published and VPS production migration executed
- External pilot access promoted to general availability

---

## Epics that must be complete

| Epic | Title | Lane | Branch | Required stories |
|------|-------|------|--------|-----------------|
| E-61 | Performance + Pen-Test | D | `v2/lane-d-platform` | S-198, S-199, S-200, S-201 (all) |
| E-62 | Documentation + Migration Guide | D | `v2/lane-d-platform` | S-202, S-203, S-204 |
| E-63 | Integration Validation + Pilot + Merge | all | `v2-enterprise` | S-205, S-206 |

---

## Gate criteria

Each criterion requires **evidence** — a commit hash, test name, command output, or URL. Assertion without evidence does not satisfy a criterion.

### C1 — Full E-61 security suite green, including penetration check findings addressed

- [ ] All automated security tests in the E-61 suite pass (unit, integration, adversarial ACL, DoS surface)
- [ ] Penetration test engagement completed; report received and reviewed
- [ ] All penetration test findings severity HIGH or CRITICAL remediated and re-verified
- [ ] V2-DEC-001 (expression_eval.py DoS): confirmed `resolved: true`; the four guards (F2/F3/F5/F6) are present and tested
- [ ] No OWASP Top 10 items outstanding as unmitigated (accepted risk must be documented with ADR)

**Evidence:**
```
E-61 test run: _______________________________________________
Pen-test firm / date: _______________________________________________
Pen-test report ID or reference: _______________________________________________
All-findings resolution table: _______________________________________________
V2-DEC-001 resolution commit: _______________________________________________
```

### C2 — Migration guide validated on VPS clone

- [ ] A full clone of the production VPS (separate host or snapshot) was provisioned
- [ ] Migration guide executed verbatim on the clone by someone other than the author
- [ ] Migration completes without manual intervention or undocumented steps
- [ ] Post-migration smoke test passes on the clone (sign-in, search, sync cycle)
- [ ] Rollback procedure documented and tested on the clone

**Evidence:**
```
Clone hostname / snapshot ID: _______________________________________________
Migration executor (not author): _______________________________________________
Smoke test result on clone: _______________________________________________
Rollback test result: _______________________________________________
```

### C3 — Pilot validation complete

- [ ] At least one non-developer user completed a full workflow on the pilot tenant (sign-in → search → offline → export)
- [ ] No P0 or P1 issues raised during the pilot period remain open
- [ ] Pilot feedback collected and triaged; P2/P3 items ticketed in BACKLOG.md for post-v2.0.0

**Evidence:**
```
Pilot participant count: _______________________________________________
Pilot issues P0/P1 open at gate: _______________________________________________
Pilot feedback triage commit: _______________________________________________
```

### C4 — v2.0.0 tag criteria met

- [ ] `CHANGELOG.md` updated with all V2 changes grouped by epic
- [ ] `docs/migration-guide-v2.md` committed and covers: prerequisites, step-by-step migration, rollback, known issues
- [ ] All V2 decision ADRs committed to `docs/decisions/` (V2-DEC-001 through latest)
- [ ] Git tag `v2.0.0` produced on the merge commit (not pre-merge)

**Evidence:**
```
CHANGELOG commit: _______________________________________________
Migration guide commit: _______________________________________________
Decision ADR count in docs/decisions/: _______________________________________________
Tag commit (populated post-merge): _______________________________________________
```

### C5 — Full test suite green on v2-enterprise pre-merge commit

- [ ] All tests pass: unit, integration, E2E, security, ACL leak, offline cache, export controls, BI security
- [ ] Coverage ≥ 80% (no regression)
- [ ] Lint (ruff) and types (mypy) clean
- [ ] No skipped tests that were passing in v1

**Evidence:**
```
CI run ID (pre-merge): _______________________________________________
Coverage: _______________________________________________
Skipped test delta from v1: _______________________________________________
```

### C6 — No P0 defects open; P1 defects accepted or resolved

- [ ] Zero open P0 issues at gate declaration time
- [ ] All open P1 issues either resolved or have explicit acceptance ADR signed by Fable-5
- [ ] V2-DEC-001 lane-halt lifted (resolved: true or superseded)

**Evidence:**
```
Open issue count P0: _______________________________________________
Open issue count P1 (with disposition): _______________________________________________
V2-DEC-001 resolution: _______________________________________________
```

---

## Safety / risk checks

| Risk | Check |
|------|-------|
| Production VPS data loss on migration | Confirm backup taken and verified (not assumed) before migration; rollback tested on clone first |
| ACL regression in merge | Run `test_acl_leak` suite on the post-merge `main` commit before cutting the tag |
| Tag on wrong commit | Confirm `v2.0.0` tag points to the merge commit SHA, not a branch tip |
| Pen-test findings deferred inappropriately | All HIGH/CRITICAL pen-test findings must have either a remediation commit or a signed accepted-risk ADR — no silent deferrals |
| Pilot data in main | Confirm no pilot-specific test data or credentials are committed to `main`; run `git grep -i 'pilot\|test-tenant\|testuser'` before merge |

---

## Merge procedure (post-PASS only)

1. `v2-gate-review` emits PASS verdict → Fable-5 records decision via `depthfusion_record_decision`
2. Final CI run on `v2-enterprise` HEAD
3. Merge `v2-enterprise` → `main` (merge commit, not squash — preserve history)
4. Run `test_acl_leak` suite on `main` immediately post-merge
5. Apply tag: `git tag -a v2.0.0 -m "DepthFusion V2 Enterprise: OIDC + ACL + SharePoint + Offline"`
6. Push tag: `git push origin v2.0.0`
7. Execute production VPS migration per `docs/migration-guide-v2.md`
8. Post-migration smoke test on production
9. Publish release notes

---

## Verification procedure

```
Workflow: v2-gate-review
Args: { gate: "G4", criteria: ["C1","C2","C3","C4","C5","C6"] }
```

---

## Verdict

```
C1: [ ] PASS  [ ] FAIL — _______________________________________________
C2: [ ] PASS  [ ] FAIL — _______________________________________________
C3: [ ] PASS  [ ] FAIL — _______________________________________________
C4: [ ] PASS  [ ] FAIL — _______________________________________________
C5: [ ] PASS  [ ] FAIL — _______________________________________________
C6: [ ] PASS  [ ] FAIL — _______________________________________________

GATE G4: [ ] PASS  [ ] FAIL
```

On PASS: proceed with merge procedure above. `v2.0.0` is live.  
On FAIL: identify blocking criteria, file remediation tasks. Re-run gate only for failed criteria. Do NOT merge until all criteria pass.

---

*This is the final gate. There is no gate after G4. Once this file records PASS and the merge procedure is complete, DepthFusion V2 Enterprise is in production.*
