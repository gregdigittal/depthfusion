# KG Edge Invalidation — State Machine Design (S-123)

## States

```
ACTIVE ──invalidate_edge()──► SUPERSEDED
```

An edge is **ACTIVE** when `edge.metadata` has no `valid_until` key (or it is empty).
An edge is **SUPERSEDED** when `edge.metadata["valid_until"]` is a non-empty ISO-8601 string.
There is no ARCHIVED or DELETED state — physical deletion is out of scope.

## Invariants

1. `valid_until` is always UTC.
2. An edge transitions ACTIVE → SUPERSEDED exactly once. There is no reverse transition.
3. `valid_until` represents the moment the fact was known to be superseded — not
   necessarily the moment the new fact came into being.

## Point-in-Time Semantics

`get_edges(entity_id, as_of=dt)` returns only edges that were ACTIVE at `dt`:

| `valid_until` in metadata | `as_of` | Included? |
|---|---|---|
| absent / empty | any | yes (always active) |
| set | `as_of < valid_until` | yes (not yet superseded) |
| set | `as_of >= valid_until` | no (superseded at or before as_of) |

`as_of=None` → no temporal filter → all edges returned (backward-compatible default).

## Consistency with MemoryObject `.superseded` suffix

When a discovery is superseded via `depthfusion_mark_superseded`:
1. `MemoryObject.status` is set to `SUPERSEDED` in the MemoryStore.
2. The caller (MCP tool or future automation) should also call
   `GraphStore.invalidate_edge(edge_id, valid_until=now)` for any KG edges whose
   `source_type == "decision"` and whose `adapter_name` created that memory.
3. Optionally, the discovery file frontmatter gains `valid_until: <ISO>` so that
   `filter_blocks_by_validity(blocks, as_of=)` (S-119) also excludes the block.

Steps 2 and 3 are **not** enforced atomically in S-123 — consistency is advisory.
A future S-124 / automation sweep can enforce it. S-123 only provides the
`invalidate_edge` primitive and the `get_edges(as_of=)` filter.

## API Surface

```python
# graph/store.py — added to GraphStore protocol + all backends
def invalidate_edge(self, edge_id: str, valid_until: datetime) -> bool:
    """Write valid_until into edge.metadata["valid_until"].

    Returns True if the edge was found and updated, False if not found.
    Idempotent: calling again with a later valid_until overwrites.
    """

# get_edges gains an optional as_of parameter
def get_edges(
    self,
    entity_id: str,
    relationship_filter: list[str] | None = None,
    as_of: datetime | None = None,        # new in S-123
) -> list[Edge]: ...
```

## Out of Scope

- Physical deletion (`delete_edge`) — not implemented here.
- Cascade invalidation (invalidating edges when a connected entity is superseded).
- Atomic consistency between MemoryStore and GraphStore.
- ChromaDB sidecar backend — `invalidate_edge` is JSON+SQLite only (ChromaDB
  backend delegates to its SQLite sidecar for edges via `_edge_db`).
