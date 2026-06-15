# Sync V2 — Hub-and-Spoke Protocol Design

**Status:** Draft
**Date:** 2026-06-11
**Story:** S-166 (AC-1)
**Task:** T-581 (protocol design), T-582 (conflict policy)
**Supersedes:** `sync.sh` (rsync, deprecated under T-588)
**Downstream:** Lane A (identity), Lane D (platform)

---

## Overview

DepthFusion V2 replaces the rsync-based `sync.sh` with a cursor-based delta sync
protocol that moves context and memory records between developer laptops and the
central Hetzner VPS (`176.9.147.206`). The model is strictly **hub-and-spoke**:
every client talks only to the VPS hub — never to another client — which gives a
single authoritative ordering of records, a single enforcement point for ACL and
classification labels, and a single place to gate sync behind authentication. This
replaces `sync.sh`'s whole-tree, filesystem-level rsync (which had no record-level
identity, no delta cursor, no ACL awareness, and no auth gate) with a record-oriented
HTTP protocol where each client pulls only what changed since its last cursor. Because
the hub holds the canonical sequence, clients can go offline indefinitely and catch up
with a single batched delta on reconnect.

---

## Goals and Non-Goals

### Goals

- **Cursor-based deltas** — clients pull only records changed since their last
  per-store cursor, not the whole tree. Bandwidth scales with change rate, not store size.
- **ACL-stamped envelopes** — every record carries its `acl_allow` list; the hub
  enforces visibility on pull so clients never receive records they are not entitled to.
- **Classification labels** — every record carries an `internal | confidential | public`
  label, synced as part of the envelope and preserved end-to-end.
- **Offline-safe clients** — a client may be offline for any duration; on reconnect it
  resumes from its stored cursor and receives a single batched delta. No data is lost
  because the hub retains the canonical ordering and tombstones.

### Non-Goals

- **No peer-to-peer.** All traffic flows through the VPS hub. Clients never discover or
  connect to each other. (Hard constraint for V2.)
- **No CRDTs.** Conflict handling is last-writer-wins by timestamp (see Conflict Policy),
  not merge-by-construction. Convergence is server-authoritative, not algebraic.
- **No real-time streaming.** Sync is batch-on-reconnect (and on a client-chosen poll
  interval). There is no push channel, websocket, or live tail in V2.

---

## Architecture

```
   ┌────────────────┐                                          ┌────────────────┐
   │   Client A     │                                          │   Client B     │
   │  (laptop)      │                                          │  (laptop)      │
   │                │                                          │                │
   │ cursor[store]  │                                          │ cursor[store]  │
   └───────┬────────┘                                          └───────▲────────┘
           │                                                           │
           │  POST /v2/sync/push                       GET /v2/sync/pull?cursor=n
           │  (Bearer JWT)                                   (Bearer JWT)
           │  envelopes[]                                    → envelopes[] + next_cursor
           ▼                                                           │
   ┌───────────────────────────────────────────────────────────────────────────┐
   │                          VPS Hub  (176.9.147.206:443)                       │
   │                                                                            │
   │   ┌──────────────┐   ┌─────────────────┐   ┌──────────────────────────┐   │
   │   │ Auth gate    │──▶│ Sequence engine │──▶│ Per-store record log     │   │
   │   │ (Entra JWT)  │   │ assigns cursor  │   │ (memory/vector/event_log/│   │
   │   │              │   │ seq per record  │   │  file_index/graph/disc.) │   │
   │   └──────────────┘   └─────────────────┘   └──────────────────────────┘   │
   │            │                                          │                    │
   │            └────────── ACL filter on pull ───────────┘                    │
   │                       (acl_allow ∩ principal)                             │
   └───────────────────────────────────────────────────────────────────────────┘

   Flow:  Client A pushes a changed record → Hub assigns it the next sequence
          number for that store and stamps modified_at → Client B pulls with its
          stored cursor → Hub returns records whose seq > cursor AND whose
          acl_allow admits B's principal, plus the new next_cursor.
```

Clients never exchange records directly. The hub is the only writer of the canonical
sequence and the only enforcement point for ACL and classification.

---

## Record Envelope Schema

Every record — push or pull — travels as an envelope of this shape:

```json
{
  "record_id": "mem_01J9X2K7QF8R3N",
  "store": "memory",
  "payload": { "...": "store-specific body; null when tombstone=true" },
  "acl_allow": ["user:greg@example.com", "group:depthfusion-core"],
  "classification": "internal",
  "tombstone": false,
  "modified_at": "2026-06-11T08:42:17Z",
  "version": 1
}
```

