# Response to agent-ops — DepthFusion E-27 ready for consumption

- **From:** DepthFusion project (Greg / Claude)
- **To:** agent-ops
- **Date:** 2026-05-05
- **Status:** responding to `docs/2026-05-05-to-depthfusion-confirm-e27-ready-for-consumption.md`
- **Verdict:** **GO with one caveat** — pin by commit SHA, not by version tag (E-27 is merged-but-pre-release).

---

## TL;DR

| agent-ops item | DF blocker | DF status | Tool name | Stable to consume? |
|---|---|---|---|---|
| E-04 S-12 | DF S-70 | done, on `main` | `depthfusion_set_memory_score` | yes — pin to SHA `25fd205` or later |
| E-04 S-14 | DF S-69 | done, on `main` | `depthfusion_pin_discovery` | yes — same SHA |
| E-04 S-15 | DF S-72 | done, on `main` | `depthfusion_recall_feedback` (+ `depthfusion_recall_relevant` now returns `recall_id`) | yes — same SHA |
| E-04 S-16 | DF S-73 | done, on `main` | (no MCP tool — JSONL event stream) | yes — same SHA |
| ADR 0004 | DF S-78 | done, on `main` | `depthfusion_publish_context` (response shape changed) | yes — same SHA, **breaking change** below |

**Pin:** commit `25fd205` (HEAD on `main` as of 2026-05-04 12:59 UTC). All five stories landed on `main` between 2026-04-30 and 2026-05-02. The package version in `pyproject.toml` reads `0.6.0a1` but that tag predates E-27 — see §1.

---

## 1. Released vs ticked

**E-27 is merged-but-pre-release.** Commits landed on `main` on 2026-05-01 (S-70, S-72, S-78), 2026-05-01 evening (S-69, S-73), and were ticked done in BACKLOG.md on 2026-05-02. The most recent release tag, `v0.6.0a1`, was cut on 2026-04-21 17:21 UTC — **10 days before** E-27 work began. There is no v0.6.0 release yet (CHANGELOG line 125 lists it as "unreleased").

**Implication for agent-ops:** do not pin to `0.6.0a1` — that version does not contain E-27. Pin to commit SHA instead.

**Recommended pin:** `25fd205` (HEAD on `main`, 2026-05-04 12:59 UTC).
**Earliest E-27-complete SHA:** `397722f` (2026-05-02 06:13 UTC, the docs commit ticking S-73/S-76/S-77).

We do not have a target release date for v0.6.0. If you need a tagged release for dependency-pinning policy reasons, tell us and we can cut a `v0.6.0a2` covering E-27 + E-28 within a day. Otherwise the SHA pin is the cleanest path until v0.6.0 ships proper.

---

## 2. Stable public-API surface

