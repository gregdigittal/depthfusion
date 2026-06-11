# G3 — Phase 3 → Phase 4 Gate

**Status:** `[ ] PENDING`  
**Declared by:** _Fable-5 (fill on declaration)_  
**Date:** _YYYY-MM-DD_  
**Workflow run:** _wf_xxxxxxxx (fill on run)_

---

## Unlocks

Passing G3 allows:
- Phase 4 work to start: E-61 (performance + security pen-test), E-62 (docs + migration guide), E-63 (integration validation + pilot + merge)
- External pilot participants to be onboarded
- Penetration test engagement to be scheduled

---

## Epics that must be complete

| Epic | Title | Lane | Branch | Required stories |
|------|-------|------|--------|-----------------|
| E-58 | Offline Cache | C | `v2/lane-c-ui` | S-186, S-187, S-188 |
| E-59 | Export & Compliance Controls | C | `v2/lane-c-ui` | S-189, S-190, S-191 |
| E-55 | BI Connector | B | `v2/lane-b-ingest` | S-177, S-178, S-179 |
| E-60 | Audit Log & Compliance | A | `v2/lane-a-authz` | S-192, S-193, S-194 |

---

## Gate criteria

Each criterion requires **evidence** — a commit hash, test name, command output, or URL. Assertion without evidence does not satisfy a criterion.

### C1 — Offline cache demonstrated end-to-end

- [ ] Tauri app caches a result set (at least 50 documents) while online
- [ ] Network interface disabled on test machine; app still serves cached search results
- [ ] Cache invalidation fires correctly when a document is deleted from SharePoint (delta sync on reconnect)
- [ ] Cache size stays within configured limit (default 500 MB); LRU eviction triggered when limit approached

**Evidence:**
```
Demo recording / screenshot: _______________________________________________
Offline test method (e.g. "disabled WiFi adapter, ran X query"): _______________________________________________
Cache invalidation test: _______________________________________________
LRU eviction test: _______________________________________________
```

### C2 — Export controls active

- [ ] "Export to file" action on a document with `classification: CONFIDENTIAL` is blocked for users without export permission
- [ ] Export produces a properly formatted output for users with permission (PDF or DOCX with classification header/footer)
- [ ] Export audit event written to the audit log (C4 dependency)
- [ ] DLP-style regex rules configurable; at least one test rule blocks export of SSN-containing documents

**Evidence:**
```
Export-block test: _______________________________________________
Export-permit test (output format): _______________________________________________
Audit event for export: _______________________________________________
DLP rule test: _______________________________________________
```

### C3 — BI endpoints security-trimmed

- [ ] All BI connector query paths apply the same `security_trim` filter as the main query API
- [ ] A principal with no permissions gets empty results (not 500 or full dataset) from all BI endpoints
- [ ] Power BI / Excel add-in connects to the endpoint using service principal with least-privilege scopes
- [ ] BI endpoint response does not include `acl_allow` or raw classification metadata (stripped at serialisation layer)

**Evidence:**
```
BI endpoint ACL test run: _______________________________________________
Service principal scopes used: _______________________________________________
Serialisation strip test: _______________________________________________
```

### C4 — Audit log active and queryable

- [ ] `audit_log` table populated with events for: sign-in, search, export, ACL change, sync cycle
- [ ] Audit records immutable (no UPDATE/DELETE on the table from application code)
- [ ] Admin UI or CLI can query audit log by principal, event type, date range
- [ ] Audit records include: timestamp (UTC), principal, device, event type, resource, classification

**Evidence:**
```
Audit log schema commit: _______________________________________________
Immutability constraint test: _______________________________________________
Sample audit query output: _______________________________________________
```

### C5 — Zero P0 defects open

- [ ] No open issues labelled `P0` in the tracker at gate declaration time
- [ ] Any P0 that was open during Phase 3 is resolved and verified (not just closed)
- [ ] V2-DEC-001 (expression_eval.py DoS) resolved: `resolved: true` set or superseded decision file exists

**Evidence:**
```
Open issue count at gate: _______________________________________________
V2-DEC-001 resolution commit / superseding decision: _______________________________________________
```

### C6 — CI green on v2-enterprise

- [ ] All tests pass on the `v2-enterprise` merge commit for this gate
- [ ] ACL leak suite, offline cache suite, export controls suite, BI security suite all pass explicitly
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
| Offline cache stale data | Confirm delta sync reconciliation runs within 60 s of reconnect |
| Export DRM bypass | Confirm exported files are not re-importable by an unpermissioned user (test round-trip) |
| BI credential leakage | Confirm BI service principal cannot be used to access SharePoint directly beyond what DepthFusion exposes |
| Audit log retention | Confirm audit records survive application restart (persisted, not in-memory) |
| V2-DEC-001 closure | If expression_eval.py DoS guards are not in by G3, block gate on C5 |

---

## Verification procedure

```
Workflow: v2-gate-review
Args: { gate: "G3", criteria: ["C1","C2","C3","C4","C5","C6"] }
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

GATE G3: [ ] PASS  [ ] FAIL
```

On PASS: record via `depthfusion_record_decision` and begin Phase 4 / pen-test engagement.  
On FAIL: identify blocking criteria, file remediation tasks, re-run gate only for failed criteria.
