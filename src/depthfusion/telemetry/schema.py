"""SQLite schema for model telemetry capture."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = PROJECT_ROOT / "telemetry" / "model_telemetry.db"

TASK_CATEGORIES = frozenset({"code", "review", "planning", "search", "summarise", "other"})
QUALITY_VERDICTS = frozenset({"pass", "fail", "retry"})

DDL = """
CREATE TABLE IF NOT EXISTS model_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    session_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    task_category TEXT NOT NULL CHECK(
        task_category IN ('code','review','planning','search','summarise','other')
    ),
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    quality_verdict TEXT CHECK(
        quality_verdict IS NULL OR quality_verdict IN ('pass','fail','retry')
    ),
    project_slug TEXT
);

CREATE INDEX IF NOT EXISTS idx_model_telemetry_recorded_at
    ON model_telemetry(recorded_at);
CREATE INDEX IF NOT EXISTS idx_model_telemetry_project_slug
    ON model_telemetry(project_slug);
CREATE INDEX IF NOT EXISTS idx_model_telemetry_model_id
    ON model_telemetry(model_id);
"""


def get_db_path() -> Path:
    """Return the configured model telemetry DB path."""
    configured = os.getenv("TELEMETRY_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    return DB_PATH


def connect() -> sqlite3.Connection:
    """Open a row-factory SQLite connection to the model telemetry DB."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def migrate() -> None:
    """Create the model telemetry schema if it does not already exist."""
    get_db_path().parent.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as conn:
        conn.executescript(DDL)
        conn.commit()
