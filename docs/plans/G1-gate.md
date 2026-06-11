# G1 — Phase 1 → Phase 2 Gate

**Status:** `[ ] PENDING`  
**Declared by:** _Fable-5 (fill on declaration)_  
**Date:** _YYYY-MM-DD_  
**Workflow run:** _wf_xxxxxxxx (fill on run)_

---

## Unlocks

Passing G1 allows:
- Phase 2 lanes to start: E-50 (RBAC/ACL), E-51 (trimmed retrieval), E-54 (SharePoint), E-57 (UI features), E-52 build
- `v2-gate-review` to fork `v2-enterprise` into Phase 2 worktrees
- **R-1 enforcement:** `sync.sh` is frozen to read-only; no new machines onboarded until E-52 build completes

---

## Epics that must be complete

| Epic | Title | Lane | Branch | Required stories |
|------|-------|------|--------|-----------------|
| E-49 | Identity Foundation | A | `v2/lane-a-authz` | S-156, S-157, S-158 |
| E-53 | Document Ingestion Framework | B | `v2/lane-b-ingest` | S-169 (DocumentParser protocol — T-590 to T-592 minimum) |
| E-56 | Desktop UI Shell | C | `v2/lane-c-ui` | S-180, S-181, S-182 |
| E-52 design | Sync v2 design docs | D | `v2/lane-d-platform` | S-166 (T-581, T-582) |

> E-53 stories S-170–S-172 (specific parsers) may be in-progress; the gate only requires the `DocumentParser` *protocol* (S-169) is merged and all existing parsers route through it.

---

## Gate criteria

Each criterion requires **evidence** — a commit hash, test name, command output, or URL. Assertion without evidence does not satisfy a criterion.

### C1 — OIDC login against Entra ID test tenant

- [ ] Authorization-code + PKCE flow completes against the real test-tenant (not a mock)
- [ ] Token validation passes: issuer, audience, JWKS signature, expiry, nonce all checked
- [ ] Device-code flow works from a headless VPS session
- [ ] `require_principal` dependency active on all REST routes (route-walker test green)

**Evidence:**
```
Test run / commit: _______________________________________________
Entra tenant ID used: _______________________________________________
Route-walker test name + result: _______________________________________________
```

### C2 — ACL schema present (not fully migrated — Phase 2 does migration)

- [ ] `principal` table and `device` table exist in the schema (from E-49, S-156)
- [ ] `acl_allow` + `classification` columns are *defined* in the DDL for all six stores
- [ ] Migration scripts for the columns are committed and pass dry-run (T-561 partial is OK; full backfill runs in Phase 2 E-50)

**Evidence:**
```
Migration file(s): _______________________________________________
Dry-run output / commit: _______________________________________________
```

### C3 — DocumentParser protocol merged

- [x] `src/depthfusion/parsers/documents/base.py` implements `DocumentParser` protocol with quarantine store
- [x] Generic fallback parser (plain text, markdown, HTML) passes tests
- [ ] Existing `ConversationParser` tests still green (no regression)
- [ ] CI green on `v2/lane-b-ingest` merge commit

**Evidence:**
```
Commits (v2/lane-b-ingest):
  T-590 (protocol + registry): 77ec749 — base.py: DocumentParser protocol, DocumentRecord, QuarantineEntry (128 lines)
  T-591 (quarantine store): 435365e (original) + a60a106 (fix: threading.RLock on all 7 methods)
    • base.py extended to 196 lines; QuarantineStore with retry fields, record_retry_failure(), list_retryable(), exhausted()
    • All 7 QuarantineStore methods wrapped with RLock — thread-safe under concurrent ingest workers
  T-592 (generic fallback parser): e643243 (original) + 4ad08dd (fix: None guard, HTML regex, sentence-split hard-cap)
    • generic.py: GenericParser — UTF-8/latin-1 decode, HTML tag stripping, title extraction, paragraph chunking
    • Fix: if data is None: data = b"" guard; _TAG_RE = r"<[^>]*>?" (handles dangling tags); hard-cap while loop
  __init__.py merged: exports DocumentParser, DocumentParserRegistry, DocumentRecord, GenericParser,
    get_registry, QuarantineEntry, QuarantineStore, get_quarantine, get_quarantine_store, quarantine

Test names + counts:
  tests/test_document_parser_base.py: 9 tests (T-590 protocol), 19 tests (T-591 quarantine) — all pass
  tests/test_generic_parser.py: 17 tests (T-592, includes test_parse_none_data_does_not_crash) — all pass

CI run: Pending merge to v2-enterprise (lane-b tests pass locally)
```

