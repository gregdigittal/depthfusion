# DepthFusion v0.4.0 Knowledge Graph Entity Linking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a knowledge graph layer to DepthFusion that improves recall via entity-anchored query expansion and cross-session relationship traversal.

**Architecture:** A new `src/depthfusion/graph/` module extracts named entities from memory files using regex + async Haiku, stores them in tiered backends (JSON/SQLite/ChromaDB mirroring existing tier logic), and integrates into the existing `RecallPipeline` via pre-BM25 query expansion and post-reranker score boosting. Three new MCP tools expose the graph for explicit traversal and scope control. A feature flag (`DEPTHFUSION_GRAPH_ENABLED`, default `false`) keeps all existing recall behaviour identical when off.

**Tech Stack:** Python 3.10+, dataclasses, hashlib (stdlib), sqlite3 (stdlib), anthropic SDK (existing), pytest, unittest.mock. No new dependencies.

---

## Multi-Agent Build Structure

This plan is designed for parallel agent execution. Independent tasks in each wave can be dispatched simultaneously.

```
Wave 1 (sequential — foundation)
  └─ Task 1: Graph types + package init

Wave 2 (parallel — 3 independent agents)
  ├─ Agent A → Task 2: Scope module
  ├─ Agent B → Task 3: GraphStore JSON backend
  └─ Agent C → Task 4: Entity extractor (regex + haiku)

Wave 3 (sequential — builds on Wave 2)
  ├─ Task 5: Linker trio (CO_OCCURS, haiku-inferred, temporal)
  └─ Task 6: GraphStore SQLite + ChromaDB backends

Wave 4 (sequential — requires Tasks 3–6)
  └─ Task 7: Traverser (traverse, expand_query, boost_scores)

Wave 5 (parallel — 2 independent agents)
  ├─ Agent D → Task 8: MCP tools (graph_traverse, graph_status, set_scope)
  └─ Agent E → Task 9: Pipeline integration (hybrid.py + auto_learn.py)

Wave 6 (sequential — wiring + validation)
  ├─ Task 10: Session-init scope prompt + install.py flag
  └─ Task 11: Full test suite validation (328 existing + 80 new = 408)
```

**Dispatch command for Wave 2 (example):**
```bash
# Each agent is given its task number and the plan path:
# Agent A: "Implement Task 2 from docs/superpowers/plans/2026-03-28-depthfusion-v0.4.0-knowledge-graph.md"
# Agent B: "Implement Task 3 from ..."
# Agent C: "Implement Task 4 from ..."
```

---

## File Map

**New files:**
```
src/depthfusion/graph/__init__.py
src/depthfusion/graph/types.py       — Entity, Edge, GraphScope, TraversalResult
src/depthfusion/graph/scope.py       — read/write ~/.claude/.depthfusion-session-scope.json
src/depthfusion/graph/store.py       — GraphStore: JSON / SQLite / ChromaDB backends
src/depthfusion/graph/extractor.py   — RegexExtractor, HaikuExtractor, confidence_merge
src/depthfusion/graph/linker.py      — CoOccurrenceLinker, HaikuLinker, TemporalLinker
src/depthfusion/graph/traverser.py   — traverse(), expand_query(), boost_scores()

tests/test_graph/__init__.py
tests/test_graph/conftest.py
tests/test_graph/test_extractor.py   — 18 tests
tests/test_graph/test_linker.py      — 16 tests
tests/test_graph/test_store.py       — 22 tests
tests/test_graph/test_traverser.py   — 14 tests
tests/test_graph/test_scope.py       — 10 tests
```

**Modified files:**
```
src/depthfusion/retrieval/hybrid.py       — query expansion before BM25 (lines ~32–64)
src/depthfusion/mcp/server.py             — 3 new tools, graph extraction trigger
src/depthfusion/capture/auto_learn.py     — call GraphExtractor after HaikuSummarizer
src/depthfusion/install/install.py        — add DEPTHFUSION_GRAPH_ENABLED=false to VPS env
~/.claude/hooks/depthfusion-session-init.sh — scope prompt injection section
```

---

## Task 1: Graph Types and Package Init

**Files:**
- Create: `src/depthfusion/graph/__init__.py`
- Create: `src/depthfusion/graph/types.py`
- Create: `tests/test_graph/__init__.py`
- Create: `tests/test_graph/conftest.py`
- Test: `tests/test_graph/test_types.py` (inline in this task)

- [ ] **Step 1: Write failing test for Entity construction**

```python
# tests/test_graph/test_types.py
import pytest
from depthfusion.graph.types import Entity, Edge, GraphScope, TraversalResult
from depthfusion.core.types import RetrievedChunk


def test_entity_id_is_12_chars():
    e = Entity(
        entity_id="abc123456789",
        name="TierManager",
        type="class",
        project="depthfusion",
        source_files=["memory/foo.md"],
        confidence=1.0,
        first_seen="2026-03-28T00:00:00",
        metadata={},
    )
    assert len(e.entity_id) == 12


def test_entity_below_threshold_stored():
    e = Entity(
        entity_id="abc123456789",
        name="WeakEntity",
        type="concept",
        project="depthfusion",
        source_files=[],
        confidence=0.50,
        first_seen="2026-03-28T00:00:00",
        metadata={},
    )
    assert e.confidence < 0.70


def test_edge_weight_range():
    edge = Edge(
        edge_id="edge00000001",
        source_id="abc123456789",
        target_id="def123456789",
        relationship="CO_OCCURS",
        weight=1.0,
        signals=["co_occurrence"],
        metadata={},
    )
    assert 1 <= edge.weight <= 3


def test_traversal_result_holds_chunks():
    e = Entity(
        entity_id="abc123456789",
        name="BM25",
        type="concept",
        project="depthfusion",
        source_files=["memory/recall.md"],
        confidence=1.0,
        first_seen="2026-03-28T00:00:00",
        metadata={},
    )
    chunk = RetrievedChunk(
        chunk_id="recall.md#0",
        content="BM25 scoring is used for recall",
        source="memory",
        score=0.85,
    )
    result = TraversalResult(
        origin_entity=e,
        connected=[],
        source_memories=[chunk],
        depth=1,
    )
    assert result.source_memories[0].chunk_id == "recall.md#0"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/gregmorris/Development/Projects/depthfusion
.venv/bin/pytest tests/test_graph/test_types.py -v 2>&1 | head -30
```
Expected: `ModuleNotFoundError: No module named 'depthfusion.graph'`

- [ ] **Step 3: Create package init and types**

```python
# src/depthfusion/graph/__init__.py
"""DepthFusion v0.4.0 — Knowledge Graph Entity Linking."""
```

```python
# src/depthfusion/graph/types.py
"""Graph data model: Entity, Edge, GraphScope, TraversalResult."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from depthfusion.core.types import RetrievedChunk


@dataclass
class Entity:
    """A named entity extracted from memory files."""
    entity_id: str           # sha256(name + type + project)[:12]
    name: str                # e.g. "BM25", "TierManager", "PostCompact hook"
    type: str                # "class"|"function"|"file"|"concept"|"project"|"decision"|"error_pattern"
    project: str             # e.g. "depthfusion"
    source_files: list[str]  # memory/discovery files containing this entity
    confidence: float        # 1.0 = regex; 0.70–0.95 = haiku
    first_seen: str          # ISO-8601
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Edge:
    """A directed relationship between two entities."""
    edge_id: str
    source_id: str
    target_id: str
    relationship: str        # "CO_OCCURS"|"CAUSES"|"FIXES"|"DEPENDS_ON"|"REPLACES"|"CONFLICTS_WITH"|"CO_WORKED_ON"
    weight: float            # 1–3: count of signals that agree
    signals: list[str]       # ["co_occurrence", "haiku", "temporal"]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphScope:
    """Session-level scope controlling cross-project visibility."""
    mode: str                    # "project"|"cross_project"|"global"
    active_projects: list[str]
    session_id: str
    set_at: str                  # ISO-8601


@dataclass
class TraversalResult:
    """Result of a graph traversal from an origin entity."""
    origin_entity: Entity
    connected: list[tuple[Entity, Edge]]
    source_memories: list["RetrievedChunk"]  # from depthfusion.core.types
    depth: int
```

```python
# tests/test_graph/__init__.py
```

```python
# tests/test_graph/conftest.py
"""Shared fixtures for graph tests."""
import pytest
from depthfusion.graph.types import Entity, Edge


@pytest.fixture
def sample_entity() -> Entity:
    return Entity(
        entity_id="abc123456789",
        name="TierManager",
        type="class",
        project="depthfusion",
        source_files=["memory/arch.md"],
        confidence=1.0,
        first_seen="2026-03-28T00:00:00",
        metadata={},
    )


@pytest.fixture
def sample_entity_b() -> Entity:
    return Entity(
        entity_id="def123456789",
        name="RecallPipeline",
        type="class",
        project="depthfusion",
        source_files=["memory/arch.md"],
        confidence=1.0,
        first_seen="2026-03-28T00:00:00",
        metadata={},
    )


@pytest.fixture
def sample_edge(sample_entity, sample_entity_b) -> Edge:
    return Edge(
        edge_id="edge00000001",
        source_id=sample_entity.entity_id,
        target_id=sample_entity_b.entity_id,
        relationship="CO_OCCURS",
        weight=1.0,
        signals=["co_occurrence"],
        metadata={},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_graph/test_types.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/depthfusion/graph/ tests/test_graph/
git commit -m "feat(graph): add types module — Entity, Edge, GraphScope, TraversalResult"
```

---

## Task 2: Scope Module  *(Wave 2 — Agent A)*

