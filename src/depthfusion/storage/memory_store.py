from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from depthfusion.core.memory_object import MemoryObject

if TYPE_CHECKING:
    from depthfusion.identity.models import Principal

logger = logging.getLogger(__name__)

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

_FTS_MIGRATION_ID = "fts5_v1"

# FTS5 standalone virtual table (no content= backing table).
# Standalone tables store their own copy of indexed text — no column-mapping
# constraint against the memories table.  facts_text and concepts_text are
# denormalized from data_json at write time (T-390); triggers keep the index
# in sync.  Storage overhead is negligible for typical memory corpus sizes.
_FTS_TABLE_STMT = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, summary, facts_text, concepts_text,
    tokenize='porter unicode61'
)
"""

# Triggers keep the FTS5 index in sync with the memories table.
# The 'delete' special command removes a row from the inverted index.
_FTS_TRIGGER_INSERT = """
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, summary, facts_text, concepts_text)
    VALUES (new.rowid, new.content, new.summary,
            COALESCE(json_extract(new.data_json, '$.facts_text'), ''),
            COALESCE(json_extract(new.data_json, '$.concepts_text'), ''));
END
"""

_FTS_TRIGGER_UPDATE = """
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    DELETE FROM memories_fts WHERE rowid = old.rowid;
    INSERT INTO memories_fts(rowid, content, summary, facts_text, concepts_text)
    VALUES (new.rowid, new.content, new.summary,
            COALESCE(json_extract(new.data_json, '$.facts_text'), ''),
            COALESCE(json_extract(new.data_json, '$.concepts_text'), ''));
END
"""

_FTS_TRIGGER_DELETE = """
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    DELETE FROM memories_fts WHERE rowid = old.rowid;
END
"""

_FTS_BACKFILL_SQL = """
INSERT INTO memories_fts(rowid, content, summary, facts_text, concepts_text)
SELECT rowid, content, summary,
       COALESCE(json_extract(data_json, '$.facts_text'), ''),
       COALESCE(json_extract(data_json, '$.concepts_text'), '')
FROM memories
"""


def _principal_allowed(acl_allow: object, allowed_ids: "set[str]") -> bool:
    """Return True if any member of allowed_ids appears in acl_allow.

    acl_allow may be a list[str] or None/missing.  Returns False when
    acl_allow is absent or empty (absent == deny).
    """
    if not acl_allow or not isinstance(acl_allow, list):
        return False
    return bool(set(acl_allow) & allowed_ids)


def _validate_acl_fields(acl_allow: list[str]) -> None:
    """Raise ValueError if acl_allow is missing or empty.

    T-562: every write path in every store must call this before persisting.
    acl_allow=None or acl_allow=[] are both rejected — absence equals deny and
    an unprotected record must never be silently written.
    """
    if not acl_allow:
        raise ValueError("acl_allow is required")


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)
        self._conn.commit()
        self._apply_fts_migration()

    def _apply_fts_migration(self) -> None:
        """Create FTS5 virtual table + triggers; backfill existing rows once."""
        try:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS _df_schema_migrations "
                "(migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for stmt in (_FTS_TABLE_STMT, _FTS_TRIGGER_INSERT,
                         _FTS_TRIGGER_UPDATE, _FTS_TRIGGER_DELETE):
                self._conn.execute(stmt)
            cur = self._conn.execute(
                "SELECT 1 FROM _df_schema_migrations WHERE migration_id=?",
                (_FTS_MIGRATION_ID,),
            )
            if not cur.fetchone():
                self._conn.execute(_FTS_BACKFILL_SQL)
                self._conn.execute(
                    "INSERT INTO _df_schema_migrations VALUES(?,?)",
                    (_FTS_MIGRATION_ID, datetime.now(timezone.utc).isoformat()),
                )
            self._conn.commit()
        except Exception as exc:  # noqa: BLE001 — FTS5 unavailable is non-fatal
            logger.debug("FTS5 migration skipped: %s", exc)
            try:
                self._conn.rollback()
            except Exception:
                pass

    def upsert(self, memory: MemoryObject) -> None:
        # T-562: enforce ACL stamp before any write.
        raw_acl = (memory.extra or {}).get("acl_allow")
        # Normalize: only a non-empty list passes; None/empty-list/non-list all fail.
        acl_allow: list[str] = raw_acl if isinstance(raw_acl, list) else []
        _validate_acl_fields(acl_allow)
        data = memory.to_dict()
        # T-390: denormalize facts/concepts into FTS5-searchable text fields.
        # Stored as space-joined strings inside data_json so triggers can
        # extract them via json_extract(data_json, '$.facts_text').
        facts = memory.extra.get("facts") or []
        concepts = memory.extra.get("concepts") or []
        if facts:
            data["facts_text"] = " ".join(str(f) for f in facts if f)
        if concepts:
            data["concepts_text"] = " ".join(str(c) for c in concepts if c)
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

    def search(
        self,
        query: str,
        *,
        principal: "Optional[Any]" = None,
        project_id: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list["MemoryObject"]:
        """Search memories by FTS query, filtered by principal ACL.

        T-571: every retrieval call carries the requesting Principal.
        Only records where principal.principal_id or any of principal.groups
        appears in acl_allow are returned.  When principal is None, no ACL
        filter is applied (internal / system callers only).

        Falls back to the full corpus query when FTS is unavailable.
        """
        # Build candidate pool via FTS then ACL-filter, or full-scan + ACL-filter.
        candidates = self.query(
            project_id=project_id,
            include_archived=include_archived,
            limit=limit * 4,  # over-fetch; ACL filter will trim
        )

        if not query or not query.strip():
            filtered = candidates
        else:
            # Try FTS for pre-filtering, then ACL-filter the results.
            fts_ids = self._fts_search(query, limit=limit * 4)
            if fts_ids:
                id_set = set(fts_ids)
                filtered = [m for m in candidates if m.id in id_set]
            else:
                filtered = candidates

        if principal is not None:
            allowed: set[str] = {principal.principal_id}
            for g in (principal.groups or []):
                allowed.add(g)
            filtered = [
                m for m in filtered
                if _principal_allowed(m.extra.get("acl_allow"), allowed)
            ]

        return filtered[:limit]

    def _fts_search(self, query: str, limit: int = 50) -> list[str]:
        """Return memory IDs ranked by FTS5 relevance for `query`.

        Returns an empty list on any failure (FTS5 unavailable, bad query
        syntax, etc.) so callers fall through to the full-table BM25 scan.
        FTS5 `rank` is negative — ORDER BY rank ASC gives most-relevant first.
        """
        if not query or not query.strip():
            return []
        try:
            cur = self._conn.execute(
                """SELECT m.id
                   FROM memories_fts fts
                   JOIN memories m ON fts.rowid = m.rowid
                   WHERE memories_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit),
            )
            return [row[0] for row in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.debug("_fts_search failed (falling back to full scan): %s", exc)
            return []
