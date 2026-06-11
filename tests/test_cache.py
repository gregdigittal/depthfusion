"""Tests for the Intelligent Offline Cache (E-58).

Covers:
- CacheEntry.compute_ml_score formula
- CacheManager put / get round-trips with Fernet encryption
- Eviction: lowest-scored items are removed when the cache is full
- ensure_space frees the correct amount
- LRU access-count updates on get()
- Encryption round-trip: decrypted data equals original
- Wrong-key scenario produces a cache miss (not a crash)
- delete() and entry_count()
"""

from __future__ import annotations

import math
import time

import pytest
from cryptography.fernet import Fernet

from depthfusion.cache import CacheEntry, CacheManager, EvictionPolicy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def key() -> bytes:
    return Fernet.generate_key()


@pytest.fixture()
def manager(key: bytes) -> CacheManager:
    """In-memory CacheManager with a 1 MB limit for fast eviction testing."""
    return CacheManager(db_path=":memory:", key=key, max_bytes=1024 * 1024)


# ---------------------------------------------------------------------------
# CacheEntry.compute_ml_score
# ---------------------------------------------------------------------------

class TestComputeMlScore:
    def test_zero_accesses_yields_zero_score(self) -> None:
        score = CacheEntry.compute_ml_score(
            access_count=0,
            last_accessed=time.time(),
            size_bytes=100,
        )
        assert score == 0.0

    def test_score_decreases_with_age(self) -> None:
        now = time.time()
        recent = CacheEntry.compute_ml_score(
            access_count=5,
            last_accessed=now - 3600,  # 1 hour ago
            size_bytes=1000,
            now=now,
        )
        old = CacheEntry.compute_ml_score(
            access_count=5,
            last_accessed=now - 30 * 86400,  # 30 days ago
            size_bytes=1000,
            now=now,
        )
        assert recent > old

    def test_score_increases_with_access_count(self) -> None:
        now = time.time()
        low = CacheEntry.compute_ml_score(
            access_count=1, last_accessed=now, size_bytes=500, now=now
        )
        high = CacheEntry.compute_ml_score(
            access_count=10, last_accessed=now, size_bytes=500, now=now
        )
        assert high > low

    def test_score_decreases_with_larger_file(self) -> None:
        now = time.time()
        small = CacheEntry.compute_ml_score(
            access_count=5, last_accessed=now, size_bytes=100, now=now
        )
        large = CacheEntry.compute_ml_score(
            access_count=5, last_accessed=now, size_bytes=10_000_000, now=now
        )
        assert small > large

    def test_score_is_finite_float(self) -> None:
        score = CacheEntry.compute_ml_score(
            access_count=3,
            last_accessed=time.time() - 86400,
            size_bytes=2048,
        )
        assert math.isfinite(score)

    def test_size_penalty_floor_prevents_division_by_zero(self) -> None:
        # size_bytes=0 should not raise
        score = CacheEntry.compute_ml_score(
            access_count=1,
            last_accessed=time.time(),
            size_bytes=0,
        )
        assert math.isfinite(score)
        assert score >= 0.0


# ---------------------------------------------------------------------------
# CacheManager — basic put/get
# ---------------------------------------------------------------------------

class TestCacheManagerPutGet:
    def test_put_returns_entry_with_correct_metadata(self, manager: CacheManager) -> None:
        now = time.time()
        entry = manager.put("a/b/c.txt", "user1", b"hello world", now=now)
        assert entry.path == "a/b/c.txt"
        assert entry.principal_id == "user1"
        assert entry.size_bytes == len(b"hello world")
        assert entry.encrypted is True
        assert entry.data == b"hello world"
        assert entry.access_count == 1

    def test_get_returns_decrypted_data(self, manager: CacheManager) -> None:
        payload = b"secret payload \x00\x01\x02"
        manager.put("file.bin", "alice", payload)
        entry = manager.get("file.bin", "alice")
        assert entry is not None
        assert entry.data == payload

    def test_get_miss_returns_none(self, manager: CacheManager) -> None:
        result = manager.get("nonexistent.txt", "bob")
        assert result is None

    def test_get_increments_access_count(self, manager: CacheManager) -> None:
        manager.put("doc.md", "u1", b"text")
        e1 = manager.get("doc.md", "u1")
        assert e1 is not None
        e2 = manager.get("doc.md", "u1")
        assert e2 is not None
        assert e2.access_count == e1.access_count + 1

    def test_get_updates_ml_score(self, manager: CacheManager) -> None:
        manager.put("report.pdf", "cfo", b"Q4 results")
        e1 = manager.get("report.pdf", "cfo")
        assert e1 is not None
        e2 = manager.get("report.pdf", "cfo")
        assert e2 is not None
        # More accesses → higher score (recency held constant within test)
        assert e2.ml_score >= e1.ml_score

    def test_put_overwrites_existing_entry(self, manager: CacheManager) -> None:
        manager.put("key.txt", "u1", b"v1")
        manager.put("key.txt", "u1", b"v2-updated")
        entry = manager.get("key.txt", "u1")
        assert entry is not None
        assert entry.data == b"v2-updated"

    def test_principal_isolation(self, manager: CacheManager) -> None:
        manager.put("shared.txt", "alice", b"alice data")
        manager.put("shared.txt", "bob", b"bob data")
        alice = manager.get("shared.txt", "alice")
        bob = manager.get("shared.txt", "bob")
        assert alice is not None and alice.data == b"alice data"
        assert bob is not None and bob.data == b"bob data"


# ---------------------------------------------------------------------------
# Encryption round-trip
# ---------------------------------------------------------------------------

