"""Graph storage backends: JSON (local), SQLite (vps-tier1)."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Protocol, runtime_checkable

from depthfusion.graph.types import Entity, Edge

_DEFAULT_JSON_PATH = Path.home() / ".claude" / "depthfusion-graph.json"


@runtime_checkable
class GraphBackend(Protocol):
    def upsert_entity(self, entity: Entity) -> None: ...
    def get_entity(self, entity_id: str) -> Entity | None: ...
    def upsert_edge(self, edge: Edge) -> None: ...
    def get_edges(
        self, entity_id: str, relationship_filter: list[str] | None = None
    ) -> list[Edge]: ...
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

    def get_edges(
        self,
        entity_id: str,
        relationship_filter: list[str] | None = None,
    ) -> list[Edge]:
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
) -> "JSONGraphStore | SQLiteGraphStore":
    """Return the appropriate store backend based on DEPTHFUSION_MODE and corpus size.

    Local mode → JSONGraphStore
    VPS + corpus < 500 → SQLiteGraphStore
    VPS + corpus >= 500 → SQLiteGraphStore (ChromaDB extension future work)
    """
    mode = os.environ.get("DEPTHFUSION_MODE", "local")
    if mode != "vps":
        return JSONGraphStore(path=graph_json_path)
    return SQLiteGraphStore(path=graph_db_path)