| Field | Type | Notes |
|---|---|---|
| `record_id` | string | Globally unique, client-minted, stable across updates. The identity key for upsert. |
| `store` | enum | One of `memory \| vector \| event_log \| file_index \| graph \| discoveries`. Cursors are tracked per store. |
| `payload` | object \| null | Store-specific body. MUST be `null` when `tombstone=true`. |
| `acl_allow` | string[] | Principal or group ids permitted to receive this record. Empty array = no client may pull it (hub-only). Server-authoritative on conflict. |
| `classification` | enum | `internal \| confidential \| public`. Server-authoritative on conflict. |
| `tombstone` | boolean | `true` marks a delete (see Tombstones). |
| `modified_at` | ISO-8601 (UTC, `Z`) | Last-writer-wins ordering key. Set/validated by the hub on push. |
| `version` | integer | Monotonic per `record_id`; increments on each accepted update. Used for optimistic concurrency. |

Note: the **cursor (sequence number)** is hub-internal ordering metadata returned
alongside the envelope batch on pull — it is not a field of the envelope itself, so
that `record_id` + `version` remain the stable record identity independent of where a
record sits in any one store's sequence.

---

## Cursor Model

Each client maintains one **cursor per store** — a monotonically increasing integer
sequence number representing the highest hub sequence it has consumed for that store.

- **Pull:** `GET /v2/sync/pull?store=<name>&cursor=<n>` returns every record in that
  store whose hub sequence number is greater than `n` **and** whose `acl_allow` admits
  the caller's principal, plus a `next_cursor` to persist. The client advances its
  stored cursor to `next_cursor` only after the batch is durably applied locally.
- **Full sync / first run:** clients start at `cursor=0`, which returns the entire
  visible store (all non-tombstoned records the principal may see, plus live tombstones).
- **Per-store independence:** stores advance independently; a quiet `graph` store does
  not force re-pulls of an active `memory` store.
- **Monotonicity guarantee:** the hub assigns sequence numbers strictly increasing per
  store at push-accept time, so a cursor is a complete watermark — nothing below it can
  ever change ordering, which is what makes resume-after-offline exact rather than
  best-effort.

Because the cursor is server-assigned and ACL filtering happens at pull time, two
clients with different entitlements legitimately receive different record sets for the
same cursor advance — this is expected, not a bug.

---

## Tombstones

Deletes propagate as **tombstone envelopes**, never as the silent absence of a record:

- A delete is represented as a normal envelope with `tombstone: true` and
  `payload: null`, carrying the same `record_id` and an incremented `version`.
- The hub assigns the tombstone the next sequence number for its store, so it flows to
  pulling clients through the ordinary cursor mechanism.
- On receipt of a tombstone, a client **deletes the local record** identified by
  `record_id` and records the tombstone's version to reject any later out-of-order
  resurrection (an update with a lower or equal version is ignored).
- **Retention:** the hub retains tombstones for **30 days**, then purges them. Thirty
  days bounds the catch-up window: a client offline longer than 30 days may miss a
  tombstone and must therefore do a **full re-sync** (`cursor=0`) rather than a delta —
  the hub signals this by rejecting a stale cursor (see Conflict Policy →
  Stale-Cursor Signaling).

---

## Conflict Policy

### Decision

Last-writer-wins (LWW) by `modified_at` with a **security-field server-authority
exception**. Vector clocks are deferred to post-v2.0.0.

**Rationale:** clients typically sync within hours, not days, so the window in which
two offline edits to the same `record_id` race is small and the cost of an occasional
silently-dropped body edit is bounded and recoverable. Vector clocks would add ~32B
per record per client of storage and a non-trivial comparison/merge path on every
push for a conflict class that is rare in practice. The combination of LWW for body
fields and **immutable, server-authoritative security fields** (`acl_allow`,
`classification`) gives the property that actually matters for an enterprise sync hub:
no client can widen its own visibility or downgrade a classification even on a write
that wins the LWW race. Convergence is server-authoritative (the hub is the only
writer of the canonical sequence), which is sufficient without algebraic merge.

### LWW Rules

1. **`modified_at` wins on push conflict.** When a pushed envelope's `record_id`
   already exists, the envelope with the later hub-validated `modified_at` becomes the
   live record. **On tie (identical `modified_at`): the server-stored record wins** —
   the push is treated as a no-op for the body and acknowledged as accepted. This makes
   ordering deterministic without requiring a vector/lamport stamp.