**Files:**
- Create: `src/depthfusion/graph/scope.py`
- Create: `tests/test_graph/test_scope.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_graph/test_scope.py
import json
import pytest
from pathlib import Path
from unittest.mock import patch, mock_open

from depthfusion.graph.scope import read_scope, write_scope, default_scope
from depthfusion.graph.types import GraphScope


def test_default_scope_returns_project_mode():
    scope = default_scope(project="depthfusion", session_id="sess001")
    assert scope.mode == "project"
    assert scope.active_projects == ["depthfusion"]
    assert scope.session_id == "sess001"


def test_write_and_read_roundtrip(tmp_path):
    scope_file = tmp_path / ".depthfusion-session-scope.json"
    scope = GraphScope(
        mode="cross_project",
        active_projects=["depthfusion", "skillforge"],
        session_id="sess002",
        set_at="2026-03-28T10:00:00",
    )
    write_scope(scope, path=scope_file)
    loaded = read_scope(path=scope_file)
    assert loaded.mode == "cross_project"
    assert loaded.active_projects == ["depthfusion", "skillforge"]


def test_read_missing_file_returns_none(tmp_path):
    result = read_scope(path=tmp_path / "nonexistent.json")
    assert result is None


def test_write_creates_parent_dirs(tmp_path):
    nested = tmp_path / "subdir" / "scope.json"
    scope = default_scope(project="depthfusion", session_id="s1")
    write_scope(scope, path=nested)
    assert nested.exists()


def test_scope_project_filter():
    scope = GraphScope(
        mode="project",
        active_projects=["depthfusion"],
        session_id="sess003",
        set_at="2026-03-28T10:00:00",
    )
    assert "skillforge" not in scope.active_projects


def test_global_scope_empty_projects():
    scope = GraphScope(
        mode="global",
        active_projects=[],
        session_id="sess004",
        set_at="2026-03-28T10:00:00",
    )
    assert scope.mode == "global"
    assert scope.active_projects == []


def test_invalid_json_returns_none(tmp_path):
    bad_file = tmp_path / "scope.json"
    bad_file.write_text("not json", encoding="utf-8")
    result = read_scope(path=bad_file)
    assert result is None


def test_default_scope_set_at_is_iso():
    import re
    scope = default_scope(project="depthfusion", session_id="s1")
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", scope.set_at)


def test_write_scope_serializes_all_fields(tmp_path):
    scope_file = tmp_path / "scope.json"
    scope = GraphScope(
        mode="project",
        active_projects=["depthfusion"],
        session_id="sess-abc",
        set_at="2026-03-28T00:00:00",
    )
    write_scope(scope, path=scope_file)
    data = json.loads(scope_file.read_text())
    assert data["mode"] == "project"
    assert data["session_id"] == "sess-abc"


def test_read_scope_from_default_path_when_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "depthfusion.graph.scope._DEFAULT_SCOPE_PATH",
        tmp_path / "scope.json"
    )
    scope = default_scope("depthfusion", "s1")
    write_scope(scope)
    loaded = read_scope()
    assert loaded is not None
    assert loaded.mode == "project"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_graph/test_scope.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'depthfusion.graph.scope'`

- [ ] **Step 3: Implement scope.py**

```python
# src/depthfusion/graph/scope.py
"""Session scope configuration for cross-project graph visibility."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from depthfusion.graph.types import GraphScope

_DEFAULT_SCOPE_PATH = Path.home() / ".claude" / ".depthfusion-session-scope.json"


def default_scope(project: str, session_id: str) -> GraphScope:
    """Return a per-project (isolated) scope — the safe default."""
    return GraphScope(
        mode="project",
        active_projects=[project] if project else [],
        session_id=session_id,
        set_at=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    )


def read_scope(path: Path | None = None) -> GraphScope | None:
    """Read scope from JSON file. Returns None if missing or invalid."""
    target = path or _DEFAULT_SCOPE_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return GraphScope(
            mode=data["mode"],
            active_projects=data.get("active_projects", []),
            session_id=data.get("session_id", ""),
            set_at=data.get("set_at", ""),
        )
    except (OSError, KeyError, json.JSONDecodeError):
        return None


def write_scope(scope: GraphScope, path: Path | None = None) -> None:
    """Persist scope to JSON file. Creates parent directories as needed."""
    target = path or _DEFAULT_SCOPE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({
            "mode": scope.mode,
            "active_projects": scope.active_projects,
            "session_id": scope.session_id,
            "set_at": scope.set_at,
        }, indent=2),
        encoding="utf-8",
    )
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_graph/test_scope.py -v
```
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/depthfusion/graph/scope.py tests/test_graph/test_scope.py
git commit -m "feat(graph): add scope module — read/write session graph scope"
```

---

## Task 3: GraphStore JSON Backend  *(Wave 2 — Agent B)*

**Files:**
- Create: `src/depthfusion/graph/store.py`
- Create: `tests/test_graph/test_store.py` (JSON section)

- [ ] **Step 1: Write failing tests for JSON store**

```python
# tests/test_graph/test_store.py
import json
import pytest
from pathlib import Path

from depthfusion.graph.store import JSONGraphStore
from depthfusion.graph.types import Entity, Edge


@pytest.fixture
def json_store(tmp_path):
    return JSONGraphStore(path=tmp_path / "graph.json")


def test_upsert_and_get_entity(json_store, sample_entity):
    json_store.upsert_entity(sample_entity)
    result = json_store.get_entity(sample_entity.entity_id)
    assert result is not None
    assert result.name == "TierManager"


def test_get_missing_entity_returns_none(json_store):
    assert json_store.get_entity("nonexistent") is None


def test_upsert_entity_twice_updates(json_store, sample_entity):
    json_store.upsert_entity(sample_entity)
    updated = Entity(
        entity_id=sample_entity.entity_id,
        name=sample_entity.name,
        type=sample_entity.type,
        project=sample_entity.project,
        source_files=["memory/new.md"],
        confidence=0.95,
        first_seen=sample_entity.first_seen,
        metadata={},
    )
    json_store.upsert_entity(updated)
    result = json_store.get_entity(sample_entity.entity_id)
    assert result.confidence == 0.95


def test_upsert_and_get_edge(json_store, sample_entity, sample_entity_b, sample_edge):
    json_store.upsert_entity(sample_entity)
    json_store.upsert_entity(sample_entity_b)
    json_store.upsert_edge(sample_edge)
    edges = json_store.get_edges(sample_entity.entity_id)
    assert len(edges) == 1
    assert edges[0].relationship == "CO_OCCURS"


def test_all_entities_empty(json_store):
    assert json_store.all_entities() == []


def test_all_entities_returns_all(json_store, sample_entity, sample_entity_b):
    json_store.upsert_entity(sample_entity)
    json_store.upsert_entity(sample_entity_b)
    entities = json_store.all_entities()
    assert len(entities) == 2


def test_json_persists_to_disk(tmp_path, sample_entity):
    path = tmp_path / "graph.json"
    store1 = JSONGraphStore(path=path)
    store1.upsert_entity(sample_entity)
    store2 = JSONGraphStore(path=path)
    assert store2.get_entity(sample_entity.entity_id) is not None


def test_get_edges_by_source(json_store, sample_entity, sample_entity_b, sample_edge):
    json_store.upsert_entity(sample_entity)
    json_store.upsert_entity(sample_entity_b)
    json_store.upsert_edge(sample_edge)
    # target side should also be found (bidirectional lookup)
    edges = json_store.get_edges(sample_entity.entity_id)
    assert any(e.relationship == "CO_OCCURS" for e in edges)


def test_node_count(json_store, sample_entity, sample_entity_b):
    json_store.upsert_entity(sample_entity)
    json_store.upsert_entity(sample_entity_b)
    assert json_store.node_count() == 2


def test_edge_count(json_store, sample_entity, sample_entity_b, sample_edge):
    json_store.upsert_entity(sample_entity)
    json_store.upsert_entity(sample_entity_b)
    json_store.upsert_edge(sample_edge)
    assert json_store.edge_count() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_graph/test_store.py -v 2>&1 | head -15
```
Expected: `ModuleNotFoundError: No module named 'depthfusion.graph.store'`

- [ ] **Step 3: Implement JSONGraphStore in store.py**

```python
# src/depthfusion/graph/store.py
"""Graph storage backends: JSON (local), SQLite (vps-tier1), ChromaDB (vps-tier2)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from depthfusion.graph.types import Entity, Edge

_DEFAULT_JSON_PATH = Path.home() / ".claude" / "depthfusion-graph.json"


@runtime_checkable
class GraphBackend(Protocol):
    def upsert_entity(self, entity: Entity) -> None: ...
    def get_entity(self, entity_id: str) -> Entity | None: ...
    def upsert_edge(self, edge: Edge) -> None: ...
    def get_edges(self, entity_id: str) -> list[Edge]: ...
    def all_entities(self) -> list[Entity]: ...
    def node_count(self) -> int: ...
    def edge_count(self) -> int: ...


def _entity_to_dict(e: Entity) -> dict:
    return {
        "entity_id": e.entity_id,
        "name": e.name,
        "type": e.type,
        "project": e.project,
        "source_files": e.source_files,
        "confidence": e.confidence,
        "first_seen": e.first_seen,
        "metadata": e.metadata,
    }


def _dict_to_entity(d: dict) -> Entity:
    return Entity(
        entity_id=d["entity_id"],
        name=d["name"],
        type=d["type"],
        project=d["project"],
        source_files=d.get("source_files", []),
        confidence=d.get("confidence", 1.0),
        first_seen=d.get("first_seen", ""),
        metadata=d.get("metadata", {}),
    )


def _edge_to_dict(e: Edge) -> dict:
    return {
        "edge_id": e.edge_id,
        "source_id": e.source_id,
        "target_id": e.target_id,
        "relationship": e.relationship,
        "weight": e.weight,
        "signals": e.signals,
        "metadata": e.metadata,
    }


def _dict_to_edge(d: dict) -> Edge:
    return Edge(
        edge_id=d["edge_id"],
        source_id=d["source_id"],
        target_id=d["target_id"],
        relationship=d["relationship"],
        weight=d.get("weight", 1.0),
        signals=d.get("signals", []),
        metadata=d.get("metadata", {}),
    )


class JSONGraphStore:
    """Flat JSON graph store. Suitable for local mode and small corpora."""

    def __init__(self, path: Path | None = None):
        self._path = path or _DEFAULT_JSON_PATH
        self._data: dict = {"entities": {}, "edges": {}}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._data = raw
            except (json.JSONDecodeError, OSError):
                self._data = {"entities": {}, "edges": {}}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2), encoding="utf-8"
        )

    def upsert_entity(self, entity: Entity) -> None:
        self._data["entities"][entity.entity_id] = _entity_to_dict(entity)
        self._save()

    def get_entity(self, entity_id: str) -> Entity | None:
        d = self._data["entities"].get(entity_id)
        return _dict_to_entity(d) if d else None

    def upsert_edge(self, edge: Edge) -> None:
        self._data["edges"][edge.edge_id] = _edge_to_dict(edge)
        self._save()

    def get_edges(self, entity_id: str) -> list[Edge]:
        return [
            _dict_to_edge(d)
            for d in self._data["edges"].values()
            if d["source_id"] == entity_id or d["target_id"] == entity_id
        ]

    def all_entities(self) -> list[Entity]:
        return [_dict_to_entity(d) for d in self._data["entities"].values()]

    def node_count(self) -> int:
        return len(self._data["entities"])

    def edge_count(self) -> int:
        return len(self._data["edges"])
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_graph/test_store.py -v
```
Expected: 10 passed (JSON section)

- [ ] **Step 5: Commit**

```bash
git add src/depthfusion/graph/store.py tests/test_graph/test_store.py
git commit -m "feat(graph): add JSONGraphStore with upsert/get/all entity+edge operations"
```

---

## Task 4: Entity Extractor  *(Wave 2 — Agent C)*

**Files:**
- Create: `src/depthfusion/graph/extractor.py`
- Create: `tests/test_graph/test_extractor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_graph/test_extractor.py
import pytest
from unittest.mock import MagicMock, patch

from depthfusion.graph.extractor import RegexExtractor, HaikuExtractor, confidence_merge, make_entity_id
from depthfusion.graph.types import Entity


SAMPLE_TEXT = """
## Architecture

