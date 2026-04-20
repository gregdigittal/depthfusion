"""Graph data model: Entity, Edge, GraphScope, TraversalResult."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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
    """A directed relationship between two entities.

    Relationship values (one of):
      * CO_OCCURS       — two entities mentioned in the same block (structural)
      * CAUSES          — A is a cause of B (semantic; Haiku-inferred)
      * FIXES           — A fixes B (semantic; Haiku-inferred)
      * DEPENDS_ON      — A depends on B (semantic; Haiku-inferred)
      * REPLACES        — A replaces B (semantic; Haiku-inferred)
      * CONFLICTS_WITH  — A conflicts with B (semantic; Haiku-inferred)
      * CO_WORKED_ON    — two ENTITIES appeared across sessions in a time window (TemporalLinker)
      * PRECEDED_BY     — B PRECEDED_BY A: session A came before session B in
                          wall-clock time, and shared vocabulary suggests
                          continuity. Directed, session-level. v0.5 CM-4 / S-50.
    """
    edge_id: str
    source_id: str
    target_id: str
    relationship: str
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
