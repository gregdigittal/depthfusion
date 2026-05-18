"""Tests for S-114: SQLite FTS5 index on MemoryStore."""
from __future__ import annotations

import json

from depthfusion.core.memory_object import MemoryObject, MemoryType
from depthfusion.storage.memory_store import MemoryStore


def _make_memory(
    id: str = "m1",
    project: str = "proj",
    content: str = "some content",
    summary: str = "",
    extra: dict | None = None,
) -> MemoryObject:
    return MemoryObject(
        id=id,
        project_id=project,
        type=MemoryType.SEMANTIC,
        content=content,
        summary=summary,
        extra=extra or {},
    )


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class TestFtsMigration:
    def test_fts_table_created_on_init(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        cur = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
        )
        assert cur.fetchone() is not None

    def test_migration_table_created(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        cur = store._conn.execute(
            "SELECT migration_id FROM _df_schema_migrations WHERE migration_id='fts5_v1'"
        )
        assert cur.fetchone() is not None

    def test_migration_idempotent(self, tmp_path):
        """Re-opening the same database does not duplicate rows or raise."""
        db = tmp_path / "mem.db"
        store1 = MemoryStore(db)
        store1.upsert(_make_memory("m1", content="asyncpg postgres"))
        store1._conn.close()

        store2 = MemoryStore(db)
        # Migration should not backfill again — row count in fts stays at 1
        cur = store2._conn.execute("SELECT COUNT(*) FROM memories_fts")
        assert cur.fetchone()[0] == 1

    def test_backfill_indexes_existing_rows(self, tmp_path):
        """Rows present before migration are backfilled into FTS index."""
        import sqlite3
        db_path = tmp_path / "pre.db"
        # Insert a row directly (no FTS table yet)
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                pinned INTEGER NOT NULL DEFAULT 0,
                content TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
                confidence_score REAL NOT NULL DEFAULT 0.7,
                data_json TEXT NOT NULL, event_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
        """)
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("pre1", "proj", "semantic", "active", 0,
             "kubernetes deployment strategy", "",
             0.7, json.dumps({"id": "pre1"}), 0, now, now),
        )
        conn.commit()
        conn.close()

        store = MemoryStore(db_path)
        # FTS should now find the pre-existing row
        ids = store._fts_search("kubernetes")
        assert "pre1" in ids


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

class TestFtsTriggers:
    def test_insert_trigger_indexes_row(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        store.upsert(_make_memory("m1", content="asyncpg over psycopg2"))
        ids = store._fts_search("asyncpg")
        assert "m1" in ids

    def test_update_trigger_reindexes_row(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        m = _make_memory("m1", content="old content here")
        store.upsert(m)
        m.content = "new content kubernetes"
        store.upsert(m)

        assert "m1" not in store._fts_search("old")
        assert "m1" in store._fts_search("kubernetes")

    def test_delete_trigger_removes_from_index(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        store.upsert(_make_memory("m1", content="ephemeral content"))
        assert "m1" in store._fts_search("ephemeral")

        store._conn.execute("DELETE FROM memories WHERE id=?", ("m1",))
        store._conn.commit()
        assert "m1" not in store._fts_search("ephemeral")


# ---------------------------------------------------------------------------
# Phrase queries
# ---------------------------------------------------------------------------

class TestPhraseQuery:
    def test_exact_phrase_matches(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        store.upsert(_make_memory("m1", content="use asyncpg for async database access"))
        store.upsert(_make_memory("m2", content="asyncpg is better than psycopg2"))

        results = store._fts_search('"asyncpg for async"')
        assert "m1" in results
        # m2 doesn't contain the exact phrase "asyncpg for async"
        assert "m2" not in results

    def test_phrase_query_case_insensitive(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        store.upsert(_make_memory("m1", content="Deploy via Kubernetes on production"))
        results = store._fts_search('"deploy via kubernetes"')
        assert "m1" in results


# ---------------------------------------------------------------------------
# Field boost (facts_text / concepts_text)
# ---------------------------------------------------------------------------

class TestFieldWeights:
    def test_facts_text_indexed_from_extra(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        store.upsert(_make_memory(
            "m1",
            content="general content here",
            extra={"facts": ["asyncpg preferred over psycopg2"]},
        ))
        ids = store._fts_search("asyncpg")
        assert "m1" in ids

    def test_concepts_text_indexed_from_extra(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        store.upsert(_make_memory(
            "m1",
            content="some prose content",
            extra={"concepts": ["event-driven architecture", "CQRS pattern"]},
        ))
        ids = store._fts_search("CQRS")
        assert "m1" in ids

    def test_facts_text_stored_in_data_json(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        store.upsert(_make_memory(
            "m1",
            extra={"facts": ["use poetry for dependency management"]},
        ))
        cur = store._conn.execute(
            "SELECT json_extract(data_json, '$.facts_text') FROM memories WHERE id='m1'"
        )
        row = cur.fetchone()
        assert row is not None
        assert "poetry" in (row[0] or "")

    def test_no_facts_text_when_extra_empty(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        store.upsert(_make_memory("m1"))
        cur = store._conn.execute(
            "SELECT json_extract(data_json, '$.facts_text') FROM memories WHERE id='m1'"
        )
        row = cur.fetchone()
        # facts_text key should be absent (json_extract returns None)
        assert row[0] is None


# ---------------------------------------------------------------------------
# Feature flag fallback
# ---------------------------------------------------------------------------

class TestFeatureFlagFallback:
    def test_fts_search_returns_empty_on_bad_query(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        store.upsert(_make_memory("m1", content="content"))
        # A malformed FTS5 query should return [] not raise
        result = store._fts_search("AND OR")
        assert isinstance(result, list)

    def test_fts_search_empty_query_returns_empty(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        store.upsert(_make_memory("m1", content="content"))
        assert store._fts_search("") == []
        assert store._fts_search("   ") == []


# ---------------------------------------------------------------------------
# fts_prefilter_memory_ids integration (hybrid.py)
# ---------------------------------------------------------------------------

class TestFtsPrefilter:
    def test_returns_none_when_flag_disabled(self, tmp_path, monkeypatch):
        from depthfusion.retrieval.hybrid import fts_prefilter_memory_ids
        monkeypatch.setenv("DEPTHFUSION_FTS_ENABLED", "false")
        store = MemoryStore(tmp_path / "mem.db")
        result = fts_prefilter_memory_ids(store, "anything")
        assert result is None

    def test_returns_ids_when_flag_enabled(self, tmp_path, monkeypatch):
        from depthfusion.retrieval.hybrid import fts_prefilter_memory_ids
        monkeypatch.setenv("DEPTHFUSION_FTS_ENABLED", "true")
        store = MemoryStore(tmp_path / "mem.db")
        store.upsert(_make_memory("m1", content="asyncpg connection pooling"))
        result = fts_prefilter_memory_ids(store, "asyncpg")
        assert result is not None
        assert "m1" in result

    def test_returns_none_on_store_error(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        from depthfusion.retrieval.hybrid import fts_prefilter_memory_ids
        monkeypatch.setenv("DEPTHFUSION_FTS_ENABLED", "true")
        broken = MagicMock()
        broken._fts_search.side_effect = RuntimeError("db error")
        result = fts_prefilter_memory_ids(broken, "query")
        assert result is None
