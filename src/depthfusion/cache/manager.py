"""CacheManager: LRU + ML-scored SQLite-backed offline cache (E-58).

Design decisions
----------------
* SQLite is used as the backing store — simple, file-based, no server.
* All payloads are encrypted with Fernet (symmetric AES-128-CBC + HMAC-SHA256).
* The encryption key is passed in at construction time; key storage
  (OS keychain, DPAPI, etc.) is the caller's responsibility.
* Eviction runs synchronously on ``put()`` and ``ensure_space()``; the
  cache does not start background threads.
* ``contextlib.closing()`` is used everywhere so cursors / connections
  are never leaked even on exception paths.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from depthfusion.cache.models import CacheEntry, EvictionPolicy

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES: int = 10 * 1024 ** 3  # 10 GB
_SCHEMA_VERSION: int = 1


class CacheManager:
    """LRU cache backed by SQLite with Fernet encryption.

    Parameters
    ----------
    db_path:
        Filesystem path for the SQLite database file.  Pass ``":memory:"``
        for an in-process store (useful in tests).
    key:
        A URL-safe base64-encoded 32-byte Fernet key
        (``Fernet.generate_key()``).  Must be kept secret.
    max_bytes:
        Maximum total unencrypted payload size.  Defaults to 10 GB.
    eviction_policy:
        Algorithm used to rank entries for eviction.
        ``EvictionPolicy.ML_SCORE`` (default) evicts the lowest-scoring
        items first; ``EvictionPolicy.LRU`` evicts by ``last_accessed``.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        key: Optional[bytes] = None,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        eviction_policy: EvictionPolicy = EvictionPolicy.ML_SCORE,
    ) -> None:
        self._db_path = str(db_path)
        if key is None:
            # F-006: an ephemeral key means all cached data is lost on process
            # restart. Production deployments must set DEPTHFUSION_CACHE_KEY
            # (a URL-safe base64-encoded 32-byte Fernet key) and pass it here.
            logger.warning(
                "CacheManager: no encryption key supplied; using an ephemeral "
                "Fernet key. All cached data will be unrecoverable after process "
                "restart. Set DEPTHFUSION_CACHE_KEY and pass the key at "
                "construction time to persist the cache across restarts."
            )
            _key = Fernet.generate_key()
        else:
            _key = key
        self._fernet = Fernet(_key)
        self._max_bytes = max_bytes
        self._eviction_policy = eviction_policy

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._bootstrap()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _bootstrap(self) -> None:
        with contextlib.closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    path        TEXT    NOT NULL,
                    principal_id TEXT   NOT NULL,
                    last_accessed REAL  NOT NULL,
                    access_count  INTEGER NOT NULL DEFAULT 0,
                    size_bytes    INTEGER NOT NULL DEFAULT 0,
                    ml_score      REAL    NOT NULL DEFAULT 0.0,
                    encrypted     INTEGER NOT NULL DEFAULT 1,
                    payload       BLOB,
                    PRIMARY KEY (path, principal_id)
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ce_score "
                "ON cache_entries (ml_score ASC)"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cache_meta "
                "(key TEXT PRIMARY KEY, value TEXT)"
            )
            cur.execute(
                "INSERT OR IGNORE INTO cache_meta VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def put(
        self,
        path: str,
        principal_id: str,
        data: bytes,
        now: Optional[float] = None,
    ) -> CacheEntry:
        """Store *data* in the cache, encrypting it with Fernet.

        If the cache would exceed ``max_bytes`` after insertion, the
        lowest-scored entries are evicted until enough space is freed.

        Returns the ``CacheEntry`` that was stored.
        """
        now = now if now is not None else time.time()
        size = len(data)

        # Evict before inserting so we don't temporarily exceed the limit
        self._make_room(size, exclude_path=path, exclude_principal=principal_id)

        encrypted_payload = self._fernet.encrypt(data)

        # Upsert: if the entry exists, update it; otherwise insert
        with contextlib.closing(self._conn.cursor()) as cur:
            existing = cur.execute(
                "SELECT access_count FROM cache_entries "
                "WHERE path=? AND principal_id=?",
                (path, principal_id),
            ).fetchone()

            access_count = (existing["access_count"] + 1) if existing else 1

            ml_score = CacheEntry.compute_ml_score(
                access_count=access_count,
                last_accessed=now,
                size_bytes=size,
                now=now,
            )

            cur.execute(
                """
                INSERT INTO cache_entries
                    (path, principal_id, last_accessed, access_count,
                     size_bytes, ml_score, encrypted, payload)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(path, principal_id) DO UPDATE SET
                    last_accessed = excluded.last_accessed,
                    access_count  = excluded.access_count,
                    size_bytes    = excluded.size_bytes,
                    ml_score      = excluded.ml_score,
                    payload       = excluded.payload
                """,
                (path, principal_id, now, access_count, size, ml_score,
                 encrypted_payload),
            )
            self._conn.commit()

        return CacheEntry(
            path=path,
            principal_id=principal_id,
            last_accessed=now,
            access_count=access_count,
            size_bytes=size,
            ml_score=ml_score,
            encrypted=True,
            data=data,
        )

    def get(
        self,
        path: str,
        principal_id: str,
        now: Optional[float] = None,
    ) -> Optional[CacheEntry]:
        """Retrieve and decrypt a cached entry.

        Returns ``None`` on cache miss or decryption failure.
        Updates ``last_accessed`` and ``access_count`` on hit.
        """
        now = now if now is not None else time.time()

        with contextlib.closing(self._conn.cursor()) as cur:
            row = cur.execute(
                "SELECT * FROM cache_entries WHERE path=? AND principal_id=?",
                (path, principal_id),
            ).fetchone()

        if row is None:
            return None

        try:
            plaintext = self._fernet.decrypt(row["payload"])
        except (InvalidToken, Exception) as exc:
            logger.warning("Cache decryption failed for %s/%s: %s", path, principal_id, exc)
            return None

        new_access_count = row["access_count"] + 1
        new_ml_score = CacheEntry.compute_ml_score(
            access_count=new_access_count,
            last_accessed=now,
            size_bytes=row["size_bytes"],
            now=now,
        )

        with contextlib.closing(self._conn.cursor()) as cur:
            cur.execute(
                "UPDATE cache_entries SET last_accessed=?, access_count=?, ml_score=? "
                "WHERE path=? AND principal_id=?",
                (now, new_access_count, new_ml_score, path, principal_id),
            )
            self._conn.commit()

        return CacheEntry(
            path=path,
            principal_id=principal_id,
            last_accessed=now,
            access_count=new_access_count,
            size_bytes=row["size_bytes"],
            ml_score=new_ml_score,
            encrypted=bool(row["encrypted"]),
            data=plaintext,
        )

    def delete(self, path: str, principal_id: str) -> bool:
        """Remove a single entry.  Returns ``True`` if deleted, ``False`` if not found."""
        with contextlib.closing(self._conn.cursor()) as cur:
            cur.execute(
                "DELETE FROM cache_entries WHERE path=? AND principal_id=?",
                (path, principal_id),
            )
            deleted = cur.rowcount > 0
            self._conn.commit()
        return deleted

    def total_size_bytes(self) -> int:
        """Return the sum of all stored payload sizes (unencrypted)."""
        with contextlib.closing(self._conn.cursor()) as cur:
            row = cur.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM cache_entries"
            ).fetchone()
        return int(row["total"])

    def entry_count(self) -> int:
        """Return the number of entries currently in the cache."""
        with contextlib.closing(self._conn.cursor()) as cur:
            row = cur.execute(
                "SELECT COUNT(*) AS cnt FROM cache_entries"
            ).fetchone()
        return int(row["cnt"])

    def evict_lowest_scored(self, n: int = 1) -> list[tuple[str, str]]:
        """Evict the *n* lowest-scored entries.

        Returns a list of ``(path, principal_id)`` tuples that were removed.
        """
        if n <= 0:
            return []

        with contextlib.closing(self._conn.cursor()) as cur:
            rows = cur.execute(
                "SELECT path, principal_id FROM cache_entries "
                "ORDER BY ml_score ASC LIMIT ?",
                (n,),
            ).fetchall()

            evicted: list[tuple[str, str]] = []
            for row in rows:
                cur.execute(
                    "DELETE FROM cache_entries WHERE path=? AND principal_id=?",
                    (row["path"], row["principal_id"]),
                )
                evicted.append((row["path"], row["principal_id"]))

            self._conn.commit()

        if evicted:
            logger.debug("Evicted %d cache entries: %s", len(evicted), evicted)

        return evicted

    def ensure_space(self, needed_bytes: int) -> list[tuple[str, str]]:
        """Evict entries until at least *needed_bytes* of space is available.

        Returns the list of ``(path, principal_id)`` pairs that were removed.
        """
        evicted: list[tuple[str, str]] = []
        while True:
            available = self._max_bytes - self.total_size_bytes()
            if available >= needed_bytes:
                break
            # Evict in batches of 10 for efficiency
            batch = self.evict_lowest_scored(10)
            if not batch:
                # Nothing left to evict; accept the overflow
                logger.warning(
                    "Cache overflow: could not free %d bytes (all entries evicted)",
                    needed_bytes,
                )
                break
            evicted.extend(batch)
        return evicted

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_room(
        self,
        needed_bytes: int,
        exclude_path: str,
        exclude_principal: str,
    ) -> None:
        """Evict until the cache can hold *needed_bytes* more.

        Entries matching ``(exclude_path, exclude_principal)`` are not
        counted toward the current usage (they are being replaced).
        """
        with contextlib.closing(self._conn.cursor()) as cur:
            row = cur.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM cache_entries "
                "WHERE NOT (path=? AND principal_id=?)",
                (exclude_path, exclude_principal),
            ).fetchone()
        current = int(row["total"])

        if current + needed_bytes <= self._max_bytes:
            return

        # Evict lowest-scored entries until there is room
        while current + needed_bytes > self._max_bytes:
            with contextlib.closing(self._conn.cursor()) as cur:
                row = cur.execute(
                    "SELECT path, principal_id, size_bytes FROM cache_entries "
                    "WHERE NOT (path=? AND principal_id=?) "
                    "ORDER BY ml_score ASC LIMIT 1",
                    (exclude_path, exclude_principal),
                ).fetchone()
            if row is None:
                break
            self.delete(row["path"], row["principal_id"])
            current -= row["size_bytes"]
            logger.debug(
                "Evicted %s/%s (%d bytes) to make room",
                row["path"],
                row["principal_id"],
                row["size_bytes"],
            )