2. **Security-field exception.** `acl_allow` and `classification` **ALWAYS** defer to
   the server's stored value on conflict, **regardless of `modified_at`** — even when
   the incoming envelope wins the body LWW race. A client push may only change these
   fields if the caller is entitled to do so under the identity model (Lane A); any
   incoming value that differs from the server's stored value when the caller is not so
   entitled is discarded in favour of the server value. Violations (an unentitled
   attempt to change a security field) are logged at **WARN** with
   `(principal, resource, attempted_acl)` and the body update, if it otherwise wins
   LWW, is still applied with the server's security fields preserved.
3. **Tombstone exception.** `tombstone=true` **ALWAYS** wins over a live record for the
   same `record_id`, independent of `modified_at` ordering. A delete is never lost to a
   concurrent live edit; resurrection requires the explicit path in
   **Tombstone-then-Resurrection** below.
4. **Schema validation failure.** An envelope that fails schema validation is rejected
   with **HTTP 422** and the conflict policy is **not invoked** — the record is never
   compared against the stored value. Validation precedes conflict resolution.

### Per-Store Notes

| Store | Conflict behaviour |
|---|---|
| `memory` | LWW by `modified_at`. **No ordering guarantee on chunks** within a record — chunk order is not a conflict dimension. |
| `vector` | LWW. The **embedding must be regenerated server-side** on a conflict-win — a winning push supplies the source text/payload; the hub recomputes the vector so a stale or client-mismatched embedding can never become canonical. |
| `event_log` | **Append-only, no conflicts.** `record_id` MUST be unique per event; a duplicate `record_id` is a client bug, rejected at validation (422), never resolved by LWW. |
| `file_index` | LWW on metadata. **Binary payload is content-addressed (sha256)** — identical content collapses to one blob, so payload "conflicts" are impossible; only metadata races resolve via LWW. |
| `graph` | **Edge** LWW on `weight`; **node** LWW on attributes. Edges and nodes are independent conflict domains keyed by their own `record_id`. |
| `discoveries` | LWW on body; **`classification` is server-authoritative** (per the security-field exception — discoveries are the most classification-sensitive store). |

### Stale-Cursor Signaling

When a client presents a cursor that is **behind the 30-day tombstone purge boundary**
(i.e. the delta would be unsafe because a tombstone the client never saw has been
purged), the pull endpoint returns:

```
HTTP 409
{ "error": "cursor_expired", "new_cursor": 0 }
```

On receiving this, the client **MUST drop its local state for that store and re-sync
from `cursor=0`**. This is distinct from an ordinary empty delta: `409 cursor_expired`
explicitly tells the client its watermark is no longer a safe resume point, rather than
silently returning `cursor=0` semantics and leaving the client to guess. This answers
the offline-safe-correctness requirement from Tombstones (Retention).

### Clock-Skew Handling

`modified_at` is the LWW key, so the hub guards against client clock drift on push:

- **Future skew (> 5 minutes ahead of server time):** the hub **replaces** the
  envelope's `modified_at` with the current server time and logs a **WARNING**. This
  prevents a client with a fast clock from pinning a record as "newest" indefinitely
  and starving legitimate later edits.
- **Past skew (> 7 days in the past):** the envelope is **accepted** (a genuinely old
  offline edit is valid), but the response **flags** it (e.g. `"clock_warning":
  "stale_modified_at"`) so the client can surface a UI hint. The flag travels in the
  accepted-response only — it is **not** written into the stored record, so it never
  pollutes the canonical envelope or affects downstream LWW comparisons.

### Tombstone-then-Resurrection

A push of a **live** record whose `record_id` was **tombstoned within the 30-day
retention window** is **rejected**:

```
HTTP 409
{ "error": "record_tombstoned", "tombstone_ts": "<ISO-8601>" }
```

This prevents the version-gap ambiguity from Open Question 4: a client that missed the
tombstone (via a version gap) cannot accidentally resurrect a deleted record by pushing
a stale live copy. **Resurrection requires an explicit admin API call** — a deliberate,
audited operation — not an ordinary sync push. Because `version` is monotonic across a
record's full lifecycle (it does not reset across tombstone), the hub can always
distinguish a true new record (new `record_id`) from an attempted resurrection (existing
tombstoned `record_id`).

### Open Questions Resolved

All four open questions raised by T-581 for the T-582 reviewer are resolved by the
sections above:

1. **Stale-cursor / expired-tombstone gap** → resolved by **Stale-Cursor Signaling**:
   a distinct `409 cursor_expired` with `new_cursor: 0`, not silent `cursor=0` semantics.
