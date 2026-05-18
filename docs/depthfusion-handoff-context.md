# DepthFusion Session Handoff — 2026-05-18 (S-122)

## Branch / State

- Branch: `main`
- HEAD: `daaeb0d`
- Tests: **1901 passed, 0 failed**
- Lint: **0 violations** (ruff)
- Mypy: **0 errors** (102 source files)

---

## What Shipped This Session

### S-122 — Wing/Room Sub-Project Scoping (`daaeb0d`)

Implements Room-level (`sub_scope`) scoping as a thin additive filter on top of the
existing Wing (project) gate. Resolves OD-3.

**ADR:** `docs/decisions/ADR-sub-project-scoping.md`

**Taxonomy:**
- Wing = project slug (existing; no change)
- Room = `sub_scope: <label>` frontmatter field (new)
- Drawer = the file itself (not modelled)

**Filter truth table (ADR-001):**

| Active `sub_scope` | Block's `sub_scope` | Result |
|---|---|---|
| `None` | any | INCLUDED — filter off (back-compat) |
| set | absent | INCLUDED — legacy/universal block |
| set | matches | INCLUDED |
| set | differs | EXCLUDED |

**Invariants:**
- Wing filter runs before Room filter — foreign-project blocks excluded by Wing before Room evaluates them
- `sub_scope` is orthogonal to `mode` — never cleared by a mode change
- No write-time enforcement — advisory at recall time only

**New API surface:**
- `extract_frontmatter_sub_scope(content)` — parses `sub_scope:` from YAML frontmatter
- `_sub_scope_of_block(block)` — resolves Room label (explicit key → frontmatter fallback)
- `_block_passes_sub_scope(block, *, sub_scope)` — truth-table gate
- `filter_blocks_by_sub_scope(blocks, *, sub_scope)` — list-level filter
- `GraphScope.sub_scope: str | None = None` + `to_dict()` method
- `depthfusion_set_scope` extended with `sub_scope` and `projects` schema properties; handler now reads `scope` key (was `mode`; back-compat alias preserved)

**Test coverage:** 24 tests in `TestSubProjectScoping` — truth-table (4 rows), pipeline order, frontmatter fallback, `to_dict`, scope round-trip, `_tool_set_scope` integration (5 cases), back-compat regression

### Review findings fixed (same commit)

- **C-1**: ADR file created at `docs/decisions/ADR-sub-project-scoping.md`
- **H-1**: `depthfusion_set_scope` schema/handler mismatch fixed — handler now reads `scope` (schema key) with `mode` as back-compat alias; `projects` added to schema
- **H-2**: 5 `_tool_set_scope` integration tests added covering schema key, back-compat, sub_scope persistence, empty-string coercion, mode-orthogonality
- **M-2**: `write_scope` DRY'd via `scope.to_dict()` — single serialization source of truth
- Pre-existing F821 lint fix in `tests/test_graph/test_store.py`

---

## Backlog State

**All 38 epics are `[done]`. Backlog is empty.**

No open stories. Next work requires new epics or new stories.

---

## Key File Locations

| Area | File |
|---|---|
| Sub-scope filter functions | `src/depthfusion/retrieval/hybrid.py` |
| GraphScope + to_dict | `src/depthfusion/graph/types.py` |
| Scope persistence | `src/depthfusion/graph/scope.py` |
| MCP server (set_scope + recall wiring) | `src/depthfusion/mcp/server.py` |
| ADR — OD-3 resolution | `docs/decisions/ADR-sub-project-scoping.md` |
| S-122 tests | `tests/test_retrieval/test_hybrid.py` (`TestSubProjectScoping`) |
| Graph store (all backends) | `src/depthfusion/graph/store.py` |
| KG invalidation design doc | `docs/designs/kg-invalidation-state-machine.md` |

---

## Architecture Notes

### Sub-project scoping (S-122)

- Room filter is wired in `_tool_recall_impl` (`server.py`) immediately after `filter_blocks_by_project` — inside the `if current_project:` block
- When `current_project is None` (no git repo detectable), both Wing and Room filters are skipped — documented behavior in ADR-001
- `read_scope()` is called at recall time to retrieve the active `sub_scope`; it returns `None` if no scope file exists (→ Room filter off, back-compat)
- Ingest path (`_load_file` in `server.py`) attaches `file_sub_scope` to each block at load time, mirroring the `file_project` pattern — ensures Room label survives section-splitting

### KG edge invalidation (S-123)

- `valid_until` stored in `edge.metadata["valid_until"]` — no new DB column
- Python-side filtering in `get_edges(as_of=)` via `_edge_active_at()`
- State: ACTIVE (no `valid_until`) → SUPERSEDED (has `valid_until`); one-way

### Linear blend (S-121)

- Gated on `DEPTHFUSION_BLEND_MODE=linear` (default: `rrf`)
- Module-level `_BLEND_MODE` constant in `hybrid.py`

### Temporal validity (S-119)

- `filter_blocks_by_validity(blocks, *, as_of)` — filters by YAML frontmatter `valid_from`/`valid_until`
- `as_of=None` → no filter (back-compat)

---

## Health

| Dimension | Status |
|---|---|
| Tests | 1901/1901 ✓ |
| Lint (ruff) | 0 violations ✓ |
| Types (mypy) | 0 errors (102 files) ✓ |
| CIQS Cat A | ~40.0% (S-115 driver; vector coverage still growing) |
