# DepthFusion V2 — ACL Schema Design

**Status:** Design (T-560)
**Applies to:** E-50 Authorization Model — RBAC + Record ACLs + Classification
**Depends on:** Identity models (`depthfusion.identity.models.Principal`), S-159 RBAC
**Referenced by:** T-561 (migrations), T-562 (write-path enforcement), T-563 (discovery frontmatter)

---

## 1. Overview

Every record in every DepthFusion store carries two ACL fields:

| Field | Type | Purpose |
|---|---|---|
| `acl_allow` | `list[str]` of principal IDs / group refs | Explicit allow list; absence = deny |
| `classification` | enum: `public` / `internal` / `confidential` / `restricted` | Sensitivity tier; controls export, cache, and redaction behaviour |

**Deny semantics (V2):** Explicit deny is not implemented. A principal not present in `acl_allow` (after group expansion) is implicitly denied. Absence equals deny.

**V1 backfill default:** All V1 records receive `acl_allow=["greg"]`, `classification="internal"` per V2-DEC-002.

---

## 2. Principal ID Format

A principal ID is one of:

```
<sub_claim>              # e.g. "00000000-0000-0000-0000-000000000001"
group:<group_name>       # e.g. "group:admins", "group:data-science"
```

`acl_allow` entries are matched exactly against:
- `principal.principal_id` (the OIDC `sub` claim), or
- `"group:" + group_name` for any name in `principal.groups`

---

## 3. Classification Enum

```python
from enum import Enum

class Classification(str, Enum):
    PUBLIC       = "public"        # No restrictions; may appear in unauthenticated contexts
    INTERNAL     = "internal"      # Default for V1 backfill; internal use only
    CONFIDENTIAL = "confidential"  # Need-to-know; export restricted; cache TTL ≤ 1 h
    RESTRICTED   = "restricted"    # Highest tier; no export; no cache; redaction required
```

### Handling Rules per Classification

| Level | Export to LLM context | Cache allowed | Cache TTL | Redaction | Audit log |
|---|---|---|---|---|---|
| `public` | Yes | Yes | 24 h | None | None |
| `internal` | Yes (authenticated) | Yes | 8 h | None | Read |
| `confidential` | Yes (authorized only) | Yes | 1 h | PII fields | Read + Write |
| `restricted` | No (summary only) | No | — | Full redaction | All operations |

---

## 4. Group Expansion Strategy

Groups are **resolved at query time** from the principal record. There is no pre-computed group membership cache in V2.

**Algorithm:**

```python
def principal_matches_acl(principal: Principal, acl_allow: list[str]) -> bool:
    """Return True if principal is permitted by acl_allow.

    Checks:
    1. Exact principal_id match
    2. Any "group:<name>" entry where <name> is in principal.groups
    """
    if principal.principal_id in acl_allow:
        return True
    for entry in acl_allow:
        if entry.startswith("group:"):
            group_name = entry[len("group:"):]
            if group_name in principal.groups:
                return True
    return False
```

**Why query-time expansion:**
- Group membership is sourced from the OIDC token (`groups` claim from Entra ID / OIDC provider).
- No separate group membership store is required in V2.
- Stale-membership risk is bounded by token TTL (device lease: default 24 h).
- Group names are strings sourced from the `groups` claim; no normalization is applied — callers must match the provider's casing.

**Wildcard principal:** The special value `"*"` in `acl_allow` grants access to any authenticated principal. It does not grant unauthenticated access. Use only for `public` or `internal` records where any logged-in user should have visibility.

---

## 5. Record Shape per Store

All SQL columns are `NOT NULL`. JSON representations include both fields at the top level.

### 5.1 MemoryStore (`memories` table — SQLite)

**New columns added by T-561 migration:**

```sql
ALTER TABLE memories ADD COLUMN acl_allow  TEXT NOT NULL DEFAULT '["greg"]';
ALTER TABLE memories ADD COLUMN classification TEXT NOT NULL DEFAULT 'internal';
```

- `acl_allow` is stored as a JSON array string: `'["greg", "group:admins"]'`
- `classification` is stored as a plain string matching `Classification` enum values
- Index: `CREATE INDEX idx_memories_acl ON memories(classification)`

**Pydantic model addition:**

```python
class MemoryACL(BaseModel):
    acl_allow: list[str] = Field(default_factory=lambda: ["greg"])
    classification: Classification = Classification.INTERNAL
```

### 5.2 VectorStore (ChromaDB — metadata dict)