2. **Concurrent edit with equal / clock-skewed `modified_at`** → resolved by **LWW Rule 1**
   (server wins on tie) plus **Clock-Skew Handling** (future-skew clamp to server time,
   past-skew accept-and-flag). Vector/lamport stamps are explicitly deferred to
   post-v2.0.0 per **Decision**.
3. **Security-field race on push** → resolved by **LWW Rule 2** (security-field
   exception): `acl_allow` and `classification` always take the server value on conflict
   regardless of `modified_at`; an unentitled mutation is discarded, logged at WARN, and
   the body update (if it wins LWW) is applied with server security fields preserved.
4. **Tombstone vs. live-update reordering / resurrection** → resolved by **LWW Rule 3**
   (tombstone always wins) and **Tombstone-then-Resurrection** (`409 record_tombstoned`;
   monotonic lifecycle `version` that never resets; resurrection only via admin API).

---

## Transport

- **Endpoint:** `https://176.9.147.206:443/v2/sync/`
  - `POST /v2/sync/push` — submit changed envelopes.
  - `GET  /v2/sync/pull?store=<name>&cursor=<n>` — fetch delta + `next_cursor`.
- **Auth:** `Authorization: Bearer <JWT>` — an Entra-issued JWT from E-49. **Sync
  endpoints are gated**: a request without a valid, unexpired JWT is rejected with
  `401` before any record is read or written. The JWT principal drives ACL filtering
  on pull and the entitlement check on push.
- **Transport security:** TLS on `:443`. (Certificate strategy for the bare-IP host is
  an infra concern tracked outside this doc.)
- **Encoding:** `application/json` request and response bodies.
- **Limits:**
  - **Pull:** max **1000 records** per response. If more are available the hub returns a
    `next_cursor` short of the head; the client pulls again until `next_cursor` stops
    advancing (drained).
  - **Push:** max **100 records** per request. Larger client backlogs are chunked into
    multiple push calls.

---

## Open Questions — Resolved (T-582)

All four questions raised by T-581 are **resolved** by the Conflict Policy section
above. Each is restated with its resolving sub-section for traceability. See
**Conflict Policy → Open Questions Resolved** for the consolidated mapping.

1. ✅ **Stale-cursor / expired-tombstone gap.** *Original:* when a client presents a
   cursor older than the 30-day tombstone horizon, how does the hub signal "your delta
   is unsafe, do a full re-sync"?
   **Resolved by → Conflict Policy → Stale-Cursor Signaling:** the hub returns a
   distinct `409 {error:"cursor_expired", new_cursor:0}`; the client drops local state
   and re-syncs from `cursor=0`. Not silent `cursor=0` semantics.

2. ✅ **Concurrent edit with equal / clock-skewed `modified_at`.** *Original:* LWW by
   timestamp degenerates when two offline clients produce identical or skewed
   `modified_at`; is `version` + receive-order enough, or is a vector/lamport stamp
   needed?
   **Resolved by → Conflict Policy → LWW Rule 1 + Clock-Skew Handling:** server wins on
   exact tie; future skew (>5 min) is clamped to server time with a WARNING; past skew
   (>7 days) is accepted and flagged in the response only. Vector clocks are explicitly
   **deferred to post-v2.0.0** per the Decision.

3. ✅ **Security-field race on push.** *Original:* when a body update carries an
   out-of-date `acl_allow`, is it rejected, accepted-with-server-ACL, or
   accepted-and-re-broadcast? What is the precedence between body LWW and
   server-authoritative security fields in the same envelope?
   **Resolved by → Conflict Policy → LWW Rule 2 (security-field exception):**
   `acl_allow` and `classification` always defer to the server value regardless of
   `modified_at`; an unentitled mutation is discarded and logged at WARN
   `(principal, resource, attempted_acl)`; the body update, if it wins LWW, is applied
   with server security fields preserved.

4. ✅ **Tombstone vs. live-update reordering / resurrection.** *Original:* if a record
   is deleted and a same-id record is later re-created, how does a client that missed
   the tombstone distinguish resurrection from a new record? Is `version` monotonic
   across the full lifecycle (including post-tombstone)?
   **Resolved by → Conflict Policy → LWW Rule 3 + Tombstone-then-Resurrection:**
   tombstone always wins; a live push against a tombstoned `record_id` (within 30-day
   retention) is rejected `409 {error:"record_tombstoned", tombstone_ts}`; `version` is
   monotonic across the full lifecycle and never resets; resurrection requires an
   explicit admin API call.
