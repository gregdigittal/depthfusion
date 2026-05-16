from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from depthfusion.core.memory_object import MemoryObject

_DDL = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    pinned INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    confidence_score REAL NOT NULL DEFAULT 0.7,
    data_json TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
"""


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)
        self._conn.commit()

    def upsert(self, memory: MemoryObject) -> None:
        data = memory.to_dict()
        with self._lock:
            self._conn.execute(
                """INSERT INTO memories
                   (id, project_id, type, status, pinned, content, summary,
                    confidence_score, data_json, event_version, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                   type=excluded.type, status=excluded.status, pinned=excluded.pinned,
                   content=excluded.content, summary=excluded.summary,
                   confidence_score=excluded.confidence_score,
                   data_json=excluded.data_json, event_version=excluded.event_version,
                   updated_at=excluded.updated_at""",
                (
                    memory.id,
                    memory.project_id,
                    memory.type.value,
                    memory.status.value,
                    1 if memory.pinned else 0,
                    memory.content,
                    memory.summary,
                    memory.confidence.score,
                    json.dumps(data),
                    memory.event_version,
                    memory.created_at.isoformat(),
                    memory.updated_at.isoformat(),
                ),
            )
            self._conn.commit()

    def get(self, memory_id: str) -> Optional[MemoryObject]:
        cur = self._conn.execute(
            "SELECT data_json FROM memories WHERE id=?", (memory_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return MemoryObject.from_dict(json.loads(row[0]))

    def query(
        self,
        project_id: Optional[str] = None,
        include_archived: bool = False,
        memory_type: Optional[str] = None,
        limit: int = 200,
    ) -> list[MemoryObject]:
        conditions: list[str] = []
        params: list = []
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if not include_archived:
            conditions.append("status != 'archived'")
        if memory_type:
            conditions.append("type = ?")
            params.append(memory_type)
        where = " AND ".join(conditions)
        sql = "SELECT data_json FROM memories"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY updated_at DESC LIMIT {limit}"
        cur = self._conn.execute(sql, params)
        return [MemoryObject.from_dict(json.loads(r[0])) for r in cur.fetchall()]

    def count(self, project_id: Optional[str] = None) -> int:
        if project_id:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE project_id=?", (project_id,)
            )
        else:
            cur = self._conn.execute("SELECT COUNT(*) FROM memories")
        return cur.fetchone()[0]
