-- Migration 0001: Add ACL columns to all six DepthFusion stores
-- E-50 Authorization Model — T-561
-- Backfill default: acl_allow='["greg"]', classification='internal' (V2-DEC-002)
--
-- This migration is applied once per SQLite database via the
-- _df_schema_migrations tracking table.  Running it twice is safe because
-- each ALTER TABLE is guarded by that table.
--
-- Stores covered:
--   1. memories      (MemoryStore)
--   2. file_metadata (FileIndex)
--   3. entities      (GraphStore — entity records)
--   4. edges         (GraphStore — edge records)
--   5. candidate_skills  (TelemetryStore)
--   6. telemetry_events  (TelemetryStore)
--
-- VectorStore (ChromaDB) and EventLog (NDJSON) carry ACL in metadata /
-- JSON lines respectively; they are handled by the backfill script, not SQL.

-- ── MemoryStore ───────────────────────────────────────────────────────────
ALTER TABLE memories
    ADD COLUMN acl_allow TEXT DEFAULT '["greg"]';

ALTER TABLE memories
    ADD COLUMN classification TEXT DEFAULT 'internal';

CREATE INDEX IF NOT EXISTS idx_memories_classification
    ON memories(classification);

-- ── FileIndex ─────────────────────────────────────────────────────────────
ALTER TABLE file_metadata
    ADD COLUMN acl_allow TEXT DEFAULT '["greg"]';

ALTER TABLE file_metadata
    ADD COLUMN classification TEXT DEFAULT 'internal';

CREATE INDEX IF NOT EXISTS idx_file_metadata_classification
    ON file_metadata(classification);

-- ── GraphStore — entities ─────────────────────────────────────────────────
ALTER TABLE entities
    ADD COLUMN acl_allow TEXT DEFAULT '["greg"]';

ALTER TABLE entities
    ADD COLUMN classification TEXT DEFAULT 'internal';

CREATE INDEX IF NOT EXISTS idx_entities_classification
    ON entities(classification);

-- ── GraphStore — edges ────────────────────────────────────────────────────
ALTER TABLE edges
    ADD COLUMN acl_allow TEXT DEFAULT '["greg"]';

ALTER TABLE edges
    ADD COLUMN classification TEXT DEFAULT 'internal';

CREATE INDEX IF NOT EXISTS idx_edges_classification
    ON edges(classification);

-- ── TelemetryStore — candidate_skills ────────────────────────────────────
ALTER TABLE candidate_skills
    ADD COLUMN acl_allow TEXT DEFAULT '["greg"]';

ALTER TABLE candidate_skills
    ADD COLUMN classification TEXT DEFAULT 'internal';

CREATE INDEX IF NOT EXISTS idx_candidate_skills_classification
    ON candidate_skills(classification);

-- ── TelemetryStore — telemetry_events ────────────────────────────────────
ALTER TABLE telemetry_events
    ADD COLUMN acl_allow TEXT DEFAULT '["greg"]';

ALTER TABLE telemetry_events
    ADD COLUMN classification TEXT DEFAULT 'internal';

CREATE INDEX IF NOT EXISTS idx_telemetry_events_classification
    ON telemetry_events(classification);