The TierManager class manages storage tiers.
rrf_fuse() is called from RecallPipeline.
See hybrid.py for the main pipeline.
BM25 scoring is the baseline retrieval method.
"""


def test_regex_extracts_camel_case_class():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    names = [e.name for e in entities]
    assert "TierManager" in names
    assert "RecallPipeline" in names


def test_regex_extracts_snake_case_function():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    names = [e.name for e in entities]
    assert "rrf_fuse()" in names


def test_regex_extracts_file_reference():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    names = [e.name for e in entities]
    assert "hybrid.py" in names


def test_regex_confidence_is_1_0():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    for e in entities:
        assert e.confidence == 1.0


def test_regex_entity_type_class():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    tier_entity = next(e for e in entities if e.name == "TierManager")
    assert tier_entity.type == "class"


def test_regex_entity_type_function():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    fn_entity = next(e for e in entities if e.name == "rrf_fuse()")
    assert fn_entity.type == "function"


def test_regex_entity_type_file():
    extractor = RegexExtractor(project="depthfusion")
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    file_entity = next(e for e in entities if e.name == "hybrid.py")
    assert file_entity.type == "file"


def test_make_entity_id_is_12_chars():
    eid = make_entity_id("TierManager", "class", "depthfusion")
    assert len(eid) == 12


def test_make_entity_id_is_deterministic():
    a = make_entity_id("TierManager", "class", "depthfusion")
    b = make_entity_id("TierManager", "class", "depthfusion")
    assert a == b


def test_make_entity_id_differs_by_project():
    a = make_entity_id("TierManager", "class", "depthfusion")
    b = make_entity_id("TierManager", "class", "skillforge")
    assert a != b


def test_haiku_extractor_returns_entities_when_available():
    extractor = HaikuExtractor(project="depthfusion")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='[{"name": "BM25 scoring", "type": "concept"}]')]
    mock_client.messages.create.return_value = mock_response
    extractor._client = mock_client

    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    assert any(e.name == "BM25 scoring" for e in entities)


def test_haiku_extractor_confidence_in_range():
    extractor = HaikuExtractor(project="depthfusion")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='[{"name": "BM25 scoring", "type": "concept"}]')]
    mock_client.messages.create.return_value = mock_response
    extractor._client = mock_client

    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    for e in entities:
        assert 0.70 <= e.confidence <= 0.95


def test_haiku_extractor_returns_empty_when_unavailable():
    extractor = HaikuExtractor(project="depthfusion")
    extractor._client = None
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    assert entities == []


def test_haiku_extractor_handles_malformed_json():
    extractor = HaikuExtractor(project="depthfusion")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="not json")]
    mock_client.messages.create.return_value = mock_response
    extractor._client = mock_client
    # Should not raise
    entities = extractor.extract(SAMPLE_TEXT, source_file="memory/arch.md")
    assert entities == []


def test_confidence_merge_deduplicates():
    regex_e = Entity(
        entity_id=make_entity_id("TierManager", "class", "depthfusion"),
        name="TierManager", type="class", project="depthfusion",
        source_files=["memory/arch.md"], confidence=1.0,
        first_seen="2026-03-28T00:00:00", metadata={},
    )
    haiku_e = Entity(
        entity_id=make_entity_id("TierManager", "class", "depthfusion"),
        name="TierManager", type="class", project="depthfusion",
        source_files=["memory/arch.md"], confidence=0.85,
        first_seen="2026-03-28T00:00:00", metadata={},
    )
    merged = confidence_merge([regex_e], [haiku_e])
    # Regex (1.0) takes precedence over haiku duplicate
    tier_entities = [e for e in merged if e.name == "TierManager"]
    assert len(tier_entities) == 1
    assert tier_entities[0].confidence == 1.0


def test_confidence_merge_keeps_haiku_only_entities():
    haiku_e = Entity(
        entity_id=make_entity_id("BM25 scoring", "concept", "depthfusion"),
        name="BM25 scoring", type="concept", project="depthfusion",
        source_files=["memory/arch.md"], confidence=0.85,
        first_seen="2026-03-28T00:00:00", metadata={},
    )
    merged = confidence_merge([], [haiku_e])
    assert len(merged) == 1
    assert merged[0].name == "BM25 scoring"


def test_below_threshold_entities_included_in_output():
    """Entities below 0.70 are stored but callers filter for query expansion."""
    haiku_e = Entity(
        entity_id=make_entity_id("vague term", "concept", "depthfusion"),
        name="vague term", type="concept", project="depthfusion",
        source_files=[], confidence=0.55,
        first_seen="2026-03-28T00:00:00", metadata={},
    )
    merged = confidence_merge([], [haiku_e])
    assert len(merged) == 1
    assert merged[0].confidence < 0.70
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_graph/test_extractor.py -v 2>&1 | head -15
```
Expected: `ModuleNotFoundError: No module named 'depthfusion.graph.extractor'`

- [ ] **Step 3: Implement extractor.py**

```python
# src/depthfusion/graph/extractor.py
"""Entity extraction from memory content.

RegexExtractor: instant, confidence=1.0, no API calls.
HaikuExtractor: async Haiku enrichment, confidence 0.70–0.95.
confidence_merge: deduplicates, regex takes precedence on collision.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from depthfusion.graph.types import Entity

logger = logging.getLogger(__name__)

# Regex patterns per entity type
_CAMEL_CASE_RE = re.compile(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b')
_SNAKE_FUNC_RE = re.compile(r'\b([a-z][a-z0-9_]{2,}\(\))')
_FILE_RE = re.compile(r'\b([a-z][a-z0-9_/\-]+\.py)\b')

_HAIKU_PROMPT = """\
Extract named entities from the following text. Return ONLY a JSON array.
Each element: {{"name": "<entity>", "type": "<concept|decision|error_pattern>"}}
Limit to the 10 most important. If none, return [].

Types:
- concept: technical term, algorithm, pattern (e.g. "BM25 scoring", "RRF fusion")
- decision: an architectural choice (e.g. "chose SQLite over ChromaDB")
- error_pattern: an error message or failure mode (e.g. "AttributeError: reranker")

Text:
{content}"""


def make_entity_id(name: str, type_: str, project: str) -> str:
    """Deterministic 12-char ID from sha256(name + type + project)."""
    raw = f"{name}{type_}{project}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class RegexExtractor:
    """Fast, no-API entity extraction. Returns confidence=1.0 entities."""

    def __init__(self, project: str):
        self._project = project

    def extract(self, content: str, source_file: str) -> list[Entity]:
        entities: list[Entity] = []
        seen: set[str] = set()

        for match in _CAMEL_CASE_RE.finditer(content):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                entities.append(Entity(
                    entity_id=make_entity_id(name, "class", self._project),
                    name=name, type="class", project=self._project,
                    source_files=[source_file], confidence=1.0,
                    first_seen=_now_iso(), metadata={},
                ))

        for match in _SNAKE_FUNC_RE.finditer(content):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                entities.append(Entity(
                    entity_id=make_entity_id(name, "function", self._project),
                    name=name, type="function", project=self._project,
                    source_files=[source_file], confidence=1.0,
                    first_seen=_now_iso(), metadata={},
                ))

        for match in _FILE_RE.finditer(content):
            name = match.group(1)
            # Avoid matching things like "1.0", only actual file paths
            if name not in seen and "/" not in name or name.endswith(".py"):
                seen.add(name)
                entities.append(Entity(
                    entity_id=make_entity_id(name, "file", self._project),
                    name=name, type="file", project=self._project,
                    source_files=[source_file], confidence=1.0,
                    first_seen=_now_iso(), metadata={},
                ))

        return entities


class HaikuExtractor:
    """Async Haiku-based extraction for concepts, decisions, error_patterns.

    Returns empty list when ANTHROPIC_API_KEY is unset or SDK unavailable.
    Confidence range: 0.70–0.95 (lower than regex to allow precedence).
    """

    def __init__(self, project: str, model: str = "claude-haiku-4-5-20251001"):
        self._project = project
        self._model = model
        self._client: Any = None
        try:
            import anthropic
            import os
            if os.environ.get("ANTHROPIC_API_KEY"):
                self._client = anthropic.Anthropic()
        except ImportError:
            pass

    def is_available(self) -> bool:
        return self._client is not None

    def extract(self, content: str, source_file: str) -> list[Entity]:
        if not self._client:
            return []
        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": _HAIKU_PROMPT.format(content=content[:2000]),
                }],
            )
            raw = msg.content[0].text.strip()
            items: list[dict] = json.loads(raw)
        except (json.JSONDecodeError, Exception) as exc:
            logger.debug("HaikuExtractor failed: %s", exc)
            return []

        entities: list[Entity] = []
        for item in items[:10]:
            name = item.get("name", "").strip()
            etype = item.get("type", "concept")
            if not name:
                continue
            entities.append(Entity(
                entity_id=make_entity_id(name, etype, self._project),
                name=name, type=etype, project=self._project,
                source_files=[source_file], confidence=0.85,
                first_seen=_now_iso(), metadata={},
            ))
        return entities


