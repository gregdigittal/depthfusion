"""SQLite metadata index for fast file change detection and metadata lookup."""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path.home() / ".claude" / ".depthfusion_file_index.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS file_metadata (
    file_path TEXT PRIMARY KEY,
    mtime     REAL NOT NULL,
    size      INTEGER NOT NULL,
    content_hash TEXT,
    project   TEXT,
    importance REAL,
    salience  REAL,
    pinned    INTEGER NOT NULL DEFAULT 0,
    indexed_at REAL NOT NULL
);
"""


class FileMetadataIndex:
    """SQLite cache for file path/mtime/hash/project/importance/salience/pinned.

    Thread-safe via a per-instance lock + SQLite WAL mode.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._path = db_path or _DEFAULT_DB_PATH
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Open (or create) the database and apply schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SCHEMA_SQL)
        self._conn.commit()

    def is_stale(self, file_path: Path) -> bool:
        """Return True if file_path is not cached or mtime/size has changed."""
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT mtime, size FROM file_metadata WHERE file_path = ?",
                (str(file_path),),
            )
            row = cur.fetchone()
            if row is None:
                return True
            cached_mtime, cached_size = row
        try:
            stat = file_path.stat()
        except FileNotFoundError:
            return True
        return stat.st_mtime != cached_mtime or stat.st_size != cached_size

    def update(
        self,
        file_path: Path,
        *,
        project: str | None = None,
        importance: float | None = None,
        salience: float | None = None,
        pinned: bool = False,
        compute_hash: bool = False,
    ) -> None:
        """Upsert the cache entry for file_path with current mtime/size."""
        stat = file_path.stat()
        mtime = stat.st_mtime
        size = stat.st_size

        content_hash: str | None = None
        if compute_hash:
            data = file_path.read_bytes()
            content_hash = hashlib.sha256(data).hexdigest()

        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                """
                INSERT OR REPLACE INTO file_metadata
                    (file_path, mtime, size, content_hash, project,
                     importance, salience, pinned, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(file_path),
                    mtime,
                    size,
                    content_hash,
                    project,
                    importance,
                    salience,
                    1 if pinned else 0,
                    time.time(),
                ),
            )
            self._conn.commit()

    def get(self, file_path: Path) -> dict | None:
        """Return the cached metadata dict for file_path, or None if missing."""
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT file_path, mtime, size, content_hash, project, "
                "importance, salience, pinned, indexed_at "
                "FROM file_metadata WHERE file_path = ?",
                (str(file_path),),
            )
            row = cur.fetchone()
        if row is None:
            return None
        keys = [
            "file_path", "mtime", "size", "content_hash", "project",
            "importance", "salience", "pinned", "indexed_at",
        ]
        result = dict(zip(keys, row))
        result["pinned"] = bool(result["pinned"])
        return result

    def remove(self, file_path: Path) -> None:
        """Remove a cache entry (call when a file is deleted)."""
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                "DELETE FROM file_metadata WHERE file_path = ?",
                (str(file_path),),
            )
            self._conn.commit()

    def list_project(self, project: str) -> list[dict]:
        """Return all cached entries for a given project."""
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT file_path, mtime, size, content_hash, project, "
                "importance, salience, pinned, indexed_at "
                "FROM file_metadata WHERE project = ? ORDER BY file_path",
                (project,),
            )
            rows = cur.fetchall()
        keys = [
            "file_path", "mtime", "size", "content_hash", "project",
            "importance", "salience", "pinned", "indexed_at",
        ]
        results = []
        for row in rows:
            entry = dict(zip(keys, row))
            entry["pinned"] = bool(entry["pinned"])
            results.append(entry)
        return results

    def purge_missing(self) -> int:
        """Remove entries whose file_path no longer exists on disk. Returns count removed."""
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute("SELECT file_path FROM file_metadata")
            all_paths = [row[0] for row in cur.fetchall()]

        missing = [p for p in all_paths if not Path(p).exists()]
        if not missing:
            return 0

        with self._lock:
            assert self._conn is not None
            self._conn.executemany(
                "DELETE FROM file_metadata WHERE file_path = ?",
                [(p,) for p in missing],
            )
            self._conn.commit()
        return len(missing)

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
