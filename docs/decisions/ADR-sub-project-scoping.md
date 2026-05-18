# ADR-001: Sub-Project Scoping — Wing/Room Taxonomy (OD-3 Resolution)

**Status:** Accepted  
**Date:** 2026-05-18  
**Story:** S-122  
**Closes:** OD-3

---

## Context

DepthFusion's retrieval pipeline already scopes discovery blocks to the active
project (Wing). MemPalace's Wing → Room → Drawer hierarchy provides a second
dimension — a sub-area or work-stream beneath the project. OD-3 asked: how should
"Room" map to the Python standalone without introducing a new first-class hierarchy
type?

## Decision

**Wing = project slug** (existing; no change)  
**Room = `sub_scope: <label>` frontmatter field** (new; single string)  
**Drawer = the file itself** (not modelled — out of scope)

Room is represented as a flat `sub_scope` string rather than a namespace object or
directory prefix. This keeps the taxonomy extension minimal, fully additive, and
reversible.

---

## Filter Semantics (truth table)

| Active Room (`sub_scope`) | Block's `sub_scope` | Result |
|---|---|---|
| `None` | any | **INCLUDED** — Room filter is off (back-compat default) |
| set | absent / `None` | **INCLUDED** — legacy/universal block; no Room tag |
| set | matches active | **INCLUDED** — block is in the active Room |
| set | differs from active | **EXCLUDED** — block belongs to a different Room |

### Additional invariants

1. **Wing filter runs before Room filter** — Room is applied only to blocks that
   survived the project (Wing) gate. A foreign-project block with a matching
   `sub_scope` label is excluded by Wing before Room evaluates it.

2. **Room is orthogonal to mode** — `sub_scope` is never cleared or reset when
   `mode` changes between `project`, `cross_project`, and `global`. A mode change
   is a Wing-level operation; Room state persists until explicitly unset
   (`sub_scope = ""` or omitted).

3. **No write-time enforcement** — `sub_scope` is advisory at recall time only.
   Files without the field are universally included; files with it are scoped.
   There is no validation that a label exists before it is used.

4. **Room labels are single-token slugs** — labels must be bare, non-whitespace
   tokens (e.g. `auth`, `billing`, `onboarding`). Multi-word labels are not
   supported; the frontmatter parser captures only the first non-whitespace token.
   `depthfusion_set_scope` callers are responsible for providing a single-token
   label.

---

## Implementation Contract

The following nine items constitute the complete implementation spec. All nine are
delivered in S-122.

1. `GraphScope.sub_scope: str | None = None` — last field on the dataclass.
   `to_dict()` method serializes all five fields including `sub_scope`.

2. `_sub_scope_of_block(block: dict) -> str | None` — resolves a block's Room
   label in priority order: (1) `block["sub_scope"]` explicit key; (2)
   `extract_frontmatter_sub_scope(block["content"])` frontmatter fallback.
   Returns `None` when no label present.

3. `_block_passes_sub_scope(block, *, sub_scope) -> bool` — the truth-table gate.
   Delegates to `_sub_scope_of_block`.

4. `filter_blocks_by_sub_scope(blocks, *, sub_scope) -> list[dict]` — list-level
   filter. `sub_scope=None` returns a copy of `blocks` unchanged (back-compat).

5. `extract_frontmatter_sub_scope(content: str) -> str | None` — parses
   `sub_scope:` from YAML frontmatter using `_FRONTMATTER_SUB_SCOPE_RE`.

6. Ingest passthrough: `server.py` `_load_file()` reads `file_sub_scope` via
   `extract_frontmatter_sub_scope` and attaches it to each derived block as
   `block["sub_scope"]`. Mirrors the `file_project` pattern.

7. Recall pipeline wiring: `filter_blocks_by_sub_scope` called immediately after
   `filter_blocks_by_project` in `_tool_recall_impl`. Room state comes from
   `read_scope()` called at recall time.

8. `depthfusion_set_scope` MCP tool: `inputSchema` extended with optional
   `sub_scope` string property. Handler parses, strips, and coerces empty string →
   `None`. `GraphScope` constructed with the resolved `sub_scope`.

9. `read_scope` / `write_scope`: persist `sub_scope` as a top-level key in the
   scope JSON. `.get("sub_scope")` on read → `None` when absent (back-compat for
   existing scope files).

---

## Alternatives Considered

| Option | Rejected because |
|---|---|
| Namespace object (`Wing.Room`) | Adds a new type; requires schema migration for GraphScope |
| Directory prefix (`auth/discovery.md`) | Breaks existing file conventions; filesystem coupling |
| Tag overloading (`project: myproj/auth`) | Ambiguous separator; complicates `filter_blocks_by_project` |
| First-class `room` field alongside `project` | Equivalent to chosen approach but less aligned with MemPalace `sub_scope` naming |

---

## Consequences

**Positive:**
- Zero migration — existing files and scope JSON work unchanged.
- Strictly additive — the filter is a no-op when `sub_scope` is `None`.
- Composable — Room filter chains cleanly after the existing Wing filter.
- Reversible — sub_scope can be removed without breaking anything downstream.

**Negative / watch items:**
- Multi-token Room labels are silently truncated by the `(\S+)` regex. Label
  format is a contract convention, not an enforced schema constraint.
- When no project is resolvable (`current_project is None`), the Wing gate is
  skipped and Room filtering is also skipped (Room sits inside the Wing gate).
  This is a documented behavior — see H-2 note in S-122 review.