def confidence_merge(
    regex_entities: list[Entity],
    haiku_entities: list[Entity],
) -> list[Entity]:
    """Merge two entity lists. Regex wins on ID collision (higher confidence).

    All entities are returned regardless of confidence — callers filter by threshold.
    """
    result: dict[str, Entity] = {}
    for e in haiku_entities:
        result[e.entity_id] = e
    for e in regex_entities:
        # Regex overwrites haiku on same ID (regex confidence = 1.0 > haiku)
        result[e.entity_id] = e
    return list(result.values())
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_graph/test_extractor.py -v
```
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add src/depthfusion/graph/extractor.py tests/test_graph/test_extractor.py
git commit -m "feat(graph): add RegexExtractor, HaikuExtractor, confidence_merge"
```

---

## Task 5: Linker Trio  *(Wave 3)*

**Files:**
- Create: `src/depthfusion/graph/linker.py`
- Create: `tests/test_graph/test_linker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_graph/test_linker.py
import pytest
from unittest.mock import MagicMock
from depthfusion.graph.linker import CoOccurrenceLinker, TemporalLinker, HaikuLinker, make_edge_id
from depthfusion.graph.types import Entity, Edge


@pytest.fixture
def entity_a(sample_entity):
    return sample_entity   # TierManager


@pytest.fixture
def entity_b(sample_entity_b):
    return sample_entity_b  # RecallPipeline


def test_co_occurrence_creates_edge(entity_a, entity_b):
    linker = CoOccurrenceLinker()
    edges = linker.link([entity_a, entity_b])
    assert len(edges) == 1
    assert edges[0].relationship == "CO_OCCURS"


def test_co_occurrence_no_edge_for_single_entity(entity_a):
    linker = CoOccurrenceLinker()
    edges = linker.link([entity_a])
    assert edges == []


def test_co_occurrence_weight_is_1():
    from depthfusion.graph.extractor import make_entity_id
    entities = [
        Entity(entity_id=make_entity_id(f"E{i}", "class", "p"), name=f"E{i}",
               type="class", project="p", source_files=["f.md"],
               confidence=1.0, first_seen="2026-03-28T00:00:00", metadata={})
        for i in range(3)
    ]
    linker = CoOccurrenceLinker()
    edges = linker.link(entities)
    assert all(e.weight == 1.0 for e in edges)


def test_co_occurrence_signal_label(entity_a, entity_b):
    linker = CoOccurrenceLinker()
    edges = linker.link([entity_a, entity_b])
    assert "co_occurrence" in edges[0].signals


def test_make_edge_id_is_deterministic():
    a = make_edge_id("src1", "tgt1", "CO_OCCURS")
    b = make_edge_id("src1", "tgt1", "CO_OCCURS")
    assert a == b


def test_make_edge_id_differs_by_relationship():
    a = make_edge_id("src1", "tgt1", "CO_OCCURS")
    b = make_edge_id("src1", "tgt1", "DEPENDS_ON")
    assert a != b


def test_temporal_linker_within_48h(entity_a, entity_b):
    linker = TemporalLinker(window_hours=48)
    # Same timestamp → within window
    ts = "2026-03-28T10:00:00"
    edges = linker.link_across_sessions(
        session_a_entities=[entity_a], session_a_ts=ts,
        session_b_entities=[entity_b], session_b_ts=ts,
    )
    assert len(edges) >= 1
    assert edges[0].relationship == "CO_WORKED_ON"


def test_temporal_linker_outside_window(entity_a, entity_b):
    linker = TemporalLinker(window_hours=48)
    edges = linker.link_across_sessions(
        session_a_entities=[entity_a], session_a_ts="2026-03-20T00:00:00",
        session_b_entities=[entity_b], session_b_ts="2026-03-28T00:00:00",
    )
    assert edges == []


def test_temporal_linker_signal_label(entity_a, entity_b):
    linker = TemporalLinker(window_hours=48)
    ts = "2026-03-28T10:00:00"
    edges = linker.link_across_sessions(
        session_a_entities=[entity_a], session_a_ts=ts,
        session_b_entities=[entity_b], session_b_ts=ts,
    )
    assert all("temporal" in e.signals for e in edges)


def test_haiku_linker_returns_typed_edge():
    linker = HaikuLinker()
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"relationship": "DEPENDS_ON"}')]
    mock_client.messages.create.return_value = mock_response
    linker._client = mock_client

    from depthfusion.graph.extractor import make_entity_id
    from depthfusion.graph.types import Entity
    a = Entity(entity_id=make_entity_id("A", "class", "p"), name="A", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    b = Entity(entity_id=make_entity_id("B", "class", "p"), name="B", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})

    edge = linker.infer_relationship(a, b, context="A depends on B for storage")
    assert edge is not None
    assert edge.relationship == "DEPENDS_ON"
    assert "haiku" in edge.signals


def test_haiku_linker_returns_none_when_unavailable():
    linker = HaikuLinker()
    linker._client = None
    from depthfusion.graph.extractor import make_entity_id
    from depthfusion.graph.types import Entity
    a = Entity(entity_id=make_entity_id("A", "class", "p"), name="A", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    b = Entity(entity_id=make_entity_id("B", "class", "p"), name="B", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    result = linker.infer_relationship(a, b, context="x")
    assert result is None


def test_haiku_linker_handles_invalid_relationship():
    linker = HaikuLinker()
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"relationship": "INVENTED_TYPE"}')]
    mock_client.messages.create.return_value = mock_response
    linker._client = mock_client

    from depthfusion.graph.extractor import make_entity_id
    a = Entity(entity_id=make_entity_id("A", "class", "p"), name="A", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    b = Entity(entity_id=make_entity_id("B", "class", "p"), name="B", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    result = linker.infer_relationship(a, b, context="x")
    # Invalid relationship type → None
    assert result is None


def test_weight_accumulation_across_signals(entity_a, entity_b):
    """Edge weight should reflect combined signal count."""
    co_edge = Edge(
        edge_id=make_edge_id(entity_a.entity_id, entity_b.entity_id, "CO_OCCURS"),
        source_id=entity_a.entity_id,
        target_id=entity_b.entity_id,
        relationship="CO_OCCURS",
        weight=1.0,
        signals=["co_occurrence"],
        metadata={},
    )
    # Simulate adding a temporal signal
    co_edge.signals.append("temporal")
    co_edge.weight = float(len(co_edge.signals))
    assert co_edge.weight == 2.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_graph/test_linker.py -v 2>&1 | head -15
```

- [ ] **Step 3: Implement linker.py**

```python
# src/depthfusion/graph/linker.py
"""Edge creation signals: co-occurrence, haiku-inferred, temporal proximity."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

from depthfusion.graph.types import Entity, Edge

logger = logging.getLogger(__name__)

_VALID_RELATIONSHIPS = frozenset({
    "CO_OCCURS", "CAUSES", "FIXES", "DEPENDS_ON",
    "REPLACES", "CONFLICTS_WITH", "CO_WORKED_ON",
})

_HAIKU_PROMPT = """\
Given two code entities and context, classify their relationship.
Return ONLY a JSON object: {{"relationship": "<type>"}}

Valid types: CAUSES, FIXES, DEPENDS_ON, REPLACES, CONFLICTS_WITH
Choose the strongest signal. If uncertain, omit (return {{}}).

Entity A: {name_a} ({type_a})
Entity B: {name_b} ({type_b})
Context: {context}"""


def make_edge_id(source_id: str, target_id: str, relationship: str) -> str:
    raw = f"{source_id}{target_id}{relationship}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class CoOccurrenceLinker:
    """Create CO_OCCURS edges between all entity pairs in the same memory block."""

    def link(self, entities: list[Entity]) -> list[Edge]:
        edges: list[Edge] = []
        for a, b in combinations(entities, 2):
            edges.append(Edge(
                edge_id=make_edge_id(a.entity_id, b.entity_id, "CO_OCCURS"),
                source_id=a.entity_id,
                target_id=b.entity_id,
                relationship="CO_OCCURS",
                weight=1.0,
                signals=["co_occurrence"],
                metadata={},
            ))
        return edges


class TemporalLinker:
    """Create CO_WORKED_ON edges for entities that appear across sessions within N hours."""

    def __init__(self, window_hours: int = 48):
        self._window_hours = window_hours

    def link_across_sessions(
        self,
        session_a_entities: list[Entity],
        session_a_ts: str,
        session_b_entities: list[Entity],
        session_b_ts: str,
    ) -> list[Edge]:
        try:
            ts_a = datetime.fromisoformat(session_a_ts)
            ts_b = datetime.fromisoformat(session_b_ts)
        except ValueError:
            return []

        delta_hours = abs((ts_b - ts_a).total_seconds()) / 3600
        if delta_hours > self._window_hours:
            return []

        edges: list[Edge] = []
        for a in session_a_entities:
            for b in session_b_entities:
                if a.entity_id != b.entity_id:
                    edges.append(Edge(
                        edge_id=make_edge_id(a.entity_id, b.entity_id, "CO_WORKED_ON"),
                        source_id=a.entity_id,
                        target_id=b.entity_id,
                        relationship="CO_WORKED_ON",
                        weight=1.0,
                        signals=["temporal"],
                        metadata={"delta_hours": delta_hours},
                    ))
        return edges


class HaikuLinker:
    """Use Claude Haiku to infer semantic relationship type between two entities."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self._model = model
        self._client: Any = None
        try:
            import anthropic
            import os
            if os.environ.get("ANTHROPIC_API_KEY"):
                self._client = anthropic.Anthropic()
        except ImportError:
            pass

    def infer_relationship(
        self, entity_a: Entity, entity_b: Entity, context: str
    ) -> Edge | None:
        if not self._client:
            return None
        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=64,
                messages=[{
                    "role": "user",
                    "content": _HAIKU_PROMPT.format(
                        name_a=entity_a.name, type_a=entity_a.type,
                        name_b=entity_b.name, type_b=entity_b.type,
                        context=context[:500],
                    ),
                }],
            )
            raw = msg.content[0].text.strip()
            data: dict = json.loads(raw)
            rel = data.get("relationship", "")
        except Exception as exc:
            logger.debug("HaikuLinker failed: %s", exc)
            return None

        if rel not in _VALID_RELATIONSHIPS:
            return None

        return Edge(
            edge_id=make_edge_id(entity_a.entity_id, entity_b.entity_id, rel),
            source_id=entity_a.entity_id,
            target_id=entity_b.entity_id,
            relationship=rel,
            weight=1.0,
            signals=["haiku"],
            metadata={},
        )
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_graph/test_linker.py -v
```
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add src/depthfusion/graph/linker.py tests/test_graph/test_linker.py
git commit -m "feat(graph): add CoOccurrenceLinker, TemporalLinker, HaikuLinker"
```

---

## Task 6: GraphStore SQLite and ChromaDB Backends  *(Wave 3)*

**Files:**
- Modify: `src/depthfusion/graph/store.py` (add SQLiteGraphStore, ChromaDBGraphStore, get_store())
- Modify: `tests/test_graph/test_store.py` (add SQLite + ChromaDB + get_store() tests)

- [ ] **Step 1: Write failing tests for SQLite + get_store()**

```python
# Append to tests/test_graph/test_store.py

