# DepthFusion v0.4.0 — Knowledge Graph Entity Linking
# Design Specification | 2026-03-28

---

## Purpose

Add a knowledge graph layer to DepthFusion that:
1. **Improves recall** — entities act as index anchors, expanding BM25 queries with linked concepts
2. **Enables cross-project insight** — entities link across projects when session scope allows
3. **Supports relationship navigation** — explicit graph traversal via `depthfusion_graph_traverse`

---

## Architecture

```
~/.claude/memory/*.md
~/.claude/shared/discoveries/*.md
~/.claude/sessions/*.tmp
        │
        ▼
  [GraphExtractor]  ← runs after PostCompact capture + on-demand via auto_learn
  regex (instant, confidence 1.0)
  + haiku async enrichment (confidence 0.70–0.95)
  + temporal proximity (cross-session edges)
        │
        ▼
  [GraphStore] — tiered
  local  → JSON sidecars + ~/.claude/depthfusion-graph.json
  tier-1 → ~/.claude/depthfusion-graph.db (SQLite, stdlib)
  tier-2 → ChromaDB entity collection (semantic edges)
        │
   ┌────┴────┐
   ▼         ▼
[recall]  [graph_traverse]
auto       explicit MCP
query      tool
expansion
```

**Session scope** is set at session start via an explicit prompt in `depthfusion-session-init.sh`.
Default: per-project isolation. Stored in `~/.claude/.depthfusion-session-scope.json`.

---

## Data Model

All new types in `src/depthfusion/graph/types.py`.

```python
@dataclass
class Entity:
    entity_id: str          # sha256(name + type + project)[:12]
    name: str               # "BM25", "TierManager", "PostCompact hook"
    type: str               # "class" | "function" | "file" | "concept" |
                            # "project" | "decision" | "error_pattern"
    project: str            # "depthfusion" | "agreement_automation" | ...
    source_files: list[str] # memory/discovery files containing this entity
    confidence: float       # 1.0 = regex, 0.70–0.95 = haiku
    first_seen: str         # ISO-8601
    metadata: dict[str, Any]

@dataclass
class Edge:
    edge_id: str
    source_id: str
    target_id: str
    relationship: str       # "CO_OCCURS" | "CAUSES" | "FIXES" |
                            # "DEPENDS_ON" | "REPLACES" | "CONFLICTS_WITH" |
                            # "CO_WORKED_ON"
    weight: float           # 1–3: count of signals that agree
    signals: list[str]      # ["co_occurrence", "haiku", "temporal"]
    metadata: dict[str, Any]

@dataclass
class GraphScope:
    mode: str               # "project" | "cross_project" | "global"
    active_projects: list[str]
    session_id: str
    set_at: str             # ISO-8601

@dataclass
class TraversalResult:
    origin_entity: Entity
    connected: list[tuple[Entity, Edge]]
    source_memories: list[RetrievedChunk]  # from depthfusion.retrieval.types
    depth: int
```

**Confidence threshold:** Entities with `confidence < 0.70` are stored but excluded from query
expansion and rerank boosting. Available for explicit `graph_traverse` calls only.

---

## Entity Types

| Type | Examples | Extraction method |
|------|---------|-------------------|
| `class` | `TierManager`, `RecallPipeline`, `HaikuReranker` | Regex (CamelCase) |
| `function` | `rrf_fuse()`, `apply_reranker()` | Regex (snake_case + `()`) |
| `file` | `hybrid.py`, `server.py` | Regex (path pattern) |
| `concept` | `"BM25 scoring"`, `"RRF fusion"`, `"PostCompact hook"` | Haiku |
| `project` | `depthfusion`, `agreement_automation` | Regex (project patterns) |
| `decision` | `"chose SQLite over ChromaDB for graph"` | Haiku |
| `error_pattern` | `"AttributeError: reranker"`, `"ANTHROPIC_API_KEY not set"` | Haiku |

---

## Edge Creation

Three signals, layered. Edge weight = count of signals that agree (1–3).

| Signal | Trigger | Relationship types produced |
|--------|---------|----------------------------|
| **Co-occurrence** | Two entities in the same memory block | `CO_OCCURS` |
| **Haiku-inferred** | Haiku reads context, labels the edge | `CAUSES`, `FIXES`, `DEPENDS_ON`, `REPLACES`, `CONFLICTS_WITH` |
| **Temporal proximity** | Entities appear across sessions within 48h | `CO_WORKED_ON` |

---

## Storage Tiers

Matches existing DepthFusion tier architecture exactly.

| Tier | Condition | Storage | Query capability |
|------|-----------|---------|-----------------|
| Local | `DEPTHFUSION_MODE=local` | JSON sidecars + `depthfusion-graph.json` | Full-file scan, no traversal index |
| VPS Tier 1 | `DEPTHFUSION_MODE=vps`, corpus < 500 | SQLite `depthfusion-graph.db` | Proper graph traversal, edge filtering |
| VPS Tier 2 | `DEPTHFUSION_MODE=vps`, corpus ≥ 500 | ChromaDB entity collection | Semantic edge discovery via embeddings |

---

## Pipeline Integration

### Query expansion (automatic, inside `recall_relevant`)