ChromaDB metadata does not support list types natively. ACL fields are stored as:

```python
# At write time — serialize to metadata
metadata["acl_allow"] = json.dumps(acl_allow)       # '["greg","group:admins"]'
metadata["classification"] = classification          # "internal"

# At query time — deserialize from metadata
acl_allow: list[str] = json.loads(doc.metadata.get("acl_allow", '["greg"]'))
classification: Classification = Classification(doc.metadata.get("classification", "internal"))
```

**ChromaDB `where` filter for pre-filtering:**

```python
# Exact principal match (no group expansion at Chroma level)
where={"classification": {"$ne": "restricted"}}
# Post-filter step must apply full group expansion via principal_matches_acl()
```

Note: ChromaDB metadata filters cannot perform group expansion. ACL pre-filter at the Chroma layer is limited to `classification` gating (e.g. exclude `restricted` for non-admin principals). Full `acl_allow` enforcement is applied in the post-rank pass.

### 5.3 EventLog (`events.jsonl` — NDJSON)

Each JSON event line gains two top-level fields:

```json
{
  "event_id": "...",
  "project_id": "...",
  "event_type": "...",
  "timestamp": "...",
  "acl_allow": ["greg", "group:admins"],
  "classification": "internal",
  "payload": { ... }
}
```

- Default values for V1 events (backfill): `acl_allow=["greg"]`, `classification="internal"`
- The backfill script (T-561) rewrites NDJSON in-place using atomic rename; dry-run mode prints lines without writing

**EventLog schema addition (Pydantic):**

```python
class MemoryEvent(BaseModel):
    ...
    acl_allow: list[str] = Field(default_factory=lambda: ["greg"])
    classification: Classification = Classification.INTERNAL
```

### 5.4 FileIndex (`file_metadata` table — SQLite)

**New columns added by T-561 migration:**

```sql
ALTER TABLE file_metadata ADD COLUMN acl_allow     TEXT NOT NULL DEFAULT '["greg"]';
ALTER TABLE file_metadata ADD COLUMN classification TEXT NOT NULL DEFAULT 'internal';
```

- Same serialization as MemoryStore (JSON array string for `acl_allow`)
- `file_path` already serves as primary key; no additional index required beyond `classification`
- FileIndex is write-only from the capture path; `acl_allow` is stamped at index time from the principal that triggered indexing

### 5.5 GraphStore — Entities and Edges

GraphStore has two record types: `Entity` and `Edge`. Both carry ACL fields.

**Entity ACL (added to `Entity` dataclass):**

```python
@dataclass
class Entity:
    ...  # existing fields
    acl_allow: list[str] = field(default_factory=lambda: ["greg"])
    classification: str = "internal"   # Classification enum value
```

**Edge ACL:** Edges inherit the most restrictive classification of their source and target entities. `acl_allow` for edges is the intersection of source and target `acl_allow` lists.

```python
def edge_acl_allow(source: Entity, target: Entity) -> list[str]:
    """Intersection of source and target acl_allow.
    If either list is empty (after backfill), falls back to ["greg"]."""
    src = set(source.acl_allow)
    tgt = set(target.acl_allow)
    intersection = list(src & tgt)
    return intersection if intersection else ["greg"]

def edge_classification(source: Entity, target: Entity) -> Classification:
    """Most restrictive classification of source and target."""
    order = [Classification.PUBLIC, Classification.INTERNAL,
             Classification.CONFIDENTIAL, Classification.RESTRICTED]
    return max(
        Classification(source.classification),
        Classification(target.classification),
        key=lambda c: order.index(c),
    )
```

**SQLite GraphStore schema (entities and edges tables):**

```sql
-- entities table
ALTER TABLE entities ADD COLUMN acl_allow     TEXT NOT NULL DEFAULT '["greg"]';
ALTER TABLE entities ADD COLUMN classification TEXT NOT NULL DEFAULT 'internal';

-- edges table
ALTER TABLE edges ADD COLUMN acl_allow     TEXT NOT NULL DEFAULT '["greg"]';
ALTER TABLE edges ADD COLUMN classification TEXT NOT NULL DEFAULT 'internal';
```

**JSON GraphStore:** The `metadata` dict on Entity/Edge carries `acl_allow` and `classification` as first-class keys (not nested inside `metadata`). Both fields are serialized at the top level in the JSON backend.

### 5.6 Discoveries (YAML frontmatter — Markdown files)

