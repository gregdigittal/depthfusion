"""AnalyticsCollector — records usage events per principal (E-55).

Each call to :meth:`record_event` appends one row to ``analytics_events``
with the principal_id, event_type, and UTC timestamp.

Supported event types
---------------------
  ``search``  — a recall / search query was executed
  ``ingest``  — a document batch was ingested
  ``sync``    — a connector sync run completed

Unknown types are accepted and stored verbatim so callers do not need a
code change to introduce new event types; the aggregation layer will bucket
unknown types under ``other`` in summary output.
"""
from __future__ import annotations

import logging
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .store import _connect, init_db

logger = logging.getLogger(__name__)

#: The canonical set of event types this collector understands.
KNOWN_EVENT_TYPES: frozenset[str] = frozenset({"search", "ingest", "sync"})


class AnalyticsCollector:
    """Thread-safe recorder of principal usage events.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Created (with parent dirs) if it
        does not exist.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        init_db(self._db_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_event(
        self,
        *,
        principal_id: str,
        event_type: str,
        recorded_at: datetime | None = None,
    ) -> None:
        """Append one usage event to the database.

        Parameters
        ----------
        principal_id:
            Stable identifier for the authenticated caller (``sub`` claim).
        event_type:
            One of ``search``, ``ingest``, ``sync`` (or any custom string).
        recorded_at:
            Timestamp to record; defaults to ``datetime.now(UTC)``.

        Errors are swallowed and logged — observability must never block
        serving.
        """
        if recorded_at is None:
            recorded_at = datetime.now(tz=timezone.utc)
        ts = recorded_at.isoformat()

        try:
            with self._lock, closing(_connect(self._db_path)) as conn:
                conn.execute(
                    "INSERT INTO analytics_events (principal_id, event_type, recorded_at)"
                    " VALUES (?, ?, ?)",
                    (principal_id, event_type, ts),
                )
                conn.commit()
        except Exception:  # noqa: BLE001 — observability must not raise
            logger.exception(
                "analytics: failed to record event type=%r for principal=%r",
                event_type,
                principal_id,
            )

    def recent_events(
        self,
        *,
        principal_id: str,
        since: datetime,
        event_type: str | None = None,
    ) -> list[dict]:
        """Return raw events for *principal_id* since *since*.

        Used by the aggregation service and in tests.

        Parameters
        ----------
        principal_id:
            Filter to this principal only.
        since:
            Lower bound (inclusive) on ``recorded_at``.
        event_type:
            Optional filter; ``None`` returns all event types.
        """
        since_ts = since.isoformat()
        try:
            with closing(_connect(self._db_path)) as conn:
                if event_type is not None:
                    rows = conn.execute(
                        "SELECT principal_id, event_type, recorded_at"
                        "  FROM analytics_events"
                        " WHERE principal_id = ? AND event_type = ? AND recorded_at >= ?"
                        " ORDER BY recorded_at",
                        (principal_id, event_type, since_ts),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT principal_id, event_type, recorded_at"
                        "  FROM analytics_events"
                        " WHERE principal_id = ? AND recorded_at >= ?"
                        " ORDER BY recorded_at",
                        (principal_id, since_ts),
                    ).fetchall()
            return [dict(r) for r in rows]
        except Exception:  # noqa: BLE001
            logger.exception(
                "analytics: failed to query recent events for principal=%r", principal_id
            )
            return []

    def count_events(
        self,
        *,
        principal_id: str,
        event_type: str,
        since: datetime,
    ) -> int:
        """Return the count of *event_type* events for *principal_id* since *since*."""
        since_ts = since.isoformat()
        try:
            with closing(_connect(self._db_path)) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM analytics_events"
                    " WHERE principal_id = ? AND event_type = ? AND recorded_at >= ?",
                    (principal_id, event_type, since_ts),
                ).fetchone()
            return int(row[0]) if row else 0
        except Exception:  # noqa: BLE001
            logger.exception(
                "analytics: failed to count events type=%r for principal=%r",
                event_type,
                principal_id,
            )
            return 0
