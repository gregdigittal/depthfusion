# publish_context Idempotency Runbook

> **Epic/Story:** E-27 S-78 — *publish_context idempotency by content_hash*
> **Status:** Active — landed in v0.5.x (this commit)
> **Purpose:** Document the API contract and operational behaviour of
> `depthfusion_publish_context` so consumers (agent-ops, future MCP clients,
> internal callers via `ContextPublisher`) can rely on retry-safe semantics.

This runbook is for callers who need to retry a publish on transient failure
without creating duplicate context entries, and for operators inspecting
`bus.jsonl` to understand what dedup did or didn't fire.

---

## 1. The contract

### Request

```json
{
  "item": {
    "item_id": "<caller-supplied id>",
    "content": "<bytes that define the dedup key>",
    "source_agent": "<agent slug>",
    "tags": ["..."],
    "priority": "normal",
    "ttl_seconds": null,
    "metadata": {}
  }
}
```

`content` is the **only** field that contributes to the dedup key. `item_id`,
`source_agent`, `tags`, `priority`, `ttl_seconds`, and `metadata` do not.

### Response

```json
{ "published": true, "item_id": "<id>", "deduped": false }
```

| Case | `published` | `item_id` | `deduped` |
|---|---|---|---|
| First publish (newly stored) | `true` | the caller's `item_id` | `false` |
| Repeat publish (identical content) | `true` | the **original** stored item's `item_id` (NOT the retry's) | `true` |
| Bus error (disk, IO) | `false` | absent; `error` field present | absent |
| Invalid payload | `false` | absent; `error` field present | absent |

The dedup branch returning the **original** `item_id` is the contract that lets
agent-ops ADR 0004 ramp option β (single retry on transient errors) — the caller
can correlate retries to the canonical stored record.

---

## 2. What dedupes, what doesn't

Dedup is **exact-content** (sha256 of `content` bytes):

- `"hello"` vs `"hello"` → dedupes ✓
- `"hello"` (tags `["a"]`) vs `"hello"` (tags `["b", "c"]`) → dedupes ✓ (tags excluded)
- `"hello"` (metadata `{}`) vs `"hello"` (metadata `{"trace": "x"}`) → dedupes ✓
- `"hello"` vs `"hellp"` → new item (1-byte diff)
- `"hello world"` vs `"hello  world"` → new item (whitespace differs)
- `"Hello"` vs `"hello"` → new item (casing differs)

If a caller wants to allow tag/metadata variation between retries, this is the
correct behaviour: the same payload with updated routing metadata is still the
same payload.

If a caller wants stricter dedup (e.g. content + source_agent), they must
include those fields inside `content` themselves.

---

## 3. Backward compatibility (AC-6)

Rows written to `bus.jsonl` before this story shipped do **not** carry a
`content_hash` field. They are loaded into `subscribe()` results as
`ContextItem(content_hash="")` and are intentionally **never matched for
dedup**. If you publish content identical to a legacy row, you will get a
fresh item; the legacy row remains in place.

This is deliberate — re-hashing legacy rows on read would be cheap but
silently changes the dedup graph for content that was never stored under a
hash discipline. Operators who want to coalesce legacy duplicates should run
a one-shot migration script (not in scope for S-78).

---

## 4. Concurrency

**Within a process:** a `threading.Lock` on each bus instance serializes
publishes. Two threads in the same process publishing identical content
produce one stored row and one dedupe response.

**Across processes:** `FileBus.publish()` holds an exclusive `fcntl.flock`
on `bus.jsonl` for the read-check-write critical section. Two processes
publishing identical content produce one stored row and one dedupe response.

The hash index in memory is a **warm cache** — the authoritative dedup
decision is always made under flock against a freshly-re-scanned `bus.jsonl`.
This is what makes cross-process correctness work: a sibling process's writes
between our `__init__` and our first `publish()` are observed.

---

## 5. Retry pattern (recommended)