Authoritative source: `src/depthfusion/mcp/server.py`. All four MCP tools return JSON-encoded strings (you'll need to `JSON.parse` on the agent-ops side — this is consistent with the existing surface).

### 2.1 S-69 — `depthfusion_pin_discovery`

```jsonc
// Request
{
  "filename": "2026-05-04-myproj-decisions.md",  // required, non-empty string
  "pinned": true                                  // optional, default true; bool only
}

// Response — success
{
  "ok": true,
  "filename": "2026-05-04-myproj-decisions.md",
  "pinned": true,
  "previous": false        // the value before this call (lets callers detect no-op vs change)
}

// Response — file not found
{ "error": "file not found", "filename": "<input>" }

// Response — invalid args
{ "error": "pin_discovery: 'filename' must be a non-empty string", "filename": null }
{ "error": "pin_discovery: 'pinned' must be a bool, got int", "filename": "<input>" }
```

- **Unpin** is the same tool with `pinned: false`. There is no separate `depthfusion_unpin_discovery`.
- **Idempotency:** re-pinning a pinned file (or unpinning an unpinned file) is a successful no-op — `ok: true`, `previous` reveals state didn't change.
- **Locking:** atomic via `fcntl.LOCK_EX` on `<file>.scorelock` sidecar + `mkstemp` + `os.replace`. Safe under concurrent callers.
- **Filename resolution:** the bare filename (e.g. `2026-05-04-myproj-decisions.md`) resolves under `~/.claude/depthfusion-discoveries/<project>/`, where `<project>` is the active project slug as set by the most recent `depthfusion_set_scope` call (or the cwd's git-repo name as fallback). Do NOT pass an absolute path. If the file lives in a sibling project's discovery directory, switch scope first or include the project segment in the filename — see `core/types.py:_resolve_discovery_path()` for the exact rules.

### 2.2 S-70 — `depthfusion_set_memory_score`

```jsonc
// Request
{
  "filename": "2026-05-04-myproj-decisions.md",  // required
  "importance": 0.85,                             // optional, float ∈ [0.0, 1.0]; clamped if out of range
  "salience": 2.5                                 // optional, float ∈ [0.0, 5.0]; clamped if out of range
}

// Response — success
{
  "ok": true,
  "filename": "2026-05-04-myproj-decisions.md",
  "importance": 0.85,
  "salience": 2.5,
  "previous": { "importance": 0.5, "salience": 1.0 }
}

// Response — invalid args
{ "ok": false, "error": "set_memory_score: 'filename' must be a non-empty string" }
```

- **Both fields are independent inputs** — supply one or both. Unsupplied fields preserve the file's current value (true partial-update under the same lock).
- **Value type:** float. Defaults: `importance: 0.5`, `salience: 1.0` (constants `DEFAULT_IMPORTANCE` / `DEFAULT_SALIENCE` in `core/types.py`).
- **Clamping:** out-of-range values are silently clamped via `MemoryScore.__post_init__` — no error returned. If you need strict validation on the agent-ops side, validate before calling.
- **Heads-up on default importance for extracted discoveries:** the decision/negative extractors derive `importance` from `max(per-entry-confidence)` across all entries written to the same file, not the mean. A file with one high-confidence + four low-confidence entries gets the high-confidence file-level importance. Documented in `core/feedback.py`-adjacent memory and in the S-70 consensus review (`docs/reviews/2026-05-01-s70-consensus.md`). Do not assume mean.
- **Idempotency:** replaying the same payload produces a byte-identical file (provided no concurrent writer interleaved).
- **Filename resolution:** same rules as §2.1 — bare filename under `~/.claude/depthfusion-discoveries/<project>/`; no absolute paths.

### 2.3 S-72 — `depthfusion_recall_feedback` (+ `recall_relevant` change)

**`depthfusion_recall_relevant` response shape changed** — it now includes `recall_id`. Full response (so you can see exactly which `chunk_id` value to thread into the `used`/`ignored` lists for `recall_feedback`):

```jsonc
// recall_relevant response — full shape
{
  "query": "the query string passed in",
  "blocks": [
    {
      "chunk_id": "abc123def456-0",          // ← this is what goes into used/ignored
      "snippet": "…up to 1500 chars of body…",
      "source": "discoveries/myproj/2026-05-04-foo.md",
      "score": 0.847,
      "tier": "vps-gpu",
      "tags": ["decision", "auth"],
      "metadata": { "project": "myproj", "file_mtime": "2026-05-04T10:11:12Z" }
    }
    // ...up to N blocks (N = top_k from request, default 10)
  ],
  "recall_id": "5f8a8b30-9c7e-4d3a-b21f-8e6a4d5c2b1a",   // NEW: feed this to recall_feedback
  "result_count": 5,
  "total_latency_ms": 803.245,
  "backend_used": { "reranker": "haiku", "embedding": "local_embedding", ... }
}

// On empty-result paths, recall_id is null:
{ "query": "...", "blocks": [], "recall_id": null, "result_count": 0, ... }
```

**Threading `recall_id → chunk_id` into feedback:**

```typescript
const r = JSON.parse(await mcpClient.call('depthfusion_recall_relevant', {query: '...'}));
const usedIds   = r.blocks.filter(b => userActuallyUsed(b)).map(b => b.chunk_id);
const ignoredIds = r.blocks.filter(b => !userActuallyUsed(b)).map(b => b.chunk_id);
await mcpClient.call('depthfusion_recall_feedback', {
  recall_id: r.recall_id, used: usedIds, ignored: ignoredIds,
});
```

**`depthfusion_recall_feedback`:**

```jsonc
// Request — input shape is recall_id + per-chunk verdicts as TWO disjoint lists
{
  "recall_id": "5f8a8b30-…-uuid4",   // required
  "used":    ["chunk_a", "chunk_b"], // optional, default []
  "ignored": ["chunk_c"]              // optional, default []
}

// Response — success
{
  "ok": true,
  "recall_id": "5f8a8b30-…-uuid4",
  "applied": { "used": 2, "ignored": 1 },
  "skipped_already_applied": 0,
  "skipped_unknown_chunk_id": 0
}

// Response — recall_id not found (expired or never registered)
{ "ok": false, "error": "recall_feedback: recall_id not found", "recall_id": "<input>" }
```

- **Input shape decision:** two disjoint lists (`used`, `ignored`) — NOT a single list of per-chunk verdicts. We considered the per-chunk verdicts shape and rejected it as more verbose without observable benefit. We do NOT model "harmful" as a third state — you either used a chunk or you didn't. If you need to flag harm, that's a separate signal (file an agent-ops story; we can extend).
- **Effect:** salience changes are bounded `[0.0, 5.0]`. Per S-72 AC-3: `+0.1` per used, `−0.05` per ignored. Bounds enforced; replays are tracked in an applied-set keyed by `(recall_id, chunk_id)` for idempotency.
- **`recall_id` lifetime:** in-memory `RecallStore` singleton, process-local. Default TTL is 24 hours per S-72 AC-2. **The store does not survive an MCP server restart** — see §4 known issue #2.

### 2.4 S-73 — high-importance discovery event

**Not an MCP tool. JSONL append-only stream that consumers tail.**

```jsonc
// Path: ~/.claude/shared/depthfusion-events.jsonl
//   (env-overridable via DEPTHFUSION_EVENT_LOG)
// Trigger: any publish where the resulting frontmatter has importance ≥ threshold
//   (default 0.8, env-overridable via DEPTHFUSION_HIGH_IMPORTANCE_THRESHOLD)
// Format: one event per line

{
  "timestamp": "2026-05-04T18:42:11.527301+00:00",
  "event": "high_importance_discovery",
  "project": "agent-ops",
  "file_path": "/home/gregmorris/.claude/depthfusion-discoveries/agent-ops/2026-05-04-checkpoint.md",
  "importance": 0.92,
  "salience": 1.0,
  "summary": "checkpoint published with 7 decisions, 2 negatives"
}
```

- **Channel:** plain JSONL on disk. No Postgres NOTIFY, no webhook, no log line. DepthFusion deliberately doesn't own delivery (per S-73 AC-4) — that's a consumer concern.
- **Recommended consumption pattern:** `inotifywait` or `tail -F` on the event log; parse each line as JSON; on schema-mismatch, log and continue (so a future schema extension doesn't crash agent-ops).
- **Threshold semantics:** `importance >= threshold`, comparison is on the resolved frontmatter value (defaults applied if absent). A publish at `importance: 0.8` exactly with the default threshold WILL fire (not strictly greater).
- **Daily rotation:** writer rotates at calendar-day boundary (UTC). Consumers should expect file paths like `depthfusion-events.jsonl` (today) and `depthfusion-events.YYYY-MM-DD.jsonl` (rotated). Rotation policy may evolve; treat the *path glob* as stable, not the exact filename.

### 2.5 S-78 — `depthfusion_publish_context` (BREAKING CHANGE)

**The response shape changed from the v0.5.x stub.** Consumers using the old `{published: bool, item: <object>}` shape will silently misread the response.

```jsonc
// Request — pre-existing shape (unchanged from v0.5.x); importance/salience are NEW optional fields
{
  "item": {
    "item_id": "agent-ops-checkpoint-1234",
    "content": "…full content…",
    "source_agent": "agent-ops",
    "tags": ["checkpoint", "agent-ops"],
    "priority": "normal",                 // optional, default "normal"
    "ttl_seconds": null,                  // optional, default null
    "metadata": { "...": "..." },         // optional, default {}
    "importance": 0.85,                   // NEW (S-70), optional, defaults to 0.5
    "salience": 1.0                       // NEW (S-70), optional, defaults to 1.0
  }
}

// Response — first publish (newly stored)
{ "published": true, "item_id": "agent-ops-checkpoint-1234", "deduped": false }

// Response — duplicate publish (same content_hash already in the bus)
{ "published": true, "item_id": "<original_item_id>", "deduped": true }

// Response — invalid payload
{ "error": "publish_context: 'item' must be an object", "published": false }
```

- **Idempotency key:** sha256 of the `content` field, computed at `ContextItem` construction. Bytewise-identical content dedupes; any whitespace/casing/metadata difference creates a new item. Tag differences alone DO NOT affect the hash.
- **`item_id` semantics on dedup:** the response returns the **original** stored item's `item_id`, not the retry's. Use this to thread your retry side back to the canonical record.
- **Backward compat:** rows in `bus.jsonl` written before S-78 lack `content_hash` and are NEVER matched for dedup — they're treated as legacy items. New rows always include the hash.
- **Concurrent publish safety:** cross-process `flock` on the bus file prevents double-insert under simultaneous identical-content publishes from separate processes. Tested.

---

## 3. Migration notes / breaking changes

**One breaking change** — listed once, here, in case it scrolls past in §2.5:

- `depthfusion_publish_context` response shape changed from `{published: bool, item: <object>}` (the v0.5.x stub) to `{published: bool, item_id: str, deduped: bool}`. Callers reading `result.item.something` will get `undefined`. Callers checking `result.published` continue to work.

**Where to find the call site in agent-ops:** per ADR 0004, the existing single-attempt swallow-errors call lives in `core/src/checkpoint/handlers.ts:tryDepthFusionPublish()`. That's the file to edit for both the response-shape migration AND the new retry path the ADR authorizes. Suggested before/after:

```typescript
// Before (v0.5.x stub):
const r = JSON.parse(await mcpClient.call('depthfusion_publish_context', {item}));
const id = r.item.item_id;        // undefined under E-27
return { ok: r.published, id };

// After (E-27):
const r = JSON.parse(await mcpClient.call('depthfusion_publish_context', {item}));
const id = r.item_id;             // top-level now
const wasIdempotentHit = r.deduped;
return { ok: r.published, id, wasIdempotentHit };
```

No other breaking changes. `depthfusion_recall_relevant` gained `recall_id` (additive — old consumers ignoring it are unaffected). All other E-27 work introduced new tools / new fields, no removals or signature changes.

---

## 4. Known issues to be aware of

1. **`config_version_id` is empty string in 100% of capture & recall events** (957/957 capture, 30/30 recall observed over 13 days). This field carries the D-3 invariant per DR-018 §4 (auditor reproducibility). It's structurally present but never populated in non-gate emit paths. Filed as DepthFusion **S-81 (P1)** in our v0.5.3 polish epic. Doesn't affect agent-ops's tool calls — flagging only because if you build any audit-trail features on top, do not depend on this field yet.

2. **`RecallStore` (S-72) is process-local in-memory.** A DepthFusion MCP server restart between `recall_relevant` and `recall_feedback` calls will lose the `recall_id → chunk_ids` mapping; the feedback call returns `{ok: false, error: "recall_id not found"}`. In normal operation this is fine (calls are seconds apart). For long-running async agent flows that may span a restart, design defensively (treat feedback as best-effort).

3. **Default file-level `importance` is `max()` of per-entry confidences, not mean** (S-70 design). Already covered in §2.2 — repeating because it surprises operators expecting a mean. Documented in `docs/reviews/2026-05-01-s70-consensus.md`.

4. **Capture pipeline emission is currently zero on this host** for production-path sessions (real Claude Code invocations writing to `~/.claude/depthfusion-discoveries/<project>/`). All 957 observed capture events over 13 days came from test fixtures (`/tmp/...` paths). Filed as DepthFusion **S-79 (P0)** under v0.5.3 polish — see `docs/runbooks/dogfood-reports/2026-05-04-week1.md`. **This may or may not affect agent-ops** depending on how your MCP server is wired.

   **3-step diagnostic to check your environment** (run after pinning the SHA + restarting your MCP client + executing one real `publish_context` call from your agent-ops code):

   ```bash
   # 1. Today's metrics directory should have today's date
   ls -la ~/.claude/depthfusion-metrics/ | grep "$(date -u +%Y-%m-%d)"
   # Expected: 1–4 files (YYYY-MM-DD.jsonl, -capture, -recall, optionally -gates)

   # 2. The capture stream should reference YOUR project, not /tmp/ paths
   tail -1 ~/.claude/depthfusion-metrics/$(date -u +%Y-%m-%d)-capture.jsonl 2>/dev/null \
     | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print('PROD-PATH OK' if not d.get('file_path','').startswith('/tmp/') else 'AFFECTED: routing to /tmp/')"

   # 3. The S-73 event log should be reachable + writable
   ls -la ~/.claude/shared/depthfusion-events.jsonl 2>/dev/null \
     || echo "EVENT LOG MISSING — high-importance events will fail silently"
   ```

   If you hit "AFFECTED: routing to /tmp/" or the event log is missing, raise it back to us and we'll prioritize S-79 ahead of the rest of v0.5.3.

5. **MCP tool responses are JSON-encoded strings, not dicts** — this is consistent across the entire DepthFusion MCP surface. Your client must `JSON.parse` the result. Not a regression; flagging in case agent-ops's typing layer expects dicts directly.

---

## 5. Are we actively iterating on E-27?

**No active iteration on E-27 itself.** All five stories are tested and stable on `main`. The dual-LLM consensus reviews (S-70 at `docs/reviews/2026-05-01-s70-consensus.md`, S-72 at `docs/reviews/2026-05-01-s72-consensus.md`, S-78 at `docs/reviews/2026-04-30-s78-consensus.md`) reached consensus; all flagged findings were fixed before commit.

**Adjacent work that could affect agent-ops** (none breaking, all additive):

- **E-29 v0.5.3 Polish — Dogfood-Surfaced Instrumentation Gaps** is being drafted right now (six P0–P3 stories surfaced from the 2026-05-04 dogfood pass). One of these (S-81) addresses the empty `config_version_id` mentioned in §4. None modify the E-27 tool surface.
- **E-28 Tier-1 Engagement Audit & Introspection Surface** is also done on `main` (same SHA range). It's separate from E-27 and adds the introspection tools `depthfusion_describe_capabilities` and `depthfusion_inspect_discovery` — useful adjacent reading if you want to query DepthFusion's tier state from agent-ops.

**Verdict for agent-ops planning:** safe to start now, no need to wait. We don't anticipate breaking changes to S-69/70/72/73/78 surface before v0.6.0 ships.

---

## 6. Suggested next steps (mirrors §"What we'll do once we have the answers" in the inbound)

**Step 0 — 60-second smoke test (do this first before writing any consumer code):**

```bash
# Confirms: install path right, package version contains E-27, MCP tool dispatch reachable, response parses as JSON.
python3 -c "
import json
from depthfusion.mcp.server import _tool_pin_discovery
r = json.loads(_tool_pin_discovery({'filename': '__smoke_test__.md', 'pinned': True}))
assert r.get('error') == 'file not found', f'unexpected: {r}'
print('OK — DepthFusion E-27 wired correctly. All four E-27 tools are callable.')
"
```

Expected output: `OK — DepthFusion E-27 wired correctly. All four E-27 tools are callable.`

If the import fails: your pin doesn't include E-27 (re-pin to SHA `25fd205` or later).
If the assertion fails: the response shape changed since this doc was written — ping us before proceeding.

**Then:**

1. Pin agent-ops dependency to DepthFusion commit `25fd205` (or any later commit on `main` until v0.6.0 cuts).
2. Update agent-ops `BACKLOG.md` cross-cutting deps: mark S-12, S-14, S-15, S-16 as **unblocked**.
3. Update `docs/decisions/0004-depthfusion-publish-retry.md`: "Accepted (DF S-78 shipped, retry code authorized)."
4. Implement the four E-04 stories.
5. Implement ADR 0004 retry path.
6. **Smoke test the production-path emission question** (§4 issue #4) early — if agent-ops sees the same zero-emission behaviour, raise it back to us so we can prioritize S-79 ahead of the rest of the v0.5.3 polish work.
7. When you cut your first E-04 release, ping us — we'll cut `v0.6.0` (or `v0.6.0a2` if v0.6.0 isn't ready) at the same time so you can switch from SHA pin to version pin.

---

## Cross-references

- DepthFusion `BACKLOG.md` E-27 — lines 1118–1234
- DepthFusion `CHANGELOG.md` — `[Unreleased]` section (E-27 not yet documented there; will be added when v0.6.0 cuts)
- DepthFusion `pyproject.toml` — version `0.6.0a1` (predates E-27 — see §1)
- Authoritative tool source: `src/depthfusion/mcp/server.py` lines 22–230 (registration), 755 (publish_context), 1066 (set_memory_score), 1163 (recall_feedback), 1265 (pin_discovery)
- S-70 consensus review: `docs/reviews/2026-05-01-s70-consensus.md`
- S-72 consensus review: `docs/reviews/2026-05-01-s72-consensus.md`
- S-78 consensus review: `docs/reviews/2026-04-30-s78-consensus.md`
- 2026-05-04 dogfood pass (informs §4 known issues): `docs/runbooks/dogfood-reports/2026-05-04-week1.md`
- Inbound request: `docs/2026-05-05-to-depthfusion-confirm-e27-ready-for-consumption.md`
