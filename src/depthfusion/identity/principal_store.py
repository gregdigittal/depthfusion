"""Thread-safe SQLite backend for persisting authenticated principals.

Each public method opens and closes its own SQLite connection under a
:class:`threading.RLock`, so the store is safe to share across threads
without callers managing connection lifetime.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from .models import Principal

_DDL = """
CREATE TABLE IF NOT EXISTS principals (
    principal_id TEXT PRIMARY KEY,
    upn          TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT "",
    groups       TEXT NOT NULL DEFAULT "[]",
    last_seen    REAL NOT NULL
);
"""


class PrincipalStore:
    """Persistent store for :class:`~depthfusion.identity.Principal` records.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  When *None* (default) the file is
        placed at ``$DEPTHFUSION_DATA_DIR/identity.db``, falling back to
        ``~/.depthfusion/identity.db`` when the env-var is not set.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            data_dir = Path(
                os.environ.get("DEPTHFUSION_DATA_DIR", "~/.depthfusion")
            ).expanduser()
        else:
            data_dir = Path(db_path).parent
            # db_path given explicitly — use it directly
            self._db_path = Path(db_path)
            data_dir.mkdir(parents=True, exist_ok=True)
            self._lock = threading.RLock()
            self._init_db()
            return

        data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = data_dir / "identity.db"
        self._lock = threading.RLock()
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_DDL)

    @staticmethod
    def _row_to_principal(row: sqlite3.Row) -> Principal:
        return Principal(
            principal_id=row["principal_id"],
            upn=row["upn"],
            display_name=row["display_name"],
            groups=json.loads(row["groups"]),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert(self, principal: Principal) -> None:
        """Insert or replace *principal*, updating ``last_seen`` to now.

        Parameters
        ----------
        principal:
            The principal to persist.
        """
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO principals
                        (principal_id, upn, display_name, groups, last_seen)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        principal.principal_id,
                        principal.upn,
                        principal.display_name,
                        json.dumps(principal.groups),
                        time.time(),
                    ),
                )

    def get(self, principal_id: str) -> Principal | None:
        """Return the principal with *principal_id*, or ``None`` if not found.

        Parameters
        ----------
        principal_id:
            The ``sub`` claim / primary key to look up.
        """
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM principals WHERE principal_id = ?",
                    (principal_id,),
                ).fetchone()
        if row is None:
            return None
        return self._row_to_principal(row)

    def list_recent(self, limit: int = 10) -> list[Principal]:
        """Return up to *limit* principals ordered by most-recently seen first.

        Parameters
        ----------
        limit:
            Maximum number of records to return (default 10).
        """
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM principals ORDER BY last_seen DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_principal(r) for r in rows]


__all__ = ["PrincipalStore"]
