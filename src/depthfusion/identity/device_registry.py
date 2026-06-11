"""Thread-safe SQLite backend for the device registry.

Stores device records in the same ``principal_store`` DB file used by
:class:`~depthfusion.identity.principal_store.PrincipalStore`.  Each public
method opens and closes its own SQLite connection under a
:class:`threading.RLock`.

Schema
------
::

    CREATE TABLE IF NOT EXISTS devices (
        device_id          TEXT PRIMARY KEY,
        owner_principal_id TEXT NOT NULL,
        platform           TEXT NOT NULL DEFAULT "",
        last_sync          REAL NOT NULL,
        revoked            INTEGER NOT NULL DEFAULT 0
    );
"""
from __future__ import annotations

import sqlite3
import sys
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


_DDL = """
CREATE TABLE IF NOT EXISTS devices (
    device_id          TEXT PRIMARY KEY,
    owner_principal_id TEXT NOT NULL,
    platform           TEXT NOT NULL DEFAULT "",
    last_sync          REAL NOT NULL,
    revoked            INTEGER NOT NULL DEFAULT 0
);
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DeviceRecord:
    """A registered device row.

    Attributes
    ----------
    device_id:
        Opaque unique identifier for this device.
    owner_principal_id:
        The ``principal_id`` of the user who enrolled this device.
    platform:
        OS platform string (e.g. ``"linux"``, ``"darwin"``, ``"win32"``).
    last_sync:
        Unix timestamp (seconds, float) of the most recent successful sync.
    revoked:
        ``True`` if the device has been administratively revoked.
    """

    device_id: str
    owner_principal_id: str
    platform: str = ""
    last_sync: float = 0.0
    revoked: bool = False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class DeviceRegistry:
    """Persistent store for :class:`DeviceRecord` rows.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Shared with
        :class:`~depthfusion.identity.principal_store.PrincipalStore` when
        given the same path.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
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
            with closing(self._connect()) as conn:
                conn.executescript(_DDL)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> DeviceRecord:
        return DeviceRecord(
            device_id=row["device_id"],
            owner_principal_id=row["owner_principal_id"],
            platform=row["platform"],
            last_sync=float(row["last_sync"]),
            revoked=bool(row["revoked"]),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        device_id: str,
        owner_principal_id: str,
        platform: str = "",
    ) -> DeviceRecord:
        """Insert a new device record (or replace if *device_id* already exists).

        Parameters
        ----------
        device_id:
            Unique device identifier.
        owner_principal_id:
            ``principal_id`` of the enrolling user.
        platform:
            OS platform string.  Defaults to :data:`sys.platform` when empty.

        Returns
        -------
        DeviceRecord
            The newly created (or replaced) record.
        """
        if not platform:
            platform = sys.platform
        now = time.time()
        with self._lock:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO devices
                        (device_id, owner_principal_id, platform, last_sync, revoked)
                    VALUES (?, ?, ?, ?, 0)
                    """,
                    (device_id, owner_principal_id, platform, now),
                )
                conn.commit()
        return DeviceRecord(
            device_id=device_id,
            owner_principal_id=owner_principal_id,
            platform=platform,
            last_sync=now,
            revoked=False,
        )

    def get(self, device_id: str) -> DeviceRecord | None:
        """Return the device with *device_id*, or ``None`` if not found.

        Parameters
        ----------
        device_id:
            The device primary key to look up.
        """
        with self._lock:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT * FROM devices WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_all(self) -> list[DeviceRecord]:
        """Return all device records ordered by ``last_sync`` descending."""
        with self._lock:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT * FROM devices ORDER BY last_sync DESC"
                ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def revoke(self, device_id: str) -> bool:
        """Mark *device_id* as revoked.

        Parameters
        ----------
        device_id:
            The device to revoke.

        Returns
        -------
        bool
            ``True`` if the device was found and revoked; ``False`` if the
            *device_id* does not exist in the registry.
        """
        with self._lock:
            with closing(self._connect()) as conn:
                cursor = conn.execute(
                    "UPDATE devices SET revoked = 1 WHERE device_id = ?",
                    (device_id,),
                )
                conn.commit()
                return cursor.rowcount > 0

    def touch(self, device_id: str) -> bool:
        """Update ``last_sync`` to *now* for *device_id*.

        Parameters
        ----------
        device_id:
            The device whose sync timestamp should be refreshed.

        Returns
        -------
        bool
            ``True`` if the row was found and updated; ``False`` otherwise.
        """
        now = time.time()
        with self._lock:
            with closing(self._connect()) as conn:
                cursor = conn.execute(
                    "UPDATE devices SET last_sync = ? WHERE device_id = ?",
                    (now, device_id),
                )
                conn.commit()
                return cursor.rowcount > 0


__all__ = ["DeviceRecord", "DeviceRegistry"]
