"""DepthFusion V2 — Append-only audit log backed by SQLite.

Schema
------
::

    CREATE TABLE IF NOT EXISTS audit_events (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type        TEXT    NOT NULL,
        actor_principal_id TEXT   NOT NULL,
        resource_id       TEXT    NOT NULL DEFAULT "",
        classification    TEXT    NOT NULL DEFAULT "",
        timestamp         REAL    NOT NULL,
        ip_addr           TEXT    NOT NULL DEFAULT "",
        success           INTEGER NOT NULL DEFAULT 1
    );

Invariants
----------
* The table is **append-only**: no rows are ever deleted or updated.
* Every public method acquires a threading.RLock before touching SQLite.
* All connections use ``contextlib.closing`` — no bare ``conn.close()``.

Usage
-----
::

    from depthfusion.audit.log import AuditStore, AuditEvent, AuditEventType

    store = AuditStore(db_path=Path("/data/audit.db"))
    store.log(AuditEvent(
        event_type=AuditEventType.LOGIN,
        actor_principal_id="principal-abc",
        ip_addr="127.0.0.1",
        success=True,
    ))
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Event taxonomy
# ---------------------------------------------------------------------------

class AuditEventType(str, Enum):
    """Canonical event types that the audit log recognises."""

    # Authentication
    LOGIN = "login"
    LOGOUT = "logout"
    TOKEN_ISSUED = "token_issued"
    TOKEN_REVOKED = "token_revoked"

    # Record access
    RECORD_READ = "record_read"
    RECORD_CREATED = "record_created"
    RECORD_UPDATED = "record_updated"
    RECORD_DELETED = "record_deleted"

    # Authorization
    AUTHZ_DENIED = "authz_denied"
    ACL_CHANGED = "acl_changed"

    # Role management
    ROLE_GRANTED = "role_granted"
    ROLE_REVOKED = "role_revoked"

    # Device lifecycle
    DEVICE_ENROLLED = "device_enrolled"
    DEVICE_REVOKED = "device_revoked"

    # Ingestion / sync
    INGESTION_RUN = "ingestion_run"
    SYNC_SESSION = "sync_session"

    # Export
    EXPORT_STARTED = "export_started"
    EXPORT_ALLOWED = "export_allowed"
    EXPORT_DENIED = "export_denied"
    EXPORT_RATE_LIMITED = "export_rate_limited"

    # Anomaly detection
    ANOMALY_DETECTED = "anomaly_detected"

    # Admin
    ADMIN_ACTION = "admin_action"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AuditEvent:
    """A single audit record.

    Attributes
    ----------
    event_type:
        Category of the event (from :class:`AuditEventType`).
    actor_principal_id:
        The ``principal_id`` of the actor who triggered the event.  Use
        ``"system"`` for automated/cron-driven events.
    resource_id:
        Identifier of the affected resource (record id, device id, …).
        Empty string when not applicable.
    classification:
        Data-classification label of the accessed resource, if any.
    timestamp:
        Unix timestamp (float, seconds).  Defaults to ``time.time()``.
    ip_addr:
        IP address of the request origin.  Empty string when unknown.
    success:
        ``True`` if the operation succeeded; ``False`` for denied/failed
        attempts.
    """

    event_type: AuditEventType
    actor_principal_id: str
    resource_id: str = ""
    classification: str = ""
    timestamp: float = field(default_factory=time.time)
    ip_addr: str = ""
    success: bool = True
    device_id: str = ""
    project_id: str = ""


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type         TEXT    NOT NULL,
    actor_principal_id TEXT    NOT NULL,
    resource_id        TEXT    NOT NULL DEFAULT "",
    classification     TEXT    NOT NULL DEFAULT "",
    timestamp          REAL    NOT NULL,
    ip_addr            TEXT    NOT NULL DEFAULT "",
    success            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_audit_actor
    ON audit_events (actor_principal_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type
    ON audit_events (event_type);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON audit_events (timestamp);
"""


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class AuditStore:
    """Thread-safe, append-only audit log backed by SQLite.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  The parent directory must
        exist.  Defaults to ``~/.depthfusion/data/audit.db``.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            data_dir = Path.home() / ".depthfusion" / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = data_dir / "audit.db"
        else:
            self._db_path = Path(db_path)
        self._lock = threading.RLock()
        self._init_db()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with closing(self._connect()) as conn:
                conn.executescript(_DDL)
                conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log(self, event: AuditEvent) -> int:
        """Append *event* to the audit log.

        Parameters
        ----------
        event:
            The :class:`AuditEvent` to persist.

        Returns
        -------
        int
            The auto-assigned row id of the new record.
        """
        with self._lock:
            with closing(self._connect()) as conn:
                cur = conn.execute(
                    """
                    INSERT INTO audit_events
                        (event_type, actor_principal_id, resource_id,
                         classification, timestamp, ip_addr, success)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event.event_type.value),
                        event.actor_principal_id,
                        event.resource_id,
                        event.classification,
                        event.timestamp,
                        event.ip_addr,
                        1 if event.success else 0,
                    ),
                )
                conn.commit()
                return cur.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Read (admin-only in the REST layer; store itself is unrestricted)
    # ------------------------------------------------------------------

    def query(
        self,
        *,
        since: float | None = None,
        actor: str | None = None,
        event_type: AuditEventType | str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Return audit events matching the given filters.

        Parameters
        ----------
        since:
            Only return events with ``timestamp >= since`` (Unix float).
        actor:
            Filter by ``actor_principal_id``.
        event_type:
            Filter by event type string or :class:`AuditEventType`.
        limit:
            Maximum number of rows to return (default 500; hard-capped at
            10 000 to prevent accidental full-table scans).

        Returns
        -------
        list[dict]
            List of event dictionaries, ordered by ``timestamp`` ascending.
        """
        limit = min(limit, 10_000)
        conditions: list[str] = []
        params: list[object] = []

        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since)
        if actor is not None:
            conditions.append("actor_principal_id = ?")
            params.append(actor)
        if event_type is not None:
            val = event_type.value if isinstance(event_type, AuditEventType) else str(event_type)
            conditions.append("event_type = ?")
            params.append(val)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT id, event_type, actor_principal_id, resource_id,
                   classification, timestamp, ip_addr, success
            FROM   audit_events
            {where_clause}
            ORDER  BY timestamp ASC
            LIMIT  ?
        """
        params.append(limit)

        with self._lock:
            with closing(self._connect()) as conn:
                rows = conn.execute(sql, params).fetchall()

        return [
            {
                "id": row["id"],
                "event_type": row["event_type"],
                "actor_principal_id": row["actor_principal_id"],
                "resource_id": row["resource_id"],
                "classification": row["classification"],
                "timestamp": row["timestamp"],
                "ip_addr": row["ip_addr"],
                "success": bool(row["success"]),
            }
            for row in rows
        ]

    def count(self) -> int:
        """Return total number of audit events stored."""
        with self._lock:
            with closing(self._connect()) as conn:
                row = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()
                return int(row[0])

    def purge_before(self, cutoff: float) -> int:
        """Delete audit events older than *cutoff* (Unix timestamp, seconds).

        This is the enforcement primitive for a compliance retention policy:
        events with ``timestamp < cutoff`` are permanently removed.  The store
        is otherwise append-only — purging is an explicit, audited admin action
        invoked only by retention enforcement.

        Parameters
        ----------
        cutoff:
            Unix timestamp (float).  Events strictly older than this are
            deleted.

        Returns
        -------
        int
            The number of events deleted.
        """
        with self._lock:
            with closing(self._connect()) as conn:
                cur = conn.execute(
                    "DELETE FROM audit_events WHERE timestamp < ?",
                    (cutoff,),
                )
                conn.commit()
                return int(cur.rowcount)


__all__ = [
    "AuditEvent",
    "AuditEventType",
    "AuditStore",
]
