# G0 — Phase 0 Bootstrap → Phase 1 Gate

**Status:** `[x] PASSED`  
**Declared by:** Fable-5  
**Date:** 2026-06-10  
**Workflow run:** `g0-approve-dry-run-wf_299c377e-06d` (approve path); `we5cye6le` (forced-split path)

---

## Unlocks

Passing G0 allowed:
- Phase 1 lanes to start: E-49 (Lane A), E-52 design (Lane D), E-53 (Lane B), E-56 (Lane C)
- `v2-consensus-ticket.js` to be used for real development tickets
- Lane-halt mechanism active (V2-DEC-001 governing `expression_eval.py`)

---

## Epics that must be complete

| Epic | Title | Lane | Branch | Required stories |
|------|-------|------|--------|-----------------|
| E-48 | Phase 0 Bootstrap | all | `v2-enterprise` | S-153, S-154, S-155 |

---

## Gate criteria

### C1 — CI green on v2-enterprise

- [x] All tests pass on `v2-enterprise`
- [x] Coverage ≥ 80%
- [x] Lint (ruff) and types (mypy) clean
- [x] macOS Apple Silicon routing and Windows `fcntl` failures resolved

**Evidence:**
```
CI commits: c02bd32 (coverage floor), 4e469ca (hnswlib/domain tests),
            89e3976 + a75e438 (macOS/Windows routing fixes)
Test result: 2237 passed, 32 warnings in 277.25s
Coverage: ≥80% (enforced via pytest-cov floor in pyproject.toml)
```

### C2 — v2-consensus-ticket.js routing config committed and reviewed

- [x] `.claude/v2-routing.yaml` committed — canonical work-class → dev/reviewer/tiebreak model mapping
- [x] Routing config covers all five work classes: `security-critical`, `multi-file-feature`, `targeted-fix`, `tests-docs`, `design-doc`
- [x] `review-deepseek.sh` and `review-gemini.sh` conformance verified; HIGH finding F1 (`set -euo pipefail` + missing `|| true`) fixed
- [x] Routing config included in `.gitignore` exception (`!.claude/v2-routing.yaml`)

**Evidence:**
```
Commit: 6376646 (routing YAML + reviewer fixes)
File: .claude/v2-routing.yaml (52 lines)
Reviewer conformance: docs/decisions/T-539-smoke-test.md (both CLIs return
                      {verdict, findings} JSON; deepseek and gemini confirmed conforming)
```

### C3 — Vendor isolation rule validated

- [x] At least one dry-run ticket completed with Anthropic dev → non-Anthropic review (codex-spot)
- [x] Routing config enforces cross-vendor pairs for all work classes
- [x] No work class has the same vendor for dev and reviewer

**Evidence:**
```
Approve-path dry run: DRY-RUN-G0-APPROVE — dev: haiku, reviewer: codex-spot
                      Commit: 2b7872a, verdict: 'approve', findings: []
T-539 smoke test: docs/decisions/T-539-smoke-test.md
```

### C4a — Consensus workflow approve path exercised end-to-end

- [x] `v2-consensus-ticket.js` executed a full Dev → Review → (no rebuttal) → `status: 'approved'` cycle
- [x] All three agent phases callable (even if rebuttal was short-circuited on no-objections)
- [x] Dry-run artifact committed to `docs/decisions/`

**Evidence:**
```
Workflow: g0-approve-dry-run-wf_299c377e-06d
Artifact: docs/decisions/DRY-RUN-G0-APPROVE.md (commit 0507d65)
Status: approved (immediate — reviewer returned findings: [])
Dev commit: 2b7872a
```

### C4b — Consensus workflow forced-split path exercised end-to-end

- [x] `v2-consensus-ticket.js` executed a full Dev → Review → Rebuttal → `status: 'split'` cycle
- [x] Tiebreak advisory called via `depthfusion_bridge`; lean: reviewers
- [x] V2-DEC-NNN decision file committed to `docs/decisions/`
- [x] Lane-halt mechanism activated: `expression_eval.py` tickets blocked until `resolved: true`

**Evidence:**
```
Workflow: we5cye6le
Artifact: docs/decisions/V2-DEC-001.md (commit 6376646)
Status: SPLIT (both reviewers maintained objections after rebuttal)
Tiebreak lean: reviewers (DoS findings are real; do not defer)
Rebuttal: docs/decisions/rebuttal-DRY-RUN-G0-SPLIT-2.md
Findings: F1–F3, F5–F6 in expression_eval.py and reviewer scripts
```

### C5 — Cost ledger seeded

- [x] `v2-cost.jsonl` seeded with Phase 0 dry-run entries via `log-cost.sh`
- [x] At least 5 cost entries covering DRY-RUN-G0-SPLIT-2 phases

**Evidence:**
```
Commit: 6376646 (v2-cost.jsonl seeded with 5 entries)
```

---

## Safety / risk checks at G0

| Risk | Check | Result |
|------|-------|--------|
| `v2-consensus-ticket.js` untested code paths | Both approve and forced-split paths exercised by dry runs | ✅ Both paths covered |
| Vendor isolation violated | Routing YAML reviewed; no same-vendor dev+review pair | ✅ Cross-vendor enforced |
| DoS surface in expression_eval.py | Tiebreak advisory confirmed findings real; lane-halt activated | ✅ Lane-halt active (V2-DEC-001) |
| CI baseline < 80% | Coverage floor enforced in CI; macOS/Windows failures fixed | ✅ ≥80% on all platforms |

---

## Verification procedure (historical — gate already PASSED)

```
Workflow: v2-gate-review
Args: { gate: "G0", criteria: ["C1","C2","C3","C4a","C4b","C5"] }
```

---

## Verdict (filled — PASSED 2026-06-10)

```
C1: [x] PASS     CI 2237 passed, coverage ≥80%, ruff+mypy clean
C2: [x] PASS     .claude/v2-routing.yaml committed (6376646); reviewer scripts fixed
C3: [x] PASS     Cross-vendor validated via T-539 smoke test + DRY-RUN-G0-APPROVE
C4a:[x] PASS     Approve path: g0-approve-dry-run-wf_299c377e-06d → status='approved'
C4b:[x] PASS     Forced-split path: we5cye6le → status='SPLIT', V2-DEC-001.md created
C5: [x] PASS     v2-cost.jsonl seeded (5 entries)

GATE G0: [x] PASS
```

Phase 1 lanes (E-49, E-52 design, E-53, E-56) unblocked on 2026-06-10.

---

*G0 was the bootstrap gate — it establishes the build machinery, not product functionality. Its primary purpose was proving the `v2-consensus-ticket.js` pipeline is trustworthy enough to govern Phase 1–4 development decisions.*