```
query: "why did we choose SQLite over ChromaDB for the graph?"
  → extract query entities: ["SQLite", "ChromaDB", "graph"]
  → graph lookup:
      SQLite → REPLACES → ChromaDB (weight 2.4)
      graph  → DEPENDS_ON → TierManager (weight 1.8)
  → expanded BM25 terms: + "tier" "storage" "TierManager"
  → rerank boost: blocks mentioning linked entities +0.15–0.30
```

Query expansion only runs when `DEPTHFUSION_GRAPH_ENABLED=true` and graph has ≥ 1 node.
Expansion adds terms; it never removes original query terms.

### Rerank boost

After `HaikuReranker` scores blocks, blocks containing entities linked to the top-1 result
receive a score boost proportional to edge weight × 0.10. Maximum boost per block: +0.30.
Boost is additive, not multiplicative, to preserve relative reranker ordering.

---

## Session Scope

`depthfusion-session-init.sh` injects this prompt at session start:

```
Graph scope for this session:
  [1] {active_project} only  (default — press Enter)
  [2] cross-project  (all your projects)
  [3] custom — type project names separated by spaces

```

User response written to `~/.claude/.depthfusion-session-scope.json`.
If no response within the hook timeout, default (option 1) is applied silently.

`depthfusion_set_scope` MCP tool allows programmatic override during `/goal` runs that
intentionally cross project boundaries (e.g. SkillForge integration planning sessions).

---

## New MCP Tools

Three tools added to `src/depthfusion/mcp/server.py`:

### `depthfusion_graph_traverse`
Traverse the entity graph from a named entity.

**Parameters:**
- `entity_name` (str) — entity to start from
- `depth` (int, default 1) — traversal depth (1–3)
- `relationship_filter` (list[str], optional) — restrict to specific edge types
- `include_memories` (bool, default true) — return source memory blocks

**Returns:** `TraversalResult` — connected entities, edges, source memory blocks.

### `depthfusion_graph_status`
Report graph health and coverage.

**Returns:** node count, edge count, coverage % (entities per memory file), tier,
last extraction timestamp, entities by type breakdown.

### `depthfusion_set_scope`
Programmatically set session graph scope.

**Parameters:**
- `mode` (str) — `"project"` | `"cross_project"` | `"global"`
- `projects` (list[str], optional) — specific projects for cross_project mode

---

## New Module Layout

```
src/depthfusion/graph/
├── __init__.py
├── types.py          — Entity, Edge, GraphScope, TraversalResult
├── extractor.py      — RegexExtractor, HaikuExtractor, confidence merging
├── linker.py         — CoOccurrenceLinker, HaikuLinker, TemporalLinker
├── store.py          — GraphStore: JSON / SQLite / ChromaDB backends
├── traverser.py      — traverse(), expand_query(), boost_scores()
└── scope.py          — read/write ~/.claude/.depthfusion-session-scope.json

tests/test_graph/
├── __init__.py
├── test_extractor.py — regex extraction, haiku extraction, confidence scoring
├── test_linker.py    — co-occurrence edges, temporal edges, weight accumulation
├── test_store.py     — JSON/SQLite/ChromaDB CRUD, tier switching
├── test_traverser.py — traverse depth, query expansion, score boost
└── test_scope.py     — scope read/write, project filtering, default fallback
```

**Modified files:**
```
src/depthfusion/retrieval/hybrid.py        — query expansion step pre-BM25
src/depthfusion/mcp/server.py              — 3 new tools, graph extraction trigger
src/depthfusion/capture/auto_learn.py      — call GraphExtractor after capture
src/depthfusion/install/install.py         — graph store init on setup
~/.claude/hooks/depthfusion-session-init.sh — scope prompt injection
pyproject.toml                             — no new dependencies (SQLite = stdlib)
```

---

## Feature Flag

`DEPTHFUSION_GRAPH_ENABLED` (default: `false`)

Graph extraction and pipeline integration runs silently alongside existing pipeline.
Existing recall behaviour is **completely unchanged** when flag is off.

Enable after validating extraction quality:
```bash
export DEPTHFUSION_GRAPH_ENABLED=true
```

Added to `depthfusion.env` by installer when `--mode vps` is used.

---

## Testing Strategy

~80 new tests. Target: 408 total (328 existing + 80 new).

- Extractor tests: regex precision, haiku mock responses, confidence merging, threshold filtering
- Linker tests: co-occurrence with known corpus, temporal window boundaries, weight accumulation
- Store tests: JSON round-trip, SQLite schema migrations, ChromaDB collection (skipped without vps-tier2)
- Traverser tests: depth-1 and depth-2 traversal, query expansion term injection, boost score bounds
- Scope tests: prompt parsing, default fallback, project filtering, programmatic override

All haiku calls in tests use `unittest.mock` — no real API calls in the test suite.

---

## Success Criteria

- `depthfusion_graph_traverse("BM25")` returns `TierManager`, `RecallPipeline`, `rrf_fuse()` with correct edge types
- Query "why did we choose SQLite" expands to include "tier", "storage" terms before BM25 runs
- Cross-project session surfaces `BM25` entity appearing in both `depthfusion` and `skillforge` sessions
- 328 existing tests continue to pass unchanged
- `DEPTHFUSION_GRAPH_ENABLED=false` produces identical recall output to v0.3.0

---

## Out of Scope (v0.5.0+)

- Graph visualisation UI
- Entity disambiguation (two projects using the same class name)
- Graph export / import
- Real-time graph updates during active sessions (graph updates on PostCompact only)
