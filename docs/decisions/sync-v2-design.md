# Sync V2 — Hub-and-Spoke Protocol Design

**Status:** Draft
**Date:** 2026-06-11
**Story:** S-166 (AC-1)
**Task:** T-581
**Supersedes:** `sync.sh` (rsync, deprecated under T-588)
**Downstream:** Lane A (identity), Lane D (platform), T-582 (conflict resolution detail)

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
  the hub signals this by rejecting a stale cursor (see Open Questions).

---

## Conflict Policy (Stub — detail in T-582)

This section is a **working assumption only**; T-582 owns the full design.

- **Body fields:** last-writer-wins by `modified_at`. The envelope with the later
  `modified_at` (hub-validated) wins; ties broken by higher `version`, then by
  `record_id` lexical order as a final deterministic tiebreak.
- **Security fields always defer to the server:** `acl_allow` and `classification` are
  **not** subject to client last-writer-wins. The hub's stored value is authoritative;
  a client push may only change them if the caller is entitled to do so under the
  identity model (Lane A). A client must never be able to widen its own visibility or
  downgrade a classification by racing a write.

Detail — including concurrent-edit semantics, version-gap handling, and the exact
entitlement check for security-field mutation — is **deferred to T-582**.

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

## Open Questions (for T-582 reviewer)

1. **Stale-cursor / expired-tombstone gap.** When a client presents a cursor older than
   the 30-day tombstone horizon, how does the hub signal "your delta is unsafe, do a
   full re-sync"? A distinct `409 Stale Cursor` response vs. silently returning
   `cursor=0` semantics — which keeps offline-safe clients correct without surprising
   them?

2. **Concurrent edit with equal `modified_at` from two clients.** Last-writer-wins by
   timestamp degenerates when two offline clients edit the same `record_id` and their
   clocks produce identical (or clock-skewed) `modified_at` values. Is `version` + hub
   receive-order a sufficient tiebreak, or does T-582 need a vector/lamport stamp to
   avoid a client's edit being silently dropped?

3. **Security-field race on push.** If client A (entitled) narrows `acl_allow` while
   client B (still entitled under the old ACL) concurrently pushes a body update that
   carries the *old* `acl_allow`, does B's push get rejected, accepted-with-server-ACL,
   or accepted-and-re-broadcast? What is the exact precedence between body LWW and
   server-authoritative security fields when they arrive in the same envelope?

4. **Tombstone vs. live-update reordering across stores.** A `record_id` is unique
   within a store, but if a record is deleted and a same-id record is later re-created,
   how does a client that missed the tombstone (version gap) distinguish resurrection
   from a legitimate new record? Is monotonic `version` across a record's full
   lifecycle (including post-tombstone) guaranteed, or can `version` reset?
