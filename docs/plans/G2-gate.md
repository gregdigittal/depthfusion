# G2 — Phase 2 → Phase 3 Gate

**Status:** `[ ] PENDING`  
**Declared by:** _Fable-5 (fill on declaration)_  
**Date:** _YYYY-MM-DD_  
**Workflow run:** _wf_xxxxxxxx (fill on run)_

---

## Unlocks

Passing G2 allows:
- Phase 3 lanes to start: E-58 (offline cache), E-59 (export controls), E-55 (BI endpoints), E-60 (audit/compliance)
- Sync v2 live traffic to begin (first pilot site onboarded after C2 passes)
- Offline-first features to be scheduled

---

## Epics that must be complete

| Epic | Title | Lane | Branch | Required stories |
|------|-------|------|--------|-----------------|
| E-50 | RBAC / ACL Enforcement | A | `v2/lane-a-authz` | S-159, S-160, S-161, S-162 |
| E-51 | Security-Trimmed Retrieval | A | `v2/lane-a-authz` | S-163, S-164, S-165 |
| E-54 | SharePoint Graph Integration | B | `v2/lane-b-ingest` | S-173, S-174, S-175, S-176 |
| E-57 | UI Search & Recall | C | `v2/lane-c-ui` | S-183, S-184, S-185 |
| E-52 build | Sync v2 build | D | `v2/lane-d-platform` | S-167, S-168 (T-583–T-588) |

---

## Gate criteria

Each criterion requires **evidence** — a commit hash, test name, command output, or URL. Assertion without evidence does not satisfy a criterion.

### C1 — SharePoint delta sync ingests pilot site with ACL fidelity

- [ ] Delta sync connector processes a real pilot SharePoint site (not mock data)
- [ ] File-level `acl_allow` populated from Graph API permissions for at least 10 pilot documents
- [ ] Delta token persisted; re-run fetches only changed items
- [ ] No files appear in retrieval results for principals not in their `acl_allow` (verified by ACL leak test)

**Evidence:**
```
Pilot site URL (internal only, no credentials): _______________________________________________
Ingest run output / commit: _______________________________________________
Delta token persistence test: _______________________________________________
ACL fidelity spot-check (doc count, sample principal): _______________________________________________
```

### C2 — Security-trimmed query API passes ACL leak test-suite

- [ ] `test_acl_leak` suite (E-51, S-163) passes with 0 failures
- [ ] `security_trim` filter applied at the retrieval layer, not post-retrieval
- [ ] At least one cross-principal isolation test included: User A cannot see User B's classified document
- [ ] API endpoint returns 403 / empty result set (not 200 + empty body) for unauthorised queries

**Evidence:**
```
Test run: _______________________________________________
Test names covered: _______________________________________________
Cross-principal test name + result: _______________________________________________
403 vs 200 behavior confirmed by: _______________________________________________
```

### C3 — UI search works online (Tauri + VPS backend)

- [ ] Search query from Tauri UI reaches the query API on the VPS over mTLS
- [ ] Results rendered in the result pane with `classification` badge visible
- [ ] "No results" state renders correctly (not a blank screen or JS error)
- [ ] Sign-in → search → result round-trip completes in < 3 s on LAN (measured, not estimated)

**Evidence:**
```
Screenshot / recording: _______________________________________________
Round-trip timing: _______________________________________________
mTLS cert used: _______________________________________________
```

### C4 — ACL backfill complete (V2-DEC-002 enforced)

- [ ] All pre-V2 documents have `acl_allow` set to at minimum `[greg]` (owner-only, per V2-DEC-002)
- [ ] No document in any store has a `null` or empty `acl_allow` field after backfill
- [ ] Backfill idempotent: re-running produces no changes (second run diff is empty)

**Evidence:**
```
Backfill migration commit: _______________________________________________
Idempotency test: _______________________________________________
Null-check query result: _______________________________________________
```

### C5 — Sync v2 live (R-1 clean handoff complete)

- [ ] `sync.sh` is permanently disabled (returns non-zero, T-588)
- [ ] Sync v2 hub process running on VPS, accepting connections from at least one device
- [ ] At least one successful sync cycle completed end-to-end (device → hub → store)
- [ ] No orphaned sync.sh cron entries on any enrolled device

**Evidence:**
```
Sync v2 startup log: _______________________________________________
First successful sync cycle log: _______________________________________________
sync.sh disabled verification: _______________________________________________
```

### C6 — CI green on v2-enterprise

- [ ] All tests pass on the `v2-enterprise` merge commit for this gate
- [ ] `test_acl_leak` and `test_security_trim` suites explicitly pass (not skipped)
- [ ] Coverage ≥ 80%

**Evidence:**
```
CI run ID: _______________________________________________
Coverage: _______________________________________________
```

---

## Safety / risk checks

| Risk | Check |
|------|-------|
| ACL leak regression | Confirm `test_acl_leak` runs in every CI build from this gate forward |
| Pilot site data exposure | Verify pilot SharePoint app registration has minimum-permission scopes (Files.Read.All only, no Mail/Calendar) |
| V2-DEC-001 (expression_eval.py DoS) | Confirm F2/F3/F5/F6 fixes are in before any query endpoint touches expression_eval.py |
| sync.sh cron removal | Check all enrolled devices: `crontab -l \| grep sync.sh` returns nothing |
| Classification label leakage | Confirm `classification` metadata is stripped from API responses when the requester's clearance is below the document's classification |

---

## Verification procedure

```
Workflow: v2-gate-review
Args: { gate: "G2", criteria: ["C1","C2","C3","C4","C5","C6"] }
```

Each criterion agent reads evidence fields, runs targeted checks (test runner query, API probe, file existence), returns `{ criterion, satisfied: bool, evidence_summary }`.

---

## Verdict

```
C1: [ ] PASS  [ ] FAIL — _______________________________________________
C2: [ ] PASS  [ ] FAIL — _______________________________________________
C3: [ ] PASS  [ ] FAIL — _______________________________________________
C4: [ ] PASS  [ ] FAIL — _______________________________________________
C5: [ ] PASS  [ ] FAIL — _______________________________________________
C6: [ ] PASS  [ ] FAIL — _______________________________________________

GATE G2: [ ] PASS  [ ] FAIL
```

On PASS: record via `depthfusion_record_decision` and begin Phase 3 work.  
On FAIL: identify blocking criteria, file remediation tasks, re-run gate only for failed criteria.
