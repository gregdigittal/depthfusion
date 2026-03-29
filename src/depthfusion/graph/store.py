"""Graph storage backends: JSON (local), SQLite (vps-tier1)."""
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
