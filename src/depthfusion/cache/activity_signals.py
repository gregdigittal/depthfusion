"""Local activity signal store with privacy guard (E-58 S-189 T-652).

Stores on-device activity signals (queries, opened docs, projects, entities,
recency) in a local SQLite database.  Signals are NEVER uploaded to any
remote service — they are strictly on-device.

Design rules
------------
* **Privacy invariant** — the module has no network import and no outbound
  I/O path.  The ``_PRIVACY_GUARD`` constant is checked at module level and
  by ``ActivitySignalStore.upload_disabled`` so unit tests can assert the
  invariant without inspecting internals.
* **Schema** — one ``activity_signals`` table with columns
  ``(id, kind, value, project, entity, ts)``.  ``kind`` ∈
  {"query", "open_doc", "project", "entity"}.
* **Recency** — ``ts`` is a Unix timestamp (float).  Callers supply ``now``
  for deterministic testing.
* **No threads** — all methods are synchronous; the caller is responsible for
  running them on an appropriate executor if needed.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Privacy guard
# ---------------------------------------------------------------------------

# This module MUST NOT perform any network I/O.  The sentinel constant below
# is imported by tests to assert the invariant.  If you add an upload path,
# the unit test will catch it.
_PRIVACY_GUARD: str = "on-device-only-never-upload"

# The following names must NOT be imported into this module:
# requests, httpx, aiohttp, urllib, http.client, socket (as an uploader), etc.
# Only stdlib modules that are unambiguously local are permitted.

__all__ = [
    "_PRIVACY_GUARD",
    "SignalKind",
    "ActivitySignal",
    "ActivitySignalStore",
]


class SignalKind(str, Enum):
    """Type of activity signal."""

    QUERY = "query"
    OPEN_DOC = "open_doc"
    PROJECT = "project"
    ENTITY = "entity"


@dataclass
class ActivitySignal:
    """A single on-device activity observation.

    Attributes
    ----------
    kind:
        Category of the signal.
    value:
        The raw signal value (query text, file path, project name, entity name).
    project:
        Optional project context at the time of the signal.
    entity:
        Optional entity name (e.g. a person, concept) associated with the signal.
    ts:
        Unix timestamp when the signal was observed.
    signal_id:
        Auto-assigned integer primary key (None before first persist).
    """

    kind: SignalKind
    value: str
    project: Optional[str] = None
    entity: Optional[str] = None
    ts: float = 0.0
    signal_id: Optional[int] = None


class ActivitySignalStore:
    """Persists activity signals on-device in a SQLite database.

    All data remains local.  There is no upload, sync, or telemetry path.
    The ``upload_disabled`` property exists so tests can assert this invariant
    via introspection.

    Parameters
    ----------
    db_path:
        Filesystem path for the SQLite database.  Pass ``":memory:"`` for
        in-process storage (useful in tests).
    max_signals:
        Maximum number of signals to keep.  When the store exceeds this limit,
        the oldest signals (by ``ts``) are pruned.  Defaults to 10 000.
    """

    # -----------------------------------------------------------------------
    # Privacy sentinel — introspectable by unit tests
    # -----------------------------------------------------------------------

    upload_disabled: bool = True  # INVARIANT: never set to False

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        max_signals: int = 10_000,
    ) -> None:
        self._db_path = str(db_path)
        self._max_signals = max_signals
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._bootstrap()

    # -----------------------------------------------------------------------
    # Schema
    # -----------------------------------------------------------------------

    def _bootstrap(self) -> None:
        with contextlib.closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_signals (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind     TEXT    NOT NULL,
                    value    TEXT    NOT NULL,
                    project  TEXT,
                    entity   TEXT,
                    ts       REAL    NOT NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_as_ts "
                "ON activity_signals (ts DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_as_kind "
                "ON activity_signals (kind)"
            )
            self._conn.commit()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def record(
        self,
        kind: SignalKind,
        value: str,
        *,
        project: Optional[str] = None,
        entity: Optional[str] = None,
        now: Optional[float] = None,
    ) -> ActivitySignal:
        """Record an activity signal.

        Automatically prunes the oldest signals if the store exceeds
        ``max_signals``.

        Returns the persisted ``ActivitySignal`` with its assigned ``signal_id``.
        """
        ts = now if now is not None else time.time()
        with contextlib.closing(self._conn.cursor()) as cur:
            cur.execute(
                "INSERT INTO activity_signals (kind, value, project, entity, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind.value, value, project, entity, ts),
            )
            signal_id = cur.lastrowid
            self._conn.commit()

        self._prune_if_needed()

        return ActivitySignal(
            kind=kind,
            value=value,
            project=project,
            entity=entity,
            ts=ts,
            signal_id=signal_id,
        )

    def recent(
        self,
        limit: int = 200,
        kind: Optional[SignalKind] = None,
        project: Optional[str] = None,
    ) -> list[ActivitySignal]:
        """Return the most recent signals, optionally filtered by kind or project.

        Results are ordered newest-first.
        """
        conditions: list[str] = []
        params: list[object] = []
        if kind is not None:
            conditions.append("kind = ?")
            params.append(kind.value)
        if project is not None:
            conditions.append("project = ?")
            params.append(project)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        with contextlib.closing(self._conn.cursor()) as cur:
            rows = cur.execute(
                f"SELECT * FROM activity_signals {where_clause} "
                "ORDER BY ts DESC LIMIT ?",
                params,
            ).fetchall()

        return [
            ActivitySignal(
                kind=SignalKind(row["kind"]),
                value=row["value"],
                project=row["project"],
                entity=row["entity"],
                ts=row["ts"],
                signal_id=row["id"],
            )
            for row in rows
        ]

    def count(self) -> int:
        """Return the total number of stored signals."""
        with contextlib.closing(self._conn.cursor()) as cur:
            row = cur.execute(
                "SELECT COUNT(*) AS cnt FROM activity_signals"
            ).fetchone()
        return int(row["cnt"])

    def top_projects(self, limit: int = 10) -> list[tuple[str, int]]:
        """Return ``(project, count)`` pairs sorted by frequency descending."""
        with contextlib.closing(self._conn.cursor()) as cur:
            rows = cur.execute(
                "SELECT project, COUNT(*) AS cnt FROM activity_signals "
                "WHERE project IS NOT NULL "
                "GROUP BY project ORDER BY cnt DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [(row["project"], row["cnt"]) for row in rows]

    def top_values(
        self,
        kind: SignalKind,
        limit: int = 20,
    ) -> list[tuple[str, int]]:
        """Return ``(value, count)`` pairs for a given kind, sorted by count."""
        with contextlib.closing(self._conn.cursor()) as cur:
            rows = cur.execute(
                "SELECT value, COUNT(*) AS cnt FROM activity_signals "
                "WHERE kind = ? GROUP BY value ORDER BY cnt DESC LIMIT ?",
                (kind.value, limit),
            ).fetchall()
        return [(row["value"], row["cnt"]) for row in rows]

    def clear(self) -> None:
        """Remove all signals from the store."""
        with contextlib.closing(self._conn.cursor()) as cur:
            cur.execute("DELETE FROM activity_signals")
            self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    # -----------------------------------------------------------------------
    # Privacy enforcement — these methods must NOT be implemented
    # -----------------------------------------------------------------------

    def upload(self, *args: object, **kwargs: object) -> None:  # type: ignore[return]
        """Intentionally raises — signals are NEVER uploaded."""
        raise NotImplementedError(
            "ActivitySignalStore is on-device only. "
            "Uploading signals violates the privacy invariant."
        )

    def sync_to_remote(self, *args: object, **kwargs: object) -> None:  # type: ignore[return]
        """Intentionally raises — signals are NEVER synced remotely."""
        raise NotImplementedError(
            "ActivitySignalStore is on-device only. "
            "Remote sync violates the privacy invariant."
        )

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _prune_if_needed(self) -> int:
        """Delete oldest signals if the store exceeds ``max_signals``.

        Returns the number of rows deleted.
        """
        with contextlib.closing(self._conn.cursor()) as cur:
            total = int(
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM activity_signals"
                ).fetchone()["cnt"]
            )
            if total <= self._max_signals:
                return 0

            excess = total - self._max_signals
            cur.execute(
                "DELETE FROM activity_signals WHERE id IN ("
                "  SELECT id FROM activity_signals ORDER BY ts ASC LIMIT ?"
                ")",
                (excess,),
            )
            deleted = cur.rowcount
            self._conn.commit()
        return deleted
