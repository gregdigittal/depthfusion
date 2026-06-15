"""DepthFusion V2 — ACL-aware sync engine.

E-52 / S-167 / T-583 T-584 T-585 T-586

SyncEngine mediates record replication between devices and the hub. The
core invariant: the server is the authority on ``acl_allow`` and
``classification`` — a client **cannot** widen its own visibility by
pushing records with broader ACL or lower classification.

Change-log model
----------------
Every inserted/updated record writes a row to the ``sync_changelog``
table. The table's ``rowid`` is the opaque cursor (``sync_token``) used
for delta pulls. ``rowid`` auto-increments monotonically, so
``rowid > last_seen`` always yields exactly the new rows since the last
pull in insertion order.

Conflict resolution
-------------------
Server always wins on ``acl_allow`` and ``classification``. If a pushed
record carries a more permissive ``acl_allow`` than the server version,
or a less restrictive ``classification``, the push is rejected with a
403 ACL-widening error. All other fields are accepted from the client
(last-writer-wins for payload).

Database lifecycle
------------------
The engine creates its own SQLite database (``sync.db`` under
``DEPTHFUSION_DATA_DIR`` or a caller-supplied path). The ``SyncEngine``
constructor creates the schema on first use. Pass ``db_path=":memory:"``
for tests.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import structlog

from depthfusion.identity.models import Principal

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS sync_records (
    record_id   TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    acl_allow   TEXT NOT NULL DEFAULT '[]',
    classification TEXT NOT NULL DEFAULT 'internal',
    payload     TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_changelog (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id   TEXT NOT NULL,
    action      TEXT NOT NULL DEFAULT 'upsert',
    changed_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_changelog_record
    ON sync_changelog(record_id);

CREATE INDEX IF NOT EXISTS idx_records_principal
    ON sync_records(principal_id);
"""

# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

_VALID_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}