Discoveries are Markdown files stored at `~/.claude/shared/discoveries/` (and project-local `discoveries/` directories). They use YAML frontmatter between `---` fences.

**Frontmatter schema addition:**

```yaml
---
date: 2026-06-11
project: depthfusion
acl_allow:
  - greg
  - group:data-science
classification: internal
# ... existing frontmatter fields ...
---

Discovery content here.
```

**Default values for new discoveries:**
- `acl_allow: [greg]`
- `classification: internal`

**Default values for V1 discoveries (backfill):** T-561 backfill script adds these two fields to every existing discovery file that lacks them, using `atomic_frontmatter_rewrite` from `depthfusion.core.file_locking`.

**Parser contract (T-563):**
- If `acl_allow` is absent from frontmatter → use `["greg"]` (backfill default)
- If `classification` is absent → use `"internal"`
- If `classification` value is unrecognized → reject the write with `ValueError`; do not silently default on read

---

## 6. Write-Path Enforcement

The write path rejects any record missing `acl_allow`. This is enforced at the store layer, not the API layer.

```python
def _validate_acl_fields(acl_allow: list[str], classification: str) -> None:
    """Raise ValueError if ACL fields are invalid.

    Called by every store's write method before persisting.
    """
    if not acl_allow:
        raise ValueError(
            "acl_allow must not be empty. Every record requires at least one "
            "principal or group entry. Use ['*'] for world-readable records."
        )
    try:
        Classification(classification)
    except ValueError:
        raise ValueError(
            f"classification must be one of: "
            f"{[c.value for c in Classification]}. Got: {classification!r}"
        )
```

No record is written to any store without passing `_validate_acl_fields`. This enforces S-160 AC-3.

---

## 7. Query-Time Enforcement

Every query/retrieval function accepts a `principal: Principal` argument. Before returning results the caller applies:

```python
def filter_by_acl(
    records: list[T],
    principal: Principal,
    get_acl: Callable[[T], tuple[list[str], str]],
) -> list[T]:
    """Remove records the principal is not permitted to see.

    get_acl: callable that extracts (acl_allow, classification) from a record.
    """
    return [
        r for r in records
        if principal_matches_acl(principal, get_acl(r)[0])
    ]
```

`restricted` records additionally require the principal to hold the `read_restricted` capability (defined in the RBAC capability matrix, T-557). The `principal_matches_acl` check is a necessary but not sufficient condition for `restricted` records.

---

## 8. Invariants

1. **No empty `acl_allow`:** Every persisted record has `acl_allow` with at least one entry. Enforced at write time by `_validate_acl_fields`.
2. **No unknown classification:** `classification` must be one of the four enum values. Unknown values are rejected at write time.
3. **No explicit deny:** V2 does not support deny-list entries. Absence from `acl_allow` is denial.
4. **V1 backfill is idempotent:** Running the T-561 backfill script twice produces identical results — records already stamped are not re-stamped.
5. **Group expansion is stateless:** No cached membership tables. Groups are always resolved from the live `Principal.groups` list at query time.
6. **Edge ACL is derived, not independent:** Edge `acl_allow` is always the intersection of its source and target entity `acl_allow` lists. Edges cannot grant access beyond what the endpoint entities permit.
7. **`restricted` requires capability check:** Passing `acl_allow` alone is insufficient for `restricted` records. The PolicyEngine (T-568) must also confirm `read_restricted` capability.

---

## 9. Open Questions (V3 scope)

- **Explicit deny:** If SharePoint deny-ACE semantics must be honoured exactly, a deny-list column (`acl_deny`) will be needed. Deferred to V3 per V2-DEC-002.
- **Inheritance / resource hierarchy:** Should child records inherit parent `acl_allow`? Deferred — current design requires explicit stamping at each record.
- **Group membership cache:** If token TTL proves too coarse for enterprise group changes, a short-lived membership cache (e.g. 5 min) may be warranted. Out of scope for V2.

---

## 10. References

- `depthfusion.identity.models.Principal` — principal record with `principal_id` and `groups`
- `V2-DEC-002` — backfill default decision: `acl_allow=["greg"]`, `classification=internal`
- S-160 AC-1/AC-2/AC-3 — acceptance criteria this document satisfies
- T-561 — migrations for all six stores + backfill script
- T-562 — write-path enforcement in MemoryStore/VectorStore/EventLog/Graph
- T-563 — discovery frontmatter ACL parser + writer
- T-568 — PolicyEngine (central policy decision point)