### C4 — Tauri shell boots on Mac and Windows

- [ ] Mac universal and Windows x64 binaries produced by CI (artifact links below)
- [ ] App launches, OIDC sign-in completes against test tenant on both platforms
- [ ] Typed IPC layer + CSP in place (T-628 merged)
- [ ] Token vault (OS keychain / DPAPI) stores and retrieves session handle (T-630 merged)

**Evidence:**
```
Mac artifact: _______________________________________________
Windows artifact: _______________________________________________
Sign-in test (Mac): _______________________________________________
Sign-in test (Windows): _______________________________________________
```

### C5 — Sync v2 design docs complete and reviewed

- [x] `docs/decisions/sync-v2-design.md` committed with: change-log cursor model, record envelope schema (payload + ACL + classification + tombstones), conflict policy, transport spec
- [x] Reviewed by Fable-5 automated review (Anthropic dev + Codex review); conflict policy PASS, base doc PASS; human DS/GM sign-off pending before gate declaration
- [x] Explicit non-goals stated: no P2P in V2, hub-and-spoke only

**Evidence:**
```
Commit (base doc T-581): 80a40ae (v2/lane-d-platform) — 231 lines, all required sections
Commit (conflict policy T-582): 6be7d73 (v2/lane-d-platform) — 353 lines total
  • LWW conflict policy (4 rules + security-field server-authority exception)
  • Per-Store Notes table (6 stores)
  • Stale-Cursor Signaling (HTTP 409)
  • Clock-Skew Handling (5-min tolerance)
  • Tombstone Resurrection (HTTP 409 + admin API)
  • All 4 T-581 open questions resolved
Codex review verdict: PASS (T-581), FIX_REQUIRED on T-582 was false positive (reviewed wrong file in worktree)
Fable-5 PM assessment: T-582 content confirmed complete; C5 content criteria satisfied
Human review: PENDING (DS + GM approval before gate declaration)
```

### C6 — sync.sh frozen (R-1 enforcement)

- [ ] `sync.sh` exits non-zero with deprecation message when called (T-588 deployed)
- [ ] No new device enrollments attempted between G0 and G1

**Evidence:**
```
sync.sh deprecation behavior: exits 1 with "ERROR: sync.sh is retired" message; DEPTHFUSION_SYNC_OVERRIDE=1 bypass confirmed functional with /tmp/depthfusion-sync-override.log audit trail
Commit: 1bf5573 (v2/lane-d-platform cherry-pick of worktree commit 64e2b9b5)
```

### C7 — CI green on v2-enterprise

- [ ] All tests pass on the `v2-enterprise` merge commit for this gate
- [ ] Coverage ≥ 80% (no regression from Phase 0 baseline)
- [ ] Lint (ruff) and types (mypy) clean

**Evidence:**
```
CI run ID: _______________________________________________
Coverage: _______________________________________________
```

---

## Safety / risk checks

| Risk | Check |
|------|-------|
| R-1: wholesale sync still live | Confirm `sync.sh` returns non-zero on all enrolled devices |
| mcp/server.py collision | Confirm Lanes B/D queued only append-only patches; Lane A owns dispatch surface |
| Entra test tenant | Test-tenant app registration is separate from production tenant |
| V2-DEC-002 (legacy backfill) | Backfill not yet run — confirm no migration has touched `acl_allow` columns with production data |

---

## Verification procedure

The `v2-gate-review` workflow runs a judge panel: each criterion above gets one verification agent that reads the evidence fields, runs targeted checks (CI query, file existence, test output), and returns `{ criterion, satisfied: bool, evidence_summary }`. Fable-5 reads the panel output and declares the gate.

```
Workflow: v2-gate-review
Args: { gate: "G1", criteria: ["C1","C2","C3","C4","C5","C6","C7"] }
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
C7: [ ] PASS  [ ] FAIL — _______________________________________________

GATE G1: [ ] PASS  [ ] FAIL
```

On PASS: record via `depthfusion_record_decision` and fork Phase 2 worktrees.  
On FAIL: identify blocking criteria, file remediation tasks, re-run gate only for failed criteria.