class TestEncryption:
    def test_round_trip_preserves_binary_data(self, key: bytes) -> None:
        mgr = CacheManager(db_path=":memory:", key=key)
        original = bytes(range(256)) * 4
        mgr.put("bin.dat", "u", original)
        entry = mgr.get("bin.dat", "u")
        assert entry is not None
        assert entry.data == original

    def test_wrong_key_returns_none(self) -> None:
        key_a = Fernet.generate_key()
        key_b = Fernet.generate_key()
        mgr_a = CacheManager(db_path=":memory:", key=key_a)
        mgr_b = CacheManager(db_path=":memory:", key=key_b)

        mgr_a.put("secret.txt", "u", b"top secret")

        # Write a*'s encrypted blob into b's database directly
        import sqlite3, contextlib
        with contextlib.closing(mgr_a._conn.cursor()) as cur:
            row = cur.execute(
                "SELECT payload FROM cache_entries WHERE path='secret.txt'"
            ).fetchone()
        blob = row[0]

        # Insert the (mis-keyed) blob into mgr_b
        with contextlib.closing(mgr_b._conn.cursor()) as cur:
            cur.execute(
                "INSERT INTO cache_entries "
                "(path, principal_id, last_accessed, access_count, size_bytes, ml_score, encrypted, payload) "
                "VALUES ('secret.txt', 'u', ?, 1, 10, 0.5, 1, ?)",
                (time.time(), blob),
            )
            mgr_b._conn.commit()

        # mgr_b cannot decrypt → returns None (no crash)
        result = mgr_b.get("secret.txt", "u")
        assert result is None

    def test_encrypted_flag_is_true_in_db(self, manager: CacheManager) -> None:
        manager.put("x.txt", "u", b"data")
        import contextlib
        with contextlib.closing(manager._conn.cursor()) as cur:
            row = cur.execute(
                "SELECT encrypted FROM cache_entries WHERE path='x.txt'"
            ).fetchone()
        assert row[0] == 1


# ---------------------------------------------------------------------------
# Eviction logic
# ---------------------------------------------------------------------------

class TestEviction:
    def test_evict_lowest_scored_removes_correct_entry(
        self, manager: CacheManager
    ) -> None:
        """An entry with access_count=1 (low score) is evicted before one
        with access_count=100 (high score)."""
        now = time.time()
        manager.put("high.txt", "u", b"x" * 100, now=now)
        # Simulate many accesses for high.txt
        for _ in range(99):
            manager.get("high.txt", "u", now=now)

        manager.put("low.txt", "u", b"y" * 100, now=now)
        # low.txt has access_count=1 → lower ml_score

        evicted = manager.evict_lowest_scored(1)
        assert len(evicted) == 1
        assert evicted[0] == ("low.txt", "u")
        assert manager.get("high.txt", "u") is not None
        assert manager.get("low.txt", "u") is None

    def test_evict_n_removes_n_entries(self, manager: CacheManager) -> None:
        now = time.time()
        for i in range(5):
            manager.put(f"f{i}.txt", "u", b"a" * 10, now=now)
        evicted = manager.evict_lowest_scored(3)
        assert len(evicted) == 3
        assert manager.entry_count() == 2

    def test_cache_respects_max_bytes(self) -> None:
        """Putting data that exceeds the limit triggers eviction automatically."""
        small_limit = 200  # 200 bytes
        key = Fernet.generate_key()
        mgr = CacheManager(db_path=":memory:", key=key, max_bytes=small_limit)

        now = time.time()
        # Each entry is 80 bytes; after two entries we have 160 bytes used.
        mgr.put("a.txt", "u", b"A" * 80, now=now)
        mgr.put("b.txt", "u", b"B" * 80, now=now)
        assert mgr.total_size_bytes() == 160

        # Adding a third entry (80 bytes) would push to 240 > 200; eviction
        # must remove the lowest-scored entry first.
        mgr.put("c.txt", "u", b"C" * 80, now=now)
        assert mgr.total_size_bytes() <= small_limit

    def test_ensure_space_frees_enough(self) -> None:
        key = Fernet.generate_key()
        mgr = CacheManager(db_path=":memory:", key=key, max_bytes=500)
        now = time.time()
        for i in range(5):
            mgr.put(f"f{i}.txt", "u", b"x" * 80, now=now)

        # Force-free 300 bytes
        evicted = mgr.ensure_space(300)
        assert len(evicted) >= 1
        assert mgr._max_bytes - mgr.total_size_bytes() >= 300

    def test_evict_returns_empty_when_no_entries(
        self, manager: CacheManager
    ) -> None:
        result = manager.evict_lowest_scored(5)
        assert result == []


# ---------------------------------------------------------------------------
# Delete & entry_count
# ---------------------------------------------------------------------------

class TestDeleteAndCount:
    def test_delete_existing_entry(self, manager: CacheManager) -> None:
        manager.put("del.txt", "u", b"bye")
        assert manager.delete("del.txt", "u") is True
        assert manager.get("del.txt", "u") is None

    def test_delete_nonexistent_returns_false(self, manager: CacheManager) -> None:
        assert manager.delete("ghost.txt", "u") is False

    def test_entry_count_tracks_puts_and_deletes(
        self, manager: CacheManager
    ) -> None:
        assert manager.entry_count() == 0
        manager.put("p1.txt", "u", b"a")
        manager.put("p2.txt", "u", b"b")
        assert manager.entry_count() == 2
        manager.delete("p1.txt", "u")
        assert manager.entry_count() == 1

    def test_total_size_starts_at_zero(self, manager: CacheManager) -> None:
        assert manager.total_size_bytes() == 0

    def test_total_size_reflects_actual_payload_size(
        self, manager: CacheManager
    ) -> None:
        manager.put("s1.txt", "u", b"x" * 100)
        manager.put("s2.txt", "u", b"y" * 200)
        assert manager.total_size_bytes() == 300