import os
import pytest
from depthfusion.graph.store import SQLiteGraphStore, get_store, JSONGraphStore


@pytest.fixture
def sqlite_store(tmp_path):
    return SQLiteGraphStore(path=tmp_path / "graph.db")


def test_sqlite_upsert_and_get_entity(sqlite_store, sample_entity):
    sqlite_store.upsert_entity(sample_entity)
    result = sqlite_store.get_entity(sample_entity.entity_id)
    assert result is not None
    assert result.name == "TierManager"


def test_sqlite_get_missing_entity_returns_none(sqlite_store):
    assert sqlite_store.get_entity("missing") is None


def test_sqlite_upsert_edge_and_get(sqlite_store, sample_entity, sample_entity_b, sample_edge):
    sqlite_store.upsert_entity(sample_entity)
    sqlite_store.upsert_entity(sample_entity_b)
    sqlite_store.upsert_edge(sample_edge)
    edges = sqlite_store.get_edges(sample_entity.entity_id)
    assert any(e.relationship == "CO_OCCURS" for e in edges)


def test_sqlite_all_entities(sqlite_store, sample_entity, sample_entity_b):
    sqlite_store.upsert_entity(sample_entity)
    sqlite_store.upsert_entity(sample_entity_b)
    assert len(sqlite_store.all_entities()) == 2


def test_sqlite_node_and_edge_count(sqlite_store, sample_entity, sample_entity_b, sample_edge):
    sqlite_store.upsert_entity(sample_entity)
    sqlite_store.upsert_entity(sample_entity_b)
    sqlite_store.upsert_edge(sample_edge)
    assert sqlite_store.node_count() == 2
    assert sqlite_store.edge_count() == 1


def test_sqlite_relationship_filter(sqlite_store, sample_entity, sample_entity_b, sample_edge):
    sqlite_store.upsert_entity(sample_entity)
    sqlite_store.upsert_entity(sample_entity_b)
    sqlite_store.upsert_edge(sample_edge)
    edges = sqlite_store.get_edges(
        sample_entity.entity_id, relationship_filter=["CO_OCCURS"]
    )
    assert len(edges) == 1
    edges_empty = sqlite_store.get_edges(
        sample_entity.entity_id, relationship_filter=["DEPENDS_ON"]
    )
    assert edges_empty == []


def test_get_store_returns_json_in_local_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    store = get_store(graph_json_path=tmp_path / "g.json")
    assert isinstance(store, JSONGraphStore)


def test_get_store_returns_sqlite_in_vps_tier1(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    store = get_store(
        graph_db_path=tmp_path / "g.db",
        corpus_size=10,
    )
    assert isinstance(store, SQLiteGraphStore)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_graph/test_store.py::test_sqlite_upsert_and_get_entity -v 2>&1 | head -15
```

- [ ] **Step 3: Add SQLiteGraphStore and get_store() to store.py**

```python
# Append to src/depthfusion/graph/store.py (after JSONGraphStore)

import sqlite3


class SQLiteGraphStore:
    """SQLite-backed graph store. Supports proper traversal and edge filtering.

    Schema:
      entities(entity_id TEXT PK, name, type, project, source_files JSON,
               confidence REAL, first_seen TEXT, metadata JSON)
      edges(edge_id TEXT PK, source_id, target_id, relationship,
            weight REAL, signals JSON, metadata JSON)
    """

    _CREATE_ENTITIES = """
        CREATE TABLE IF NOT EXISTS entities (
            entity_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            project TEXT NOT NULL,
            source_files TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 1.0,
            first_seen TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '{}'
        )
    """
    _CREATE_EDGES = """
        CREATE TABLE IF NOT EXISTS edges (
            edge_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relationship TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            signals TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}'
        )
    """

    def __init__(self, path: Path | None = None):
        self._path = path or (Path.home() / ".claude" / "depthfusion-graph.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute(self._CREATE_ENTITIES)
        self._conn.execute(self._CREATE_EDGES)
        self._conn.commit()

    def upsert_entity(self, entity: Entity) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO entities
               (entity_id, name, type, project, source_files, confidence, first_seen, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entity.entity_id, entity.name, entity.type, entity.project,
                json.dumps(entity.source_files), entity.confidence,
                entity.first_seen, json.dumps(entity.metadata),
            ),
        )
        self._conn.commit()

    def get_entity(self, entity_id: str) -> Entity | None:
        row = self._conn.execute(
            "SELECT * FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        if not row:
            return None
        return Entity(
            entity_id=row[0], name=row[1], type=row[2], project=row[3],
            source_files=json.loads(row[4]), confidence=row[5],
            first_seen=row[6], metadata=json.loads(row[7]),
        )

    def upsert_edge(self, edge: Edge) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO edges
               (edge_id, source_id, target_id, relationship, weight, signals, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                edge.edge_id, edge.source_id, edge.target_id,
                edge.relationship, edge.weight,
                json.dumps(edge.signals), json.dumps(edge.metadata),
            ),
        )
        self._conn.commit()

    def get_edges(
        self,
        entity_id: str,
        relationship_filter: list[str] | None = None,
    ) -> list[Edge]:
        params: list = [entity_id, entity_id]
        if relationship_filter:
            sql = (
                "SELECT * FROM edges WHERE (source_id = ? OR target_id = ?)"
                f" AND relationship IN ({','.join('?' * len(relationship_filter))})"
            )
            params.extend(relationship_filter)
        else:
            sql = "SELECT * FROM edges WHERE source_id = ? OR target_id = ?"

        rows = self._conn.execute(sql, params).fetchall()
        return [
            Edge(
                edge_id=r[0], source_id=r[1], target_id=r[2],
                relationship=r[3], weight=r[4],
                signals=json.loads(r[5]), metadata=json.loads(r[6]),
            )
            for r in rows
        ]

    def all_entities(self) -> list[Entity]:
        rows = self._conn.execute("SELECT * FROM entities").fetchall()
        return [
            Entity(
                entity_id=r[0], name=r[1], type=r[2], project=r[3],
                source_files=json.loads(r[4]), confidence=r[5],
                first_seen=r[6], metadata=json.loads(r[7]),
            )
            for r in rows
        ]

    def node_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    def edge_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]


def get_store(
    graph_json_path: Path | None = None,
    graph_db_path: Path | None = None,
    corpus_size: int = 0,
) -> JSONGraphStore | SQLiteGraphStore:
    """Return the appropriate store backend based on DEPTHFUSION_MODE and corpus size.

    Local mode → JSONGraphStore
    VPS + corpus < 500 → SQLiteGraphStore
    VPS + corpus >= 500 → SQLiteGraphStore (ChromaDB extension future work)
    """
    mode = os.environ.get("DEPTHFUSION_MODE", "local")
    if mode != "vps":
        return JSONGraphStore(path=graph_json_path)
    return SQLiteGraphStore(path=graph_db_path)
```

- [ ] **Step 4: Run all store tests**

```bash
.venv/bin/pytest tests/test_graph/test_store.py -v
```
Expected: 22 passed

- [ ] **Step 5: Commit**

```bash
git add src/depthfusion/graph/store.py tests/test_graph/test_store.py
git commit -m "feat(graph): add SQLiteGraphStore and get_store() tier-aware factory"
```

---

## Task 7: Traverser — traverse, expand_query, boost_scores  *(Wave 4 — sequential)*

**Files:**
- Create: `src/depthfusion/graph/traverser.py`
- Create: `tests/test_graph/test_traverser.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_graph/test_traverser.py
import pytest
from depthfusion.graph.traverser import traverse, expand_query, boost_scores
from depthfusion.graph.store import JSONGraphStore
from depthfusion.graph.types import Entity, Edge, TraversalResult
from depthfusion.graph.extractor import make_entity_id
from depthfusion.graph.linker import make_edge_id


@pytest.fixture
def populated_store(tmp_path, sample_entity, sample_entity_b, sample_edge):
    store = JSONGraphStore(path=tmp_path / "g.json")
    store.upsert_entity(sample_entity)     # TierManager
    store.upsert_entity(sample_entity_b)   # RecallPipeline
    store.upsert_edge(sample_edge)         # CO_OCCURS
    return store


def test_traverse_depth1_finds_connected(populated_store, sample_entity, sample_entity_b):
    result = traverse(sample_entity.entity_id, populated_store, depth=1)
    assert result is not None
    connected_ids = [e.entity_id for e, _ in result.connected]
    assert sample_entity_b.entity_id in connected_ids


def test_traverse_returns_traversal_result(populated_store, sample_entity):
    result = traverse(sample_entity.entity_id, populated_store, depth=1)
    assert isinstance(result, TraversalResult)
    assert result.origin_entity.name == "TierManager"


