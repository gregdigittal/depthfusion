"""SQLite-backed store for analytics usage events (E-55).

Schema
------
  analytics_events  — one row per usage event (search / ingest / sync)
  analytics_rollups — pre-computed daily/weekly counts per principal + event_type

All connections go through :func:`_connect` which enforces WAL mode and the
``contextlib.closing`` pattern so raw ``conn.close()`` calls are never needed.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS analytics_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    principal_id TEXT    NOT NULL,
    event_type   TEXT    NOT NULL,
    recorded_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ae_principal ON analytics_events(principal_id);
CREATE INDEX IF NOT EXISTS idx_ae_event_type ON analytics_events(event_type);
CREATE INDEX IF NOT EXISTS idx_ae_recorded ON analytics_events(recorded_at);

-- T-622 facet performance pass.
-- The hot path for the summary / facet endpoints is:
--   WHERE principal_id = ? AND recorded_at >= ? AND recorded_at <= ?
--   GROUP BY event_type
-- A composite index on (principal_id, recorded_at, event_type) is *covering*
-- for that query: SQLite can seek the principal + range on the leading columns
-- and read the trailing event_type for the GROUP BY without touching the table.
-- This removes the full-table scan the three single-column indexes left behind.
CREATE INDEX IF NOT EXISTS idx_ae_facet_principal_recorded_type
    ON analytics_events(principal_id, recorded_at, event_type);

CREATE TABLE IF NOT EXISTS analytics_rollups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    principal_id TEXT    NOT NULL,
    event_type   TEXT    NOT NULL,
    period       TEXT    NOT NULL,
    period_start TEXT    NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    computed_at  TEXT    NOT NULL,
    UNIQUE(principal_id, event_type, period, period_start)
);

CREATE INDEX IF NOT EXISTS idx_ar_principal ON analytics_rollups(principal_id);
CREATE INDEX IF NOT EXISTS idx_ar_period_start ON analytics_rollups(period_start);
"""


def _connect(path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode enabled.

    Callers MUST use ``contextlib.closing`` or a ``with`` block — never
    call ``conn.close()`` directly.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path) -> None:
    """Create tables if they do not already exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as conn:
        conn.executescript(_DDL)
        conn.commit()