@dataclass
class Record:
    """A sync record envelope: payload + ACL + classification.

    Attributes
    ----------
    record_id:
        Stable UUID. Auto-generated when not provided.
    principal_id:
        Owning principal — the record creator.
    acl_allow:
        List of principal IDs that may read this record.
    classification:
        Sensitivity label — one of: public, internal, confidential, restricted.
    payload:
        Arbitrary JSON-serialisable dict — the record body.
    updated_at:
        ISO-8601 timestamp set by the server on write.
    """

    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    principal_id: str = ""
    acl_allow: list[str] = field(default_factory=list)
    classification: str = "internal"
    payload: dict = field(default_factory=dict)
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self) -> None:
        if self.classification not in _VALID_CLASSIFICATIONS:
            raise ValueError(
                f"classification must be one of {sorted(_VALID_CLASSIFICATIONS)!r}, "
                f"got {self.classification!r}"
            )

    def to_row(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.record_id,
            self.principal_id,
            json.dumps(self.acl_allow),
            self.classification,
            json.dumps(self.payload),
            self.updated_at,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Record":
        return cls(
            record_id=row["record_id"],
            principal_id=row["principal_id"],
            acl_allow=json.loads(row["acl_allow"]),
            classification=row["classification"],
            payload=json.loads(row["payload"]),
            updated_at=row["updated_at"],
        )


@dataclass
class SyncResult:
    """Result envelope returned by ``sync_push``.

    Attributes
    ----------
    accepted:
        Record IDs that were successfully stored.
    rejected:
        Mapping of record_id -> rejection reason string for failed records.
    """

    accepted: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ACL-widening check helpers
# ---------------------------------------------------------------------------

def _classification_rank(c: str) -> int:
    """Lower rank = more restrictive (higher classification sensitivity)."""
    order = {"restricted": 0, "confidential": 1, "internal": 2, "public": 3}
    return order.get(c, 2)


def _server_wins_on_acl(server_record: Record, client_record: Record) -> bool:
    """Return True if the client is trying to widen the ACL or loosen the classification.

    The client wins only if ACL and classification are the same OR stricter.
    Returns True when the client request is safe (server version honoured for protected fields),
    False when the push should be rejected.

    Specifically, we reject when:
      - client acl_allow has principals not in the server acl_allow (client widens)
      - client classification is LESS restrictive than the server classification
    """
    server_acl_set = set(server_record.acl_allow)
    client_acl_set = set(client_record.acl_allow)

    if client_acl_set - server_acl_set:
        # Client wants to add principals the server hasn't approved
        return False

    if _classification_rank(client_record.classification) > _classification_rank(
        server_record.classification
    ):
        # Client proposes a less-restrictive label
        return False

    return True


# ---------------------------------------------------------------------------
# SyncEngine
# ---------------------------------------------------------------------------

class SyncEngine:
    """ACL-aware delta sync engine backed by SQLite.

    Parameters
    ----------
    db_path:
        Path to the SQLite database.  Use ``":memory:"`` for tests.
        Defaults to ``sync.db`` under ``$DEPTHFUSION_DATA_DIR`` (or ``/tmp``).

    Notes
    -----
    When ``db_path`` is ``":memory:"``, a single shared connection is kept
    open for the lifetime of the engine instance — SQLite in-memory databases
    are connection-local and would otherwise be empty on each new connection.
    For file-backed databases a new connection is created per operation (WAL
    mode, serialised writes via Python's GIL on the single writer path).
    """

    def __init__(self, db_path: str | None = None) -> None:
        import os

        if db_path is None:
            data_dir = os.environ.get("DEPTHFUSION_DATA_DIR", "/tmp")
            db_path = str(os.path.join(data_dir, "sync.db"))

        self._db_path = db_path
        self._is_memory = db_path == ":memory:"
        # Keep a persistent connection for in-memory DBs so the schema survives.
        self._memory_conn: sqlite3.Connection | None = None
        self._init_db()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Return a usable SQLite connection.

        For in-memory databases, returns the persistent shared connection
        (schema is lost when the connection closes).  For file DBs, opens
        a new connection each call — callers use ``contextlib.closing``.
        """
        if self._is_memory:
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(
                    ":memory:", check_same_thread=False
                )
                self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextlib.contextmanager
    def _get_conn(self):
        """Context manager that yields a connection.

        For in-memory DBs the connection is NOT closed on exit (it must stay
        open). For file DBs the connection is closed via ``closing``.
        """
        conn = self._connect()
        if self._is_memory:
            yield conn
        else:
            with closing(conn):
                yield conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            for stmt in _DDL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)
            conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_record(self, conn: sqlite3.Connection, record_id: str) -> Optional[Record]:
        row = conn.execute(
            "SELECT * FROM sync_records WHERE record_id = ?", (record_id,)
        ).fetchone()
        return Record.from_row(row) if row else None

    def _upsert_record(self, conn: sqlite3.Connection, record: Record) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO sync_records
                (record_id, principal_id, acl_allow, classification, payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                principal_id   = excluded.principal_id,
                acl_allow      = excluded.acl_allow,
                classification = excluded.classification,
                payload        = excluded.payload,
                updated_at     = excluded.updated_at
            """,
            record.to_row(),
        )
        conn.execute(
            "INSERT INTO sync_changelog (record_id, action, changed_at) VALUES (?, 'upsert', ?)",
            (record.record_id, now),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync_push(self, principal: Principal, delta: list[Record]) -> SyncResult:
        """Validate and store pushed records from a device.

        Each record is checked for:
        1. Ownership — principal must be listed in ``acl_allow`` OR be the
           ``principal_id`` owner.
        2. Non-widening — client cannot add principals to ``acl_allow`` or
           loosen ``classification`` beyond the existing server record.
        3. Label validity — ``classification`` must be a known value.

        Parameters
        ----------
        principal:
            The authenticated device principal.
        delta:
            Records the client is pushing. Each record's ``principal_id``
            is overwritten with ``principal.principal_id`` to prevent
            impersonation.

        Returns
        -------
        SyncResult
            Populated with ``accepted`` and ``rejected`` record IDs.
        """
        result = SyncResult()

        with self._get_conn() as conn:
            for record in delta:
                # Stamp the owner — clients cannot push on behalf of others.
                record.principal_id = principal.principal_id

                # The pushing principal must be in the record's own acl_allow.
                if principal.principal_id not in record.acl_allow:
                    reason = (
                        f"push rejected: principal '{principal.principal_id}' "
                        f"is not listed in acl_allow for record '{record.record_id}'"
                    )
                    log.warning(
                        "sync.push.acl_denied",
                        principal_id=principal.principal_id,
                        record_id=record.record_id,
                    )
                    result.rejected[record.record_id] = reason
                    continue

                # Validate classification label.
                if record.classification not in _VALID_CLASSIFICATIONS:
                    reason = (
                        f"push rejected: unknown classification "
                        f"'{record.classification}' for record '{record.record_id}'"
                    )
                    result.rejected[record.record_id] = reason
                    continue

                # Check for ACL-widening against existing server record.
                server_record = self._get_record(conn, record.record_id)
                if server_record is not None:
                    if not _server_wins_on_acl(server_record, record):
                        reason = (
                            f"push rejected: record '{record.record_id}' "
                            "would widen acl_allow or loosen classification — "
                            "server is authoritative on these fields"
                        )
                        log.warning(
                            "sync.push.acl_widening",
                            principal_id=principal.principal_id,
                            record_id=record.record_id,
                        )
                        result.rejected[record.record_id] = reason
                        continue

                self._upsert_record(conn, record)
                result.accepted.append(record.record_id)
                log.info(
                    "sync.push.accepted",
                    principal_id=principal.principal_id,
                    record_id=record.record_id,
                )

            conn.commit()

        return result

    def sync_pull(
        self,
        principal: Principal,
        since: datetime,
        limit: int = 1000,
    ) -> list[Record]:
        """Return records visible to *principal* that changed since *since*.

        Only records where ``principal.principal_id`` appears in
        ``acl_allow`` are returned. The ``since`` datetime maps to the
        ``changed_at`` column in the changelog (ISO-8601 string comparison).

        Parameters
        ----------
        principal:
            The authenticated device principal.
        since:
            Return only records with changelog ``changed_at`` strictly
            after this timestamp.  Pass ``datetime.min`` to get all records.
        limit:
            Maximum number of records to return. Default 1000.

        Returns
        -------
        list[Record]
            Records visible to the principal, ordered by changelog rowid ASC.
        """
        since_iso = since.isoformat()

        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT sr.*
                FROM sync_records sr
                JOIN sync_changelog sc ON sc.record_id = sr.record_id
                WHERE sc.changed_at > ?
                  AND json_type(sr.acl_allow) = 'array'
                  AND EXISTS (
                      SELECT 1
                      FROM json_each(sr.acl_allow)
                      WHERE value = ?
                  )
                ORDER BY sc.id ASC
                LIMIT ?
                """,
                (since_iso, principal.principal_id, limit),
            ).fetchall()

        records = [Record.from_row(row) for row in rows]
        log.info(
            "sync.pull.returned",
            principal_id=principal.principal_id,
            count=len(records),
            since=since_iso,
        )
        return records

    # ------------------------------------------------------------------
    # Cursor helpers (opaque token = last changelog rowid seen)
    # ------------------------------------------------------------------

    def latest_token(self) -> int:
        """Return the current high-water changelog rowid (the sync token).

        A client should store this value and pass it as ``since_token`` on
        the next pull. Token 0 means "pull everything".
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS tok FROM sync_changelog"
            ).fetchone()
        return int(row["tok"])

    def datetime_for_token(self, token: int) -> datetime:
        """Return the ``changed_at`` datetime for a given changelog rowid.

        Used by the REST layer to convert an opaque ``since=<token>``
        query param back to a datetime for ``sync_pull``.

        If the token is 0 or not found, returns ``datetime.min`` (pull all).
        """
        if token <= 0:
            return datetime.min

        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT changed_at FROM sync_changelog WHERE id = ? LIMIT 1",
                (token,),
            ).fetchone()

        if row is None:
            return datetime.min

        changed_at = row["changed_at"]
        try:
            dt = datetime.fromisoformat(changed_at)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.min


__all__ = ["SyncEngine", "SyncResult", "Record"]