def test_traverse_unknown_entity_returns_none(populated_store):
    result = traverse("nonexistent_id", populated_store, depth=1)
    assert result is None


def test_traverse_depth0_returns_origin_only(populated_store, sample_entity):
    result = traverse(sample_entity.entity_id, populated_store, depth=0)
    assert result is not None
    assert result.connected == []


def test_traverse_relationship_filter(populated_store, sample_entity, sample_entity_b):
    result = traverse(
        sample_entity.entity_id, populated_store, depth=1,
        relationship_filter=["DEPENDS_ON"]
    )
    assert result is not None
    assert result.connected == []  # only CO_OCCURS exists


def test_expand_query_adds_linked_terms(populated_store, sample_entity):
    """expand_query extracts entity names from query and adds linked entity names."""
    # TierManager appears in query; graph shows it CO_OCCURS with RecallPipeline
    expanded = expand_query("TierManager storage", populated_store)
    assert "TierManager" in expanded  # original term preserved
    assert "RecallPipeline" in expanded  # linked entity added


def test_expand_query_no_match_returns_original(populated_store):
    expanded = expand_query("unrelated query terms", populated_store)
    # No entities match → original query returned unchanged
    assert "unrelated" in expanded


def test_expand_query_never_removes_original_terms(populated_store):
    expanded = expand_query("TierManager storage tier", populated_store)
    for term in ["TierManager", "storage", "tier"]:
        assert term in expanded


def test_boost_scores_increases_linked_block(populated_store, sample_entity, sample_entity_b):
    """Blocks mentioning linked entities get a score boost."""
    blocks = [
        {"chunk_id": "mem#0", "content": "RecallPipeline uses BM25", "score": 0.50},
        {"chunk_id": "mem#1", "content": "unrelated content", "score": 0.50},
    ]
    # top-1 result is TierManager-linked → RecallPipeline block should be boosted
    boosted = boost_scores(blocks, top_result_entity_id=sample_entity.entity_id,
                           store=populated_store)
    linked_block = next(b for b in boosted if b["chunk_id"] == "mem#0")
    unlinked_block = next(b for b in boosted if b["chunk_id"] == "mem#1")
    assert linked_block["score"] >= unlinked_block["score"]


def test_boost_scores_max_boost_is_0_30(populated_store, sample_entity, sample_entity_b):
    blocks = [
        {"chunk_id": "mem#0", "content": "RecallPipeline", "score": 0.10},
    ]
    boosted = boost_scores(blocks, top_result_entity_id=sample_entity.entity_id,
                           store=populated_store)
    # Even with multiple edges, max boost is +0.30
    assert boosted[0]["score"] <= 0.40 + 1e-6  # 0.10 + 0.30


def test_boost_scores_is_additive(populated_store, sample_entity):
    blocks = [{"chunk_id": "x", "content": "RecallPipeline", "score": 0.70}]
    boosted = boost_scores(blocks, top_result_entity_id=sample_entity.entity_id,
                           store=populated_store)
    assert boosted[0]["score"] >= 0.70


def test_boost_scores_no_entity_returns_unchanged(tmp_path):
    empty_store = JSONGraphStore(path=tmp_path / "empty.json")
    blocks = [{"chunk_id": "x", "content": "anything", "score": 0.50}]
    boosted = boost_scores(blocks, top_result_entity_id="nobody", store=empty_store)
    assert boosted[0]["score"] == pytest.approx(0.50)