DepthFusion is the MCP **server**; clients live elsewhere (Claude Code, custom
MCP-protocol clients, agent-ops, etc.). Invoke the tool by name through
whatever MCP client you're using. The example below is pseudocode showing the
response-handling shape callers should implement — substitute your client's
actual call API:

```python
# Pseudocode — `mcp.call(tool_name, args) -> dict` is whatever your MCP client
# library exposes (e.g. JSON-RPC over stdio, the Anthropic MCP SDK, etc.).
result = mcp.call("depthfusion_publish_context", {"item": {
    "item_id": "...",
    "content": "...",
    "source_agent": "...",
    "tags": ["..."],
}})

# `result` is the parsed JSON of the tool response. Three branches:

if not result.get("published"):
    # Bus error or invalid payload. Caller decides retry policy.
    # Retries are safe — identical `item.content` will dedupe on the next try.
    log.warning("publish failed: %s", result.get("error"))
    schedule_retry(item)
elif result["deduped"]:
    # Already stored — this was a retry of a previous successful publish.
    # `result["item_id"]` is the canonical (original) record id, NOT the retry's.
    log.debug("publish dedupe hit: original id %s", result["item_id"])
else:
    # First-publish path. `result["item_id"]` is the id we just sent.
    log.debug("publish stored: %s", result["item_id"])
```

For in-process tests within the DepthFusion repo itself, you can call the tool
implementation directly:

```python
from depthfusion.mcp import server as mcp_server
raw = mcp_server._tool_publish_context({"item": {...}})
result = json.loads(raw)
```

A retry of identical content is always safe. Callers do not need to track
"have I already published this" themselves; the bus is the authority.

---

## 6. ContextPublisher caveat

`ContextPublisher.publish(content, tags=[...])` constructs a `ContextItem`
with a fresh `uuid4()` for `item_id` on every call and returns the locally-
constructed item. If the underlying bus dedupes, the returned `item.item_id`
is **not** the canonical stored id — it is the id the publisher just minted.

If you need the canonical stored id, call the bus directly:

```python
from depthfusion.router.bus import ContextBus  # type: ignore
result = bus.publish(item)
canonical_id = result["item_id"]  # original on dedup, fresh on store
```

The publisher API may grow a `return_canonical=True` flag in a follow-up; for
now, callers who need that semantic must call the bus.

---

## 7. Operational inspection

The bus lives at `$DEPTHFUSION_BUS_FILE_DIR` (default `~/.claude/context-bus/`)
in `bus.jsonl`. Each line is one record. To see how many duplicate-content
entries exist (should be zero post-S-78 for new writes):

```bash
jq -r '.content_hash // "<legacy>"' ~/.claude/context-bus/bus.jsonl \
  | sort | uniq -c | sort -rn | head
```

Any count > 1 for a non-`<legacy>` hash indicates either a pre-S-78 row or a
bug in the dedup path. File an issue with the offending hashes.

To audit the first session post-deploy, raise the logger to DEBUG:

```bash
DEPTHFUSION_LOG_LEVEL=DEBUG python -m depthfusion.mcp
```

The recall lesson from AMC's dedup cutover (2026-04-11) was that latent
duplicate-tolerance bugs in callers can surface only when dedup activates.
A 24-hour DEBUG window after rollout is cheap insurance.

---

## 8. Cross-references

- **Story:** `BACKLOG.md` E-27 S-78
- **Code:** `src/depthfusion/core/types.py` (ContextItem.content_hash),
  `src/depthfusion/router/bus.py` (publish dedup), `src/depthfusion/mcp/server.py`
  (`_tool_publish_context`, `_get_context_bus`)
- **Tests:** `tests/test_router/test_bus_idempotency.py`
- **Cross-project:** `~/projects/agent-ops/docs/decisions/0004-depthfusion-publish-retry.md`
- **Recall lessons applied:**
  `~/.claude/skills/learned/global/cache-on-success-not-completion.md`
  (index updated only after fsync) and
  `~/.claude/skills/learned/stack/delete-then-insert-masks-dedup-bugs.md`
  (DEBUG window post-deploy).
