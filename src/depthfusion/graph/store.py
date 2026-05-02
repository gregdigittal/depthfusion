"""Graph storage backends: JSON (local), SQLite (vps-tier1), ChromaDB (Tier 2)."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from depthfusion.graph.types import Edge, Entity

_DEFAULT_JSON_PATH = Path.home() / ".claude" / "depthfusion-graph.json"

_DEFAULT_MIN_CONFIDENCE = 0.7


def _min_confidence() -> float:
    """Return the minimum confidence threshold for graph writes.

    Reads from DEPTHFUSION_GRAPH_MIN_CONFIDENCE env var; falls back to
    _DEFAULT_MIN_CONFIDENCE (0.7) if unset or invalid.
    """
    val = os.environ.get("DEPTHFUSION_GRAPH_MIN_CONFIDENCE", "")
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    return _DEFAULT_MIN_CONFIDENCE


@runtime_checkable
class GraphBackend(Protocol):
    def upsert_entity(self, entity: Entity) -> None:
        """Store or update an entity.

        Entities whose confidence is below the configured minimum threshold
        (default 0.7, overridden via DEPTHFUSION_GRAPH_MIN_CONFIDENCE) are
        silently skipped and not written to the store.
        """
        ...

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
        if entity.confidence < _min_confidence():
            return  # silently skip low-confidence entities
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
            if (d["source_id"] == entity_id or d["target_id"] == entity_id)
            and (relationship_filter is None or d["relationship"] in relationship_filter)
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
        if entity.confidence < _min_confidence():
            return  # silently skip low-confidence entities
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


_DEFAULT_CHROMA_DB_PATH = Path.home() / ".claude" / "depthfusion-graph-chroma"
_DEFAULT_CHROMA_EDGE_DB_PATH = Path.home() / ".claude" / "depthfusion-graph-chroma-edges.db"


def _chroma_available() -> bool:
    return importlib.util.find_spec("chromadb") is not None


class ChromaGraphStore:
    """ChromaDB-backed entity store with SQLite sidecar for edges.

    Entities are stored in a ChromaDB collection, enabling semantic entity
    search over names and types. Edges are stored in a SQLite sidecar (edges
    are relational, not vector-searchable).

    Requires the `chromadb` package (installed as part of the [vps-gpu] extra).
    When chromadb is unavailable, get_store() falls back to SQLiteGraphStore.

    S-39: AC-1 (ChromaDB entity collection), AC-2 (factory selects when Tier 2 active).
    """

    def __init__(
        self,
        chroma_path: Path | None = None,
        edge_db_path: Path | None = None,
    ):
        import chromadb  # noqa: PLC0415 — lazy import; package is optional

        self._chroma_path = chroma_path or _DEFAULT_CHROMA_DB_PATH
        self._chroma_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._chroma_path))
        self._collection = self._client.get_or_create_collection(
            name="entities",
            metadata={"hnsw:space": "cosine"},
        )

        # SQLite sidecar for edges — same schema as SQLiteGraphStore.edges
        edge_path = edge_db_path or _DEFAULT_CHROMA_EDGE_DB_PATH
        self._edge_conn = sqlite3.connect(str(edge_path), check_same_thread=False)
        self._edge_conn.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                edge_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relationship TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                signals TEXT NOT NULL DEFAULT '[]',
                metadata TEXT NOT NULL DEFAULT '{}'
            )
        """)
        self._edge_conn.commit()

    # ------------------------------------------------------------------
    # Entities (ChromaDB)
    # ------------------------------------------------------------------

    def upsert_entity(self, entity: Entity) -> None:
        if entity.confidence < _min_confidence():
            return
        meta: dict[str, str | int | float | bool] = {
            "name": entity.name,
            "type": entity.type,
            "project": entity.project,
            "source_files": json.dumps(entity.source_files),
            "confidence": entity.confidence,
            "first_seen": entity.first_seen,
            "extra_metadata": json.dumps(entity.metadata),
        }
        # Use entity name as document text for semantic search
        self._collection.upsert(
            ids=[entity.entity_id],
            documents=[entity.name],
            metadatas=[meta],
        )

    def get_entity(self, entity_id: str) -> Entity | None:
        result = self._collection.get(ids=[entity_id], include=["metadatas", "documents"])
        if not result["ids"]:
            return None
        m = result["metadatas"][0]  # type: ignore[index]
        return Entity(
            entity_id=entity_id,
            name=cast(str, m["name"]),
            type=cast(str, m["type"]),
            project=cast(str, m["project"]),
            source_files=json.loads(cast(str, m["source_files"])),
            confidence=float(cast(str, m["confidence"])),
            first_seen=cast(str, m["first_seen"]),
            metadata=json.loads(cast(str, m.get("extra_metadata", "{}"))),
        )

    def all_entities(self) -> list[Entity]:
        result = self._collection.get(include=["metadatas"])
        metadatas = cast(list[dict[str, str]], result["metadatas"] or [])
        entities = []
        for eid, m in zip(result["ids"], metadatas):
            entities.append(Entity(
                entity_id=cast(str, eid),
                name=m["name"],
                type=m["type"],
                project=m["project"],
                source_files=json.loads(m["source_files"]),
                confidence=float(m["confidence"]),
                first_seen=m["first_seen"],
                metadata=json.loads(m.get("extra_metadata", "{}")),
            ))
        return entities

    def node_count(self) -> int:
        return self._collection.count()

    # ------------------------------------------------------------------
    # Edges (SQLite sidecar)
    # ------------------------------------------------------------------

    def upsert_edge(self, edge: Edge) -> None:
        self._edge_conn.execute(
            """
            INSERT OR REPLACE INTO edges
                (edge_id, source_id, target_id, relationship, weight, signals, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.edge_id, edge.source_id, edge.target_id,
                edge.relationship, edge.weight,
                json.dumps(edge.signals), json.dumps(edge.metadata),
            ),
        )
        self._edge_conn.commit()

    def get_edges(
        self, entity_id: str, relationship_filter: list[str] | None = None
    ) -> list[Edge]:
        if relationship_filter:
            placeholders = ",".join("?" * len(relationship_filter))
            rows = self._edge_conn.execute(
                f"""
                SELECT edge_id, source_id, target_id, relationship, weight, signals, metadata
                FROM edges
                WHERE (source_id = ? OR target_id = ?)
                  AND relationship IN ({placeholders})
                """,
                [entity_id, entity_id, *relationship_filter],
            ).fetchall()
        else:
            rows = self._edge_conn.execute(
                """
                SELECT edge_id, source_id, target_id, relationship, weight, signals, metadata
                FROM edges WHERE source_id = ? OR target_id = ?
                """,
                (entity_id, entity_id),
            ).fetchall()
        return [
            Edge(
                edge_id=r[0], source_id=r[1], target_id=r[2],
                relationship=r[3], weight=r[4],
                signals=json.loads(r[5]), metadata=json.loads(r[6]),
            )
            for r in rows
        ]

    def edge_count(self) -> int:
        return self._edge_conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]


def get_store(
    graph_json_path: Path | None = None,
    graph_db_path: Path | None = None,
    corpus_size: int = 0,
    chroma_path: Path | None = None,
    chroma_edge_db_path: Path | None = None,
) -> "JSONGraphStore | SQLiteGraphStore | ChromaGraphStore":
    """Return the appropriate store backend based on DEPTHFUSION_MODE and Tier 2 status.

    local → JSONGraphStore
    vps-cpu / vps (alias) → SQLiteGraphStore
    vps-gpu (Tier 2 active) + chromadb available → ChromaGraphStore
    vps-gpu + chromadb unavailable → SQLiteGraphStore (fallback)
    """
    mode = os.environ.get("DEPTHFUSION_MODE", "local").strip().lower()
    if mode == "vps":
        mode = "vps-cpu"  # legacy alias
    if mode == "vps-gpu" and _chroma_available():
        return ChromaGraphStore(chroma_path=chroma_path, edge_db_path=chroma_edge_db_path)
    if mode in ("vps-cpu", "vps-gpu"):
        return SQLiteGraphStore(path=graph_db_path)
    return JSONGraphStore(path=graph_json_path)
