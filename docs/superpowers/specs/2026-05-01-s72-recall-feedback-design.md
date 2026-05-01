# S-72 — Recall Feedback Loop: Design

- **Story**: E-27 / S-72 (P1, M)
- **Status**: design approved 2026-05-01; awaiting implementation plan
- **Depends on**: S-70 (importance/salience scalars) ✓ shipped 2026-05-01
- **Blocks**: nothing directly; S-71 and S-69 share the lock helper extracted here
- **Authors**: brainstormed via dual-LLM session 2026-05-01

## 1. Context and motivation

S-70 added `salience: float ∈ [0.0, 5.0]` to discovery markdown frontmatter and to `ContextItem` rows on the bus. Without S-72, salience is **stored but never mutated** — there's no signal flowing in to actually shift it from the canonical 1.0 default. The whole memory-policy layer (E-27) is built around the premise that `salience` reflects **recent usefulness**, but recent usefulness is invisible until the system observes which chunks a caller actually used after a recall.

S-72 closes that loop: every `recall_relevant` call mints a `recall_id`; the caller (an agent or operator) then submits a follow-up `recall_feedback` payload listing which chunks were `used` and which were `ignored`. The feedback applies bounded `salience` deltas to the corresponding discovery files.

Once S-72 lands, salience is a live, learning signal — and S-71 (decay buckets) becomes coherent (decay applies to a value that's actually been *bumped* by use).

## 2. Acceptance criteria (from BACKLOG.md)

- AC-1: `recall_relevant` response includes `recall_id` (uuid v4) per call.
- AC-2: A short-term store maps `recall_id → [chunk_id]` for at least 24 hours.
- AC-3: New MCP tool `depthfusion_recall_feedback(recall_id, used: chunk_id[], ignored: chunk_id[])` applies `salience += 0.1` per used and `-= 0.05` per ignored chunk.
- AC-4: Idempotent — replaying the same `recall_id + items` payload doesn't double-apply.
- AC-5: Salience changes are bounded (`max 5.0`, `min 0.0`).
- AC-6: ≥ 6 tests covering: id correlation, used/ignored signals, idempotency, bounds, expiry of unfetched recall_ids.

This design exceeds AC-6 with 12 tests (see §7).

## 3. Design decisions

Each decision below was a deliberate fork during the brainstorm; the rejected alternatives are documented for future reviewers who may want to revisit.

### 3.1 Where salience bumps land — discovery file frontmatter (RMW)

Bumps mutate the `salience` field in the discovery markdown frontmatter via the same atomic-rewrite pattern S-70 established for `_tool_set_memory_score` (`fcntl.LOCK_EX` on a sidecar `.scorelock`, then `mkstemp` + `fsync` + `os.replace`).

**Rejected alternatives**:
- Sidecar `~/.claude/shared/salience-boosts.jsonl` — works for all chunk sources, but inverts S-70's "salience lives on the discovery" premise and forces every recall to merge boosts at read time.
- Mutate ContextBus rows in addition to discovery files — complicates S-78's content-hash dedup invariant; a separate story if needed.

**Implication**: bumps to chunks whose source is `session_file`, `memory`, or `context_bus` are silently dropped (counted under `skipped_unsupported` in the response). This is acceptable: discoveries are the *learning corpus*, sessions and bus rows are transient by design.

### 3.2 Short-term store — in-memory dict + `threading.Lock`

`{recall_id: RecallEntry(ts, [chunk_ids], applied: set[chunk_id])}` held in a module-level singleton, mutated under a single `threading.Lock`.

**Rejected alternatives**:
- SQLite — overkill for 24h ephemeral coordination state; new dependency for tier-1.
- File-backed JSONL — file I/O on the recall hot path; another flock site to manage.

**Implication**: feedback for in-flight recalls is lost if the MCP server restarts. Acceptable per AC-2 ("at least 24 hours" — not "across restarts").

### 3.3 Best-effort with aggregate counts in feedback response

The tool returns `{ok, applied, skipped_unsupported, skipped_missing, skipped_already_applied, skipped_expired}`. Each `chunk_id` lands in exactly one bucket. The tool never raises on per-chunk problems.

**Rejected alternatives**:
- Strict mode (first unresolvable chunk_id raises) — bad for mixed-source recalls and partial-batch failures.
- Strict-on-malformed, soft-on-unsupported — fuzzy boundary between "garbage" and "unsupported," more complex response shape.

**Implication**: caller bugs that pass wrong `chunk_id` formats are masked. Mitigation: capture metrics for each skip bucket so silent skips remain observable.

### 3.4 Idempotency — `(recall_id, chunk_id)` applied-set

Each `RecallEntry` carries `applied: set[chunk_id]`. On feedback, chunks already in the set land in `skipped_already_applied`; new chunks apply and join the set.

**Rejected alternatives**:
- All-or-nothing per recall_id — loses partial-retry support after transient failures.
- Payload-hash equality — terrible ergonomics for streaming feedback (forces caller to send cumulative payloads).

**Implication**: idempotency state evicts with the recall_id at TTL — feedback arriving after eviction lands in `skipped_expired`, which is the correct visible-failure mode.

### 3.5 Lock helper extraction — `core/file_locking.py` ships with this commit

A new module `core/file_locking.py` exports `atomic_frontmatter_rewrite(path)` as a context manager. Both `_tool_set_memory_score` (refactored) and the new feedback path use it. S-71 and S-69 inherit it for free.

**Rejected alternatives**:
- Defer the extraction to S-71 — leaves S-72 with code duplicated from S-70; consensus reviewers will flag.
- Never extract — three+ copies of subtle filesystem dance is a maintenance trap.

**Implication**: S-70's `_tool_set_memory_score` is touched as part of this PR. The diff is small (~20 lines moved, identical semantics) and consensus review will verify behavioral equivalence.

### 3.6 Eviction — sweep-on-write

Every `recall_relevant` call sweeps stale entries (`now - ts > 24h`) before inserting the new `recall_id`. O(n) under the same lock as the insert; n is bounded by 24h of recall traffic.

**Rejected alternatives**:
- Lazy-only (evict on access) — orphan entries linger forever if never re-touched; honors AC-2 only by accident.
- Periodic background sweep via `threading.Timer` — adds thread lifecycle complexity to an otherwise-synchronous tool surface.

### 3.7 Bump magnitudes — hardcoded module constants

`USED_BOOST = 0.1`, `IGNORED_DECAY = 0.05` as module-level constants in `core/feedback.py`. Matches AC-3 literally.

**Rejected alternatives**:
- Env-configurable (`DEPTHFUSION_FEEDBACK_BOOST` / `_DECAY`) — useful symmetry with S-71's env surface, but env-var sprawl outpaces the value; deferrable follow-on if operators ask.
- Per-call args — pushes policy decision to caller; complicates idempotency contract.

## 4. Architecture

### 4.1 New modules

- **`src/depthfusion/core/file_locking.py`** — single public symbol: `atomic_frontmatter_rewrite(path: Path)` context manager. Yields a mutable `FrontmatterContext` with `.body: str` (current file contents) and `.set_score(importance: float | None, salience: float | None)`. On exit, splices via existing `_splice_memory_score_frontmatter`, writes via mkstemp + fsync + `os.replace`. Acquires `fcntl.LOCK_EX` on `<dir>/.<filename>.scorelock`.
- **`src/depthfusion/core/feedback.py`** — `RecallStore` singleton class. Public API:
  - `register_recall(chunk_ids: list[str]) -> str` — mints `recall_id`, sweeps stale, inserts, returns id
  - `apply_feedback(recall_id: str, used: list[str], ignored: list[str]) -> FeedbackResult` — orchestrates lookup, bucketing, batching, mutation
  - Module constants: `USED_BOOST`, `IGNORED_DECAY`, `RECALL_TTL_SECONDS = 86400`

### 4.2 Modified files

- **`src/depthfusion/mcp/server.py`**
  - `_tool_recall` (line ~294) — calls `RecallStore.register_recall(chunk_ids)`, adds `recall_id` to response dict
  - `_tool_set_memory_score` (line ~1000+) — refactored to use `atomic_frontmatter_rewrite` instead of inline lock+mkstemp
  - New `_tool_recall_feedback(arguments)` — validates payload, calls `RecallStore.apply_feedback`, returns JSON result
  - `TOOLS` dict, `_TOOL_FLAGS`, `_dispatch_tool` — register `depthfusion_recall_feedback` (always-on, no flag)
  - Tool count assertions in `tests/test_analyzer/test_mcp_server.py` bumped 14→15

### 4.3 Data flow — recall path

```
client → recall_relevant(query, ...)
       → _tool_recall builds blocks list with chunk_ids
       → RecallStore.register_recall([chunk_ids]) → recall_id
       → response includes {query, blocks, recall_id, ...}
```

### 4.4 Data flow — feedback path

```
client → recall_feedback(recall_id, used=[c1, c2, c3], ignored=[c4])
       → _tool_recall_feedback validates payload
       → RecallStore.apply_feedback(recall_id, used, ignored):
           1. Lookup recall_id under lock
              - missing/expired → all chunks → skipped_expired/missing
           2. For each chunk in (used + ignored):
              a. If in entry.applied → skipped_already_applied
              b. Else if chunk source not discovery (resolved via file existence) → skipped_unsupported
              c. Else if file missing/superseded → skipped_missing
              d. Else group by target file path
           3. For each unique target file:
              - delta = USED_BOOST * count_used - IGNORED_DECAY * count_ignored
              - with atomic_frontmatter_rewrite(file_path) as ctx:
                  current = extract_memory_score(ctx.body)
                  ctx.set_score(salience=current.salience + delta)
                  (clamping happens in MemoryScore.__post_init__)
              - On success: add the chunks to entry.applied (under store lock)
           4. Return FeedbackResult with bucket counts
       → JSON response
```

### 4.5 chunk_id → discovery file resolution

`chunk_id` format established by S-70 era: `{file_stem}#{i}` for multi-section files, `{file_stem}` for single-section. File path: `~/.claude/shared/discoveries/<file_stem>.md`. If absent, also check `.archive/<file_stem>.md` — if found there, the discovery is archived and bumps are silently dropped (`skipped_missing`). `.superseded` files are also skipped.

Cross-project: `file_stem` already includes the project slug (e.g. `2026-05-01-depthfusion-decisions`), so cross-project chunk_ids resolve to distinct files. No extra disambiguation needed.

## 5. Concurrency model

- **`RecallStore` lock** (a single `threading.Lock`) protects the recall_id dict — covers register, sweep, lookup, and applied-set updates.
- **File lock** (per-discovery `.scorelock` flock via `atomic_frontmatter_rewrite`) protects discovery file mutation. Same lock used by `_tool_set_memory_score`, S-71 (when it lands), and S-69 (when it lands).
- **Lock ordering**: `apply_feedback` releases the `RecallStore` lock before acquiring per-file locks (we don't hold the store lock across file I/O). After file mutations succeed, we re-acquire the store lock briefly to update `entry.applied`.

This avoids deadlock and minimizes contention on the in-memory store.

## 6. Error handling

- **Missing `recall_id`** → all chunks land in `skipped_expired` (or `skipped_missing` if recall_id was never minted — distinguishable by checking the dict before TTL filtering).
- **Malformed payload** (e.g. `recall_id` not a string, `used` not a list) → structured error: `{ok: false, error: "..."}` (consistent with `_tool_set_memory_score`'s validation pattern).
- **Lock acquisition** uses blocking `fcntl.LOCK_EX` (matches S-70). The only failure modes are `OSError` (lock file unwriteable, e.g. permission issue or filesystem error) and signal-interrupted system call. Both reraise as `OSError`; the calling tool catches and lands the affected file's chunks under `skipped_missing` with a metric distinguishing "lock_failed" from "file_not_found" so silent skips remain debuggable.
- **Splice or rewrite OS error** → reraises (lock context manager handles cleanup of orphan tmp file). Caller sees a generic tool error for the affected file's chunks; other files in the same batch still proceed.
- **Salience clamp** is unconditional via `MemoryScore.__post_init__` — no caller can push past `[0.0, 5.0]`.

## 7. Testing

AC-6 requires ≥ 6 tests. This design delivers 18 in `tests/test_capture/test_recall_feedback.py` to cover consensus-anticipated scenarios (concurrency, lock-helper interactions, eviction edge cases). The minimum AC-6 set maps to tests 1, 3, 4, 5/6, 8, 10. The rest are defense-in-depth.

1. `test_recall_relevant_returns_recall_id` — uuid4 format, present in response
2. `test_recall_id_is_unique_per_call` — N calls produce N distinct ids
3. `test_feedback_applies_used_boost` — `+0.1` per used chunk, single-file case
4. `test_feedback_applies_ignored_decay` — `-0.05` per ignored chunk
5. `test_feedback_clamps_at_max` — bumping past 5.0 clamps to 5.0 (consensus-driven boundary)
6. `test_feedback_clamps_at_min` — bumping below 0.0 clamps to 0.0
7. `test_feedback_batches_multiple_chunks_per_file` — 3 used chunks from same file → single RMW with delta = 0.3
8. `test_feedback_idempotent_replay_skips_applied` — same payload twice; second call's chunks all in `skipped_already_applied`
9. `test_feedback_idempotent_partial_replay` — first call applies 2/3; retry with all 3 applies the missing 1, others land in `skipped_already_applied`
10. `test_feedback_expired_recall_id_skips_all` — fake clock advances 25h; feedback gets `skipped_expired`
11. `test_feedback_missing_recall_id_skips_all` — random uuid that was never minted → `skipped_missing` (distinct from expired)
12. `test_feedback_non_discovery_chunk_skipped_unsupported` — chunk with source `session_file` lands in `skipped_unsupported`
13. `test_feedback_archived_file_skipped_missing` — file exists in `.archive/` but not main dir → `skipped_missing`
14. `test_feedback_superseded_file_skipped_missing` — file with `.superseded` suffix only → `skipped_missing`
15. `test_sweep_on_write_evicts_stale_entries` — register N entries, advance clock, register one more, assert old entries gone
16. `test_concurrent_feedback_different_recall_ids` — two threads, two recall_ids, two different files → both apply cleanly
17. `test_lock_helper_serializes_set_memory_score_and_feedback` — race set_memory_score with feedback on same file, assert both updates land (mirrors S-70 partial-update test)
18. `test_malformed_payload_returns_structured_error` — `recall_id=123` (int not str), `used="not-a-list"`, etc.

Tests use `unittest.mock.patch` on `time.time` for clock-advancing scenarios (mirrors S-78 patterns).

## 8. Performance considerations

- **`recall_relevant` hot path**: adds `RecallStore.register_recall()` — one lock acquisition, one dict insert, one O(n) sweep. n is bounded by 24h of recall traffic; for a busy server this is at most ~thousands. Sweep is ~microseconds. Negligible.
- **`recall_feedback`**: O(C) bucketing where C is total chunks in the payload, then one lock+RMW per unique target file. For typical feedback (5–20 chunks across 1–5 files), this is ~5 lock acquisitions and ~5 fsyncs. Acceptable.
- **Memory**: each `RecallEntry` is ~tens of bytes per chunk_id + a small applied-set. At 1000 entries × 20 chunks each = ~few hundred KB. Fits comfortably.

## 9. Risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| `RecallStore` is a module-level singleton — testing requires reset between tests | Medium | Expose a `reset_store()` test helper; use a fixture in `test_recall_feedback.py` |
| Lock-ordering bug between store lock and file lock could deadlock under concurrent feedback | Medium | Design holds store lock briefly per-operation; never holds across file I/O. Test 16 verifies. |
| Helper extraction touches `_tool_set_memory_score` — risk of behavioral regression | High | Mandatory consensus review on the refactor portion; full S-70 test suite must pass |
| `chunk_id → file resolution` is fragile if S-70 ever changes the chunk_id format | Medium | Centralize the parser (`_chunk_id_to_file_stem`) in one place; document the format dependency in code |
| The `entry.applied` set update happens AFTER file mutation — between mutation and update, the entry could in principle be evicted by a concurrent sweep | Low | This window is benign: if the entry evicts before the applied-set update, a retry of the same payload finds the recall_id missing at step 1 and lands all chunks in `skipped_expired` — no double-apply. The mutation that landed is preserved on the file but invisible to the recall_id's tracking, which is acceptable (the bump itself is the durable record; tracking is just for idempotency). |

## 10. Out of scope (deferrable)

- **Env-configurable bump magnitudes** — symmetric with S-71 but not in AC; add as follow-on if operators ask.
- **ContextBus row mutation** — bus rows have `salience` (S-70) but Q2 limits S-72 to discovery files; bus participation is a separate story.
- **Persistence across MCP restarts** — Q3 in-memory; SQLite or JSONL backend is a clean upgrade path behind the same `RecallStore` interface if needed.
- **Multi-process coordination** — tier-1 is single-process; if multi-process MCP ever lands, the in-memory store needs replacement.
- **Per-source-type bump magnitudes** — currently the `+0.1` / `-0.05` is uniform for all "discovery" chunks; a future story might want different magnitudes for decisions vs negatives.

## 11. Suggested commit decomposition

Mirrors S-70's three-commit pattern:

| Commit | Content | Gateable? |
|---|---|---|
| 1 | `test(feedback)`: 18 test skeletons in `test_recall_feedback.py`, all skip-gated on the implementation modules | Yes — consensus on test completeness |
| 2 | `feat(file_locking)`: extract `core/file_locking.py`; refactor `_tool_set_memory_score` to use it; full S-70 test suite + new lock-helper unit tests pass | Yes — consensus on behavioral equivalence + new helper API |
| 3 | `feat(feedback)`: implement `RecallStore`; wire `recall_relevant` to mint recall_ids; add `_tool_recall_feedback`; tool count 14→15; all 18 tests green | Yes — consensus on behavior wiring + concurrency tests |

Per-commit Codex+Claude consensus per `~/.claude/rules/commit-review.md`.