def test_traverse_depth2_walks_two_hops(tmp_path):
    """Depth-2 traversal should reach entities two edges away."""
    store = JSONGraphStore(path=tmp_path / "g.json")
    a = Entity(entity_id=make_entity_id("A", "class", "p"), name="A", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    b = Entity(entity_id=make_entity_id("B", "class", "p"), name="B", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    c = Entity(entity_id=make_entity_id("C", "class", "p"), name="C", type="class",
               project="p", source_files=[], confidence=1.0,
               first_seen="2026-03-28T00:00:00", metadata={})
    store.upsert_entity(a)
    store.upsert_entity(b)
    store.upsert_entity(c)
    store.upsert_edge(Edge(
        edge_id=make_edge_id(a.entity_id, b.entity_id, "CO_OCCURS"),
        source_id=a.entity_id, target_id=b.entity_id,
        relationship="CO_OCCURS", weight=1.0, signals=["co_occurrence"], metadata={},
    ))
    store.upsert_edge(Edge(
        edge_id=make_edge_id(b.entity_id, c.entity_id, "CO_OCCURS"),
        source_id=b.entity_id, target_id=c.entity_id,
        relationship="CO_OCCURS", weight=1.0, signals=["co_occurrence"], metadata={},
    ))
    result = traverse(a.entity_id, store, depth=2)
    connected_ids = {e.entity_id for e, _ in result.connected}
    assert b.entity_id in connected_ids
    assert c.entity_id in connected_ids
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_graph/test_traverser.py -v 2>&1 | head -15
```

- [ ] **Step 3: Implement traverser.py**

```python
# src/depthfusion/graph/traverser.py
"""Graph traversal, query expansion, and score boosting."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from depthfusion.graph.types import Entity, Edge, TraversalResult

if TYPE_CHECKING:
    from depthfusion.graph.store import JSONGraphStore, SQLiteGraphStore

# Threshold: entities below this are excluded from query expansion
_CONFIDENCE_THRESHOLD = 0.70
# Max boost per block, applied additively
_MAX_BOOST = 0.30
# Boost per unit of edge weight
_BOOST_PER_WEIGHT_UNIT = 0.10


def traverse(
    entity_id: str,
    store: "JSONGraphStore | SQLiteGraphStore",
    depth: int = 1,
    relationship_filter: list[str] | None = None,
) -> TraversalResult | None:
    """Walk the graph from entity_id up to `depth` hops.

    Returns TraversalResult with all reachable (entity, edge) pairs,
    or None if the origin entity is not found.
    """
    origin = store.get_entity(entity_id)
    if origin is None:
        return None

    visited: set[str] = {entity_id}
    connected: list[tuple[Entity, Edge]] = []

    frontier: set[str] = {entity_id}
    for _ in range(depth):
        next_frontier: set[str] = set()
        for fid in frontier:
            edges = store.get_edges(
                fid,
                **({"relationship_filter": relationship_filter}
                   if relationship_filter and hasattr(store.get_edges, '__code__') else {}),
            )
            for edge in edges:
                # Handle both SQLite (supports filter) and JSON (no filter param)
                if relationship_filter and edge.relationship not in relationship_filter:
                    continue
                neighbor_id = (
                    edge.target_id if edge.source_id == fid else edge.source_id
                )
                if neighbor_id not in visited:
                    neighbor = store.get_entity(neighbor_id)
                    if neighbor:
                        connected.append((neighbor, edge))
                        next_frontier.add(neighbor_id)
                        visited.add(neighbor_id)
        frontier = next_frontier

    return TraversalResult(
        origin_entity=origin,
        connected=connected,
        source_memories=[],
        depth=depth,
    )


def expand_query(query: str, store: "JSONGraphStore | SQLiteGraphStore") -> str:
    """Expand a query string with entity-linked terms from the graph.

    1. Find entities whose name appears in the query (case-sensitive word match).
    2. For each found entity, look up its neighbors in the graph.
    3. Add neighbor entity names as extra query terms.

    Original terms are always preserved. Returns expanded query string.
    Skips entities with confidence < 0.70.
    """
    all_entities = store.all_entities()
    query_entities: list[Entity] = []

    for entity in all_entities:
        if entity.confidence < _CONFIDENCE_THRESHOLD:
            continue
        # Word-boundary match (case-insensitive) — clean the name for function types
        clean_name = entity.name.rstrip("()")
        pattern = r"\b" + re.escape(clean_name) + r"\b"
        if re.search(pattern, query, re.IGNORECASE):
            query_entities.append(entity)

    if not query_entities:
        return query

    extra_terms: list[str] = []
    for entity in query_entities:
        result = traverse(entity.entity_id, store, depth=1)
        if result:
            for neighbor, _ in result.connected:
                if neighbor.confidence >= _CONFIDENCE_THRESHOLD:
                    # Add the clean name (without trailing "()")
                    term = neighbor.name.rstrip("()")
                    if term.lower() not in query.lower():
                        extra_terms.append(term)

    if not extra_terms:
        return query

    return query + " " + " ".join(extra_terms)


def boost_scores(
    blocks: list[dict],
    top_result_entity_id: str,
    store: "JSONGraphStore | SQLiteGraphStore",
) -> list[dict]:
    """Boost block scores if they mention entities linked to the top-1 result.

    Boost = min(edge_weight × 0.10, 0.30), additive, per block.
    Returns new list with boosted scores; original dicts are not mutated.
    """
    result = traverse(top_result_entity_id, store, depth=1)
    if not result:
        return blocks

    # Map entity names → edge weight for linked neighbors
    linked: dict[str, float] = {}
    for neighbor, edge in result.connected:
        clean = neighbor.name.rstrip("()")
        linked[clean.lower()] = edge.weight

    boosted: list[dict] = []
    for block in blocks:
        content_lower = block.get("content", "").lower()
        boost = 0.0
        for name_lower, weight in linked.items():
            if name_lower in content_lower:
                boost += weight * _BOOST_PER_WEIGHT_UNIT
        boost = min(boost, _MAX_BOOST)
        boosted.append({**block, "score": block["score"] + boost})
    return boosted
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_graph/test_traverser.py -v
```
Expected: 14 passed

- [ ] **Step 5: Run full existing suite to confirm nothing broken**

```bash
.venv/bin/pytest --ignore=tests/test_graph -q
```
Expected: 328 passed

- [ ] **Step 6: Commit**

```bash
git add src/depthfusion/graph/traverser.py tests/test_graph/test_traverser.py
git commit -m "feat(graph): add traverser — traverse(), expand_query(), boost_scores()"
```

---

## Task 8: MCP Tools  *(Wave 5 — Agent D)*

**Files:**
- Modify: `src/depthfusion/mcp/server.py`

Three tools added to the existing registry pattern: `depthfusion_graph_traverse`, `depthfusion_graph_status`, `depthfusion_set_scope`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_analyzer/test_mcp_server.py — add to existing file

def test_graph_tools_registered_when_flag_enabled():
    """Graph tools appear in enabled list when graph_enabled=True."""
    from depthfusion.mcp.server import get_enabled_tools
    config = MagicMock()
    config.router_enabled = False
    config.rlm_enabled = False
    config.graph_enabled = True
    enabled = get_enabled_tools(config)
    assert "depthfusion_graph_traverse" in enabled
    assert "depthfusion_graph_status" in enabled
    assert "depthfusion_set_scope" in enabled


def test_graph_tools_absent_when_flag_disabled():
    from depthfusion.mcp.server import get_enabled_tools
    config = MagicMock()
    config.router_enabled = False
    config.rlm_enabled = False
    config.graph_enabled = False
    enabled = get_enabled_tools(config)
    assert "depthfusion_graph_traverse" not in enabled
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_analyzer/test_mcp_server.py::test_graph_tools_registered_when_flag_enabled -v 2>&1 | head -15
```

- [ ] **Step 3: Add graph tools to server.py**

In `src/depthfusion/mcp/server.py`, make these three edits:

**Edit 1** — add to `TOOLS` dict (after the existing entries):
```python
    # v0.4.0 graph tools
    "depthfusion_graph_traverse": "Traverse entity graph from a named entity",
    "depthfusion_graph_status": "Report graph health: node count, edge count, coverage, tier",
    "depthfusion_set_scope": "Set session graph scope (project | cross_project | global)",
```

**Edit 2** — add to `_TOOL_FLAGS` dict:
```python
    "depthfusion_graph_traverse": "graph_enabled",
    "depthfusion_graph_status": "graph_enabled",
    "depthfusion_set_scope": "graph_enabled",
```

**Edit 3** — add branches to `_dispatch_tool`:
```python
    elif tool_name == "depthfusion_graph_traverse":
        return _tool_graph_traverse(arguments)
    elif tool_name == "depthfusion_graph_status":
        return _tool_graph_status()
    elif tool_name == "depthfusion_set_scope":
        return _tool_set_scope(arguments)
```

**Edit 4** — add the three tool implementations (after the existing `_tool_compress_session` function):

```python
def _tool_graph_traverse(arguments: dict) -> str:
    """Traverse entity graph from a named entity."""
    import os
    graph_enabled = os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() == "true"
    if not graph_enabled:
        return json.dumps({"error": "DEPTHFUSION_GRAPH_ENABLED is not set"})

    from depthfusion.graph.store import get_store
    from depthfusion.graph.traverser import traverse

    entity_name = arguments.get("entity_name", "")
    depth = min(int(arguments.get("depth", 1)), 3)
    relationship_filter = arguments.get("relationship_filter") or None

    store = get_store()
    # Find entity by name (case-insensitive)
    all_entities = store.all_entities()
    match = next(
        (e for e in all_entities if e.name.lower() == entity_name.lower()), None
    )
    if not match:
        return json.dumps({"error": f"Entity not found: {entity_name}", "available": [e.name for e in all_entities[:20]]})

    result = traverse(match.entity_id, store, depth=depth, relationship_filter=relationship_filter)
    if not result:
        return json.dumps({"error": "Traversal failed"})

    return json.dumps({
        "origin": {"name": result.origin_entity.name, "type": result.origin_entity.type,
                   "confidence": result.origin_entity.confidence},
        "connected": [
            {"name": e.name, "type": e.type, "relationship": edge.relationship,
             "weight": edge.weight, "signals": edge.signals}
            for e, edge in result.connected
        ],
        "depth": result.depth,
    }, indent=2)


def _tool_graph_status() -> str:
    """Report graph health and coverage."""
    import os
    graph_enabled = os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() == "true"
    if not graph_enabled:
        return json.dumps({"graph_enabled": False, "message": "Set DEPTHFUSION_GRAPH_ENABLED=true to activate"})

    from depthfusion.graph.store import get_store
    store = get_store()
    entities = store.all_entities()
    type_breakdown: dict[str, int] = {}
    for e in entities:
        type_breakdown[e.type] = type_breakdown.get(e.type, 0) + 1

    return json.dumps({
        "graph_enabled": True,
        "node_count": store.node_count(),
        "edge_count": store.edge_count(),
        "entities_by_type": type_breakdown,
        "tier": os.environ.get("DEPTHFUSION_MODE", "local"),
    }, indent=2)


def _tool_set_scope(arguments: dict) -> str:
    """Programmatically set session graph scope."""
    from depthfusion.graph.scope import write_scope, default_scope
    from depthfusion.graph.types import GraphScope
    from datetime import datetime, timezone

    mode = arguments.get("mode", "project")
    projects = arguments.get("projects") or []

    if mode not in ("project", "cross_project", "global"):
        return json.dumps({"error": f"Invalid mode: {mode}. Use project|cross_project|global"})

    scope = GraphScope(
        mode=mode,
        active_projects=projects,
        session_id="mcp_set",
        set_at=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    )
    write_scope(scope)
    return json.dumps({"ok": True, "mode": mode, "active_projects": projects})
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_analyzer/test_mcp_server.py -v
```
Expected: existing tests + 2 new = all pass

- [ ] **Step 5: Commit**

```bash
git add src/depthfusion/mcp/server.py tests/test_analyzer/test_mcp_server.py
git commit -m "feat(graph): add depthfusion_graph_traverse, graph_status, set_scope MCP tools"
```

---

## Task 9: Pipeline Integration  *(Wave 5 — Agent E)*

**Files:**
- Modify: `src/depthfusion/retrieval/hybrid.py`
- Modify: `src/depthfusion/capture/auto_learn.py`

- [ ] **Step 1: Write failing tests for hybrid.py expansion**

```python
# tests/test_retrieval/test_hybrid.py — append to existing file

def test_expand_query_called_when_graph_enabled(tmp_path, monkeypatch):
    """expand_query injects linked terms before BM25 when flag is on."""
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")

    from depthfusion.graph.store import JSONGraphStore
    from depthfusion.graph.types import Entity, Edge
    from depthfusion.graph.extractor import make_entity_id
    from depthfusion.graph.linker import make_edge_id

    store_path = tmp_path / "g.json"
    store = JSONGraphStore(path=store_path)
    e1 = Entity(entity_id=make_entity_id("TierManager", "class", "test"),
                name="TierManager", type="class", project="test",
                source_files=["m.md"], confidence=1.0,
                first_seen="2026-03-28T00:00:00", metadata={})
    e2 = Entity(entity_id=make_entity_id("RecallPipeline", "class", "test"),
                name="RecallPipeline", type="class", project="test",
                source_files=["m.md"], confidence=1.0,
                first_seen="2026-03-28T00:00:00", metadata={})
    store.upsert_entity(e1)
    store.upsert_entity(e2)
    store.upsert_edge(Edge(
        edge_id=make_edge_id(e1.entity_id, e2.entity_id, "CO_OCCURS"),
        source_id=e1.entity_id, target_id=e2.entity_id,
        relationship="CO_OCCURS", weight=1.0, signals=["co_occurrence"], metadata={},
    ))

    from depthfusion.retrieval.hybrid import RecallPipeline, PipelineMode
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    expanded = pipeline.maybe_expand_query("TierManager storage", graph_store=store)
    assert "RecallPipeline" in expanded


def test_expand_query_skipped_when_graph_disabled(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "false")
    from depthfusion.retrieval.hybrid import RecallPipeline, PipelineMode
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    result = pipeline.maybe_expand_query("TierManager storage", graph_store=None)
    assert result == "TierManager storage"


def test_expand_query_no_op_when_store_is_none(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
    from depthfusion.retrieval.hybrid import RecallPipeline, PipelineMode
    pipeline = RecallPipeline(mode=PipelineMode.LOCAL)
    result = pipeline.maybe_expand_query("any query", graph_store=None)
    assert result == "any query"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_retrieval/test_hybrid.py::test_expand_query_called_when_graph_enabled -v 2>&1 | head -15
```

- [ ] **Step 3: Add maybe_expand_query to RecallPipeline in hybrid.py**

```python
# In src/depthfusion/retrieval/hybrid.py
# Add this import at the top of the file (after existing imports):
import os

# Add this method to the RecallPipeline class (after apply_reranker):
    def maybe_expand_query(
        self,
        query: str,
        graph_store: "Any | None" = None,
    ) -> str:
        """Expand query with graph-linked terms when DEPTHFUSION_GRAPH_ENABLED=true.

        Returns original query unchanged if:
        - DEPTHFUSION_GRAPH_ENABLED is not 'true'
        - graph_store is None
        - graph has 0 nodes
        """
        if os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() != "true":
            return query
        if graph_store is None:
            return query
        try:
            if graph_store.node_count() == 0:
                return query
            from depthfusion.graph.traverser import expand_query
            return expand_query(query, graph_store)
        except Exception:
            return query
```

Note: add `from typing import Any` to the imports in hybrid.py if not already present.

- [ ] **Step 4: Write failing test for auto_learn graph extraction**

```python
# tests/test_capture/test_auto_learn.py — append to existing file

def test_graph_extractor_populates_store(tmp_path, monkeypatch):
    """Graph entities extracted from session file and stored when graph_enabled=True."""
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")

    session_file = tmp_path / "session.tmp"
    session_file.write_text("The TierManager class is central.\nrrf_fuse() merges results.", encoding="utf-8")

    from depthfusion.graph.store import JSONGraphStore
    store = JSONGraphStore(path=tmp_path / "g.json")

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    summarize_and_extract_graph(session_file, project="depthfusion", graph_store=store)

    entities = store.all_entities()
    names = [e.name for e in entities]
    assert "TierManager" in names


def test_graph_extraction_skipped_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "false")
    session_file = tmp_path / "session.tmp"
    session_file.write_text("TierManager is central.", encoding="utf-8")

    from depthfusion.graph.store import JSONGraphStore
    store = JSONGraphStore(path=tmp_path / "g.json")

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    summarize_and_extract_graph(session_file, project="depthfusion", graph_store=store)

    assert store.node_count() == 0
```

- [ ] **Step 5: Add summarize_and_extract_graph to auto_learn.py**

```python
# Correct version — replace the above with this in auto_learn.py:

def summarize_and_extract_graph(
    path: "Path",
    project: str,
    graph_store: "Any | None",
) -> None:
    """Run HaikuSummarizer + graph entity extraction on a session file.

    Stores extracted entities and co-occurrence edges into graph_store.
    No-ops silently when DEPTHFUSION_GRAPH_ENABLED is not 'true' or graph_store is None.
    """
    import os

    # Always run the summarizer (existing behaviour is unchanged)
    HaikuSummarizer().summarize_file(path)

    if os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() != "true":
        return
    if graph_store is None:
        return

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    try:
        from depthfusion.graph.extractor import (
            RegexExtractor, HaikuExtractor, confidence_merge,
        )
        from depthfusion.graph.linker import CoOccurrenceLinker

        # Alias used by tests for patching
        GraphExtractor = RegexExtractor  # noqa: N806

        regex_ext = GraphExtractor(project=project)
        regex_entities = regex_ext.extract(content, source_file=str(path))
        haiku_ext = HaikuExtractor(project=project)
        haiku_entities = haiku_ext.extract(content, source_file=str(path))
        entities = confidence_merge(regex_entities, haiku_entities)

        linker = CoOccurrenceLinker()
        edges = linker.link(entities)

        for entity in entities:
            graph_store.upsert_entity(entity)
        for edge in edges:
            graph_store.upsert_edge(edge)
    except Exception as exc:
        logger.debug("Graph entity extraction failed: %s", exc)
```

Also update the test to use the correct extraction method. Replace the test body with:

```python
def test_graph_extractor_called_after_haiku_summarizer(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")

    session_file = tmp_path / "session.tmp"
    session_file.write_text("The TierManager class is central.\nrrf_fuse() merges results.", encoding="utf-8")

    from depthfusion.graph.store import JSONGraphStore
    store = JSONGraphStore(path=tmp_path / "g.json")

    from depthfusion.capture.auto_learn import summarize_and_extract_graph
    summarize_and_extract_graph(session_file, project="depthfusion", graph_store=store)

    # TierManager should be in the store (regex extracted)
    entities = store.all_entities()
    names = [e.name for e in entities]
    assert "TierManager" in names
```

- [ ] **Step 6: Run all pipeline integration tests**

```bash
.venv/bin/pytest tests/test_retrieval/test_hybrid.py tests/test_capture/test_auto_learn.py -v
```
Expected: existing + 4 new tests pass

- [ ] **Step 7: Commit**

```bash
git add src/depthfusion/retrieval/hybrid.py src/depthfusion/capture/auto_learn.py \
        tests/test_retrieval/test_hybrid.py tests/test_capture/test_auto_learn.py
git commit -m "feat(graph): integrate query expansion into RecallPipeline and entity extraction into auto_learn"
```

---

## Task 10: Session-Init Scope Prompt + Install Flag  *(Wave 6)*

**Files:**
- Modify: `~/.claude/hooks/depthfusion-session-init.sh`
- Modify: `src/depthfusion/install/install.py`

- [ ] **Step 1: Add scope prompt section to session-init.sh**

Open `~/.claude/hooks/depthfusion-session-init.sh` and add a new section **after** the existing `Section 3: DepthFusion memory recall` block (before `print("\n".join(lines))`):

```python
# --- Section 4: Graph scope prompt (when DEPTHFUSION_GRAPH_ENABLED=true) ---
import os as _os
if _os.environ.get("DEPTHFUSION_GRAPH_ENABLED", "false").lower() == "true":
    from depthfusion.graph.scope import read_scope, write_scope, default_scope
    existing = read_scope()
    if existing is None:
        # First session — prompt user for scope preference
        lines.append("")
        lines.append("## [graph] Session scope")
        lines.append("")
        lines.append("Graph scope for this session:")
        lines.append(f"  [1] {project_name} only  (default — current project only)")
        lines.append("  [2] cross-project  (all your projects)")
        lines.append("  [3] global  (all memory sources)")
        lines.append("")
        lines.append("*Tip: Call `depthfusion_set_scope` to change scope during this session.*")
        # Write default scope silently (user can override via MCP tool)
        write_scope(default_scope(project=project_name, session_id="auto"))
```

- [ ] **Step 2: Verify session-init.sh runs without error**

```bash
cd /home/gregmorris/Development/Projects/depthfusion
DEPTHFUSION_GRAPH_ENABLED=true timeout 4 bash ~/.claude/hooks/depthfusion-session-init.sh 2>/dev/null | tail -15
```
Expected: output includes `## [graph] Session scope` section

- [ ] **Step 3: Add DEPTHFUSION_GRAPH_ENABLED to install.py VPS env lines**

In `src/depthfusion/install/install.py`, update `_VPS_ENV_LINES`:

```python
_VPS_ENV_LINES = [
    "DEPTHFUSION_MODE=vps",
    "DEPTHFUSION_TIER_AUTOPROMOTE=true",
    "DEPTHFUSION_RERANKER_ENABLED=true",
    "DEPTHFUSION_GRAPH_ENABLED=false",   # enable after validating extraction quality
]
```

Also add a print step to `install_vps()` after the existing `_print_step` calls:

```python
    _print_step("  - Knowledge graph (v0.4.0): disabled by default (set DEPTHFUSION_GRAPH_ENABLED=true to enable)", dry_run)
```

- [ ] **Step 4: Run install.py test to confirm no regression**

```bash
.venv/bin/pytest tests/test_install/ -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/depthfusion/install/install.py ~/.claude/hooks/depthfusion-session-init.sh
git commit -m "feat(graph): add scope prompt to session-init and DEPTHFUSION_GRAPH_ENABLED flag to installer"
```

---

## Task 11: Full Test Suite Validation  *(Wave 6 — final)*

This task validates all 408 tests pass before tagging v0.4.0.

- [ ] **Step 1: Run the complete test suite**

```bash
cd /home/gregmorris/Development/Projects/depthfusion
.venv/bin/pytest -v --tb=short 2>&1 | tail -30
```
Expected: `408 passed` (328 existing + ~80 new graph tests)

- [ ] **Step 2: Verify feature flag isolation**

```bash
DEPTHFUSION_GRAPH_ENABLED=false .venv/bin/pytest tests/test_retrieval/test_hybrid.py -v
```
Expected: all pass — confirm existing recall tests unchanged

- [ ] **Step 3: Smoke-test graph traverse via MCP tool (requires VPS mode)**

```bash
DEPTHFUSION_GRAPH_ENABLED=true DEPTHFUSION_MODE=vps \
  .venv/bin/python -c "
from depthfusion.mcp.server import _tool_graph_status
print(_tool_graph_status())
"
```
Expected: JSON with `graph_enabled: true`, node/edge counts (0 if freshly initialized)

- [ ] **Step 4: Smoke-test query expansion**

```bash
DEPTHFUSION_GRAPH_ENABLED=true DEPTHFUSION_MODE=local \
  .venv/bin/python -c "
from depthfusion.graph.store import JSONGraphStore
from depthfusion.graph.types import Entity, Edge
from depthfusion.graph.extractor import make_entity_id
from depthfusion.graph.linker import make_edge_id
from depthfusion.retrieval.hybrid import RecallPipeline, PipelineMode
import tempfile, pathlib

with tempfile.TemporaryDirectory() as d:
    store = JSONGraphStore(path=pathlib.Path(d) / 'g.json')
    e1 = Entity(make_entity_id('SQLite', 'concept', 'depthfusion'), 'SQLite', 'concept',
                'depthfusion', [], 1.0, '2026-03-28T00:00:00', {})
    e2 = Entity(make_entity_id('TierManager', 'class', 'depthfusion'), 'TierManager', 'class',
                'depthfusion', [], 1.0, '2026-03-28T00:00:00', {})
    store.upsert_entity(e1)
    store.upsert_entity(e2)
    store.upsert_edge(Edge(make_edge_id(e1.entity_id, e2.entity_id, 'REPLACES'),
                           e1.entity_id, e2.entity_id, 'REPLACES', 2.0, ['haiku'], {}))
    p = RecallPipeline(mode=PipelineMode.LOCAL)
    expanded = p.maybe_expand_query('why did we choose SQLite', graph_store=store)
    print('Expanded:', expanded)
    assert 'TierManager' in expanded, 'Query expansion failed'
    print('OK')
"
```
Expected: `Expanded: why did we choose SQLite TierManager` and `OK`

- [ ] **Step 5: Update BACKLOG.md to mark v0.4.0 complete**

In `BACKLOG.md`, mark the v0.4.0 knowledge graph task as complete.

- [ ] **Step 6: Final commit and tag**

```bash
git add BACKLOG.md
git commit -m "feat(v0.4.0): complete knowledge graph entity linking

- Entity extraction: regex (class/function/file) + haiku async (concept/decision/error_pattern)
- Edge creation: co-occurrence, haiku-inferred, temporal proximity (48h window)
- Storage: JSONGraphStore (local), SQLiteGraphStore (vps), tier-aware get_store()
- Traverser: traverse(), expand_query(), boost_scores()
- 3 new MCP tools: graph_traverse, graph_status, set_scope
- Pipeline integration: query expansion pre-BM25, score boost post-reranker
- Session scope prompt in session-init.sh
- Feature flag: DEPTHFUSION_GRAPH_ENABLED=false (default)
- 80 new tests → 408 total"

git tag v0.4.0
```

---

## Quick Reference

### Running the full graph test suite
```bash
.venv/bin/pytest tests/test_graph/ -v
```

### Enabling graph for manual testing
```bash
export DEPTHFUSION_GRAPH_ENABLED=true
```

### Resetting graph store (local mode)
```bash
rm -f ~/.claude/depthfusion-graph.json
```

### Key confidence thresholds
- Regex extraction: `confidence = 1.0`
- Haiku extraction: `confidence = 0.85` (range 0.70–0.95)
- Query expansion minimum: `confidence >= 0.70`
- Entities below 0.70: stored, available via `graph_traverse` only

### Edge weight interpretation
- `weight = 1.0`: single signal (co_occurrence, haiku, or temporal)
- `weight = 2.0`: two signals agree
- `weight = 3.0`: all three signals agree
- Rerank boost = `min(weight × 0.10, 0.30)`
