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

from depthfusion.authz.classification import ClassificationLevel
from depthfusion.cache import (
    CACHE_SCHEMA,
    CACHE_SCHEMA_VERSION,
    CacheableRecord,
    CacheEntry,
    CacheManager,
    EvictionPolicy,
    LeaseRow,
    TamperResult,
    compute_integrity_hmac,
    filter_admissible,
    is_admissible,
    verify_on_open,
)


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


# ---------------------------------------------------------------------------
# Admission filter — ACL membership + classification ceiling (T-650, S-188 AC-4)
# ---------------------------------------------------------------------------

class TestAdmissionFilter:
    def _record(
        self,
        level: ClassificationLevel,
        acl: list[str],
        rid: str = "rec-1",
    ) -> CacheableRecord:
        return CacheableRecord.of(rid, level, acl)

    def test_admits_when_in_acl_and_within_ceiling(self) -> None:
        rec = self._record(ClassificationLevel.INTERNAL, ["alice", "bob"])
        decision = is_admissible(rec, "alice", ClassificationLevel.CONFIDENTIAL)
        assert decision.admitted is True
        assert decision.reason == "admitted"

    def test_rejects_when_principal_not_in_acl(self) -> None:
        rec = self._record(ClassificationLevel.PUBLIC, ["alice"])
        decision = is_admissible(rec, "mallory", ClassificationLevel.RESTRICTED)
        assert decision.admitted is False
        assert decision.acl_denied is True

    def test_rejects_when_classification_exceeds_ceiling(self) -> None:
        # In ACL, but record is RESTRICTED and ceiling is only INTERNAL.
        rec = self._record(ClassificationLevel.RESTRICTED, ["alice"])
        decision = is_admissible(rec, "alice", ClassificationLevel.INTERNAL)
        assert decision.admitted is False
        assert decision.ceiling_exceeded is True

    def test_ceiling_is_inclusive(self) -> None:
        # classification == ceiling is admitted (inclusive boundary).
        rec = self._record(ClassificationLevel.CONFIDENTIAL, ["alice"])
        decision = is_admissible(rec, "alice", ClassificationLevel.CONFIDENTIAL)
        assert decision.admitted is True

    def test_acl_checked_before_ceiling(self) -> None:
        # Out-of-ACL principal on an over-ceiling record → ACL_DENIED, never
        # leaks that the ceiling would also have failed.
        rec = self._record(ClassificationLevel.RESTRICTED, ["alice"])
        decision = is_admissible(rec, "mallory", ClassificationLevel.PUBLIC)
        assert decision.acl_denied is True
        assert decision.ceiling_exceeded is False

    def test_filter_admissible_is_order_preserving_subset(self) -> None:
        recs = [
            CacheableRecord.of("r1", ClassificationLevel.PUBLIC, ["alice"]),
            CacheableRecord.of("r2", ClassificationLevel.RESTRICTED, ["alice"]),
            CacheableRecord.of("r3", ClassificationLevel.INTERNAL, ["bob"]),
            CacheableRecord.of("r4", ClassificationLevel.INTERNAL, ["alice"]),
        ]
        admitted = filter_admissible(
            recs, "alice", ClassificationLevel.CONFIDENTIAL
        )
        # r1 (public, in acl) and r4 (internal, in acl) pass.
        # r2 over ceiling; r3 not in acl.
        assert [r.record_id for r in admitted] == ["r1", "r4"]

    def test_filter_empty_input(self) -> None:
        assert filter_admissible([], "alice", ClassificationLevel.PUBLIC) == []


# ---------------------------------------------------------------------------
# Tamper detection — HMAC over schema + lease table (T-651, S-188 AC-3)
# ---------------------------------------------------------------------------

class TestTamperDetection:
    def _leases(self) -> list[LeaseRow]:
        return [
            LeaseRow("rec-a", 1000, 1000 + 7 * 86400, ClassificationLevel.INTERNAL),
            LeaseRow("rec-b", 2000, 2000 + 48 * 3600, ClassificationLevel.CONFIDENTIAL),
        ]

    def test_matching_digest_returns_ok(self) -> None:
        key = b"k" * 32
        leases = self._leases()
        digest = compute_integrity_hmac(key, leases)
        assert verify_on_open(key, digest, leases) is TamperResult.OK

    def test_missing_digest_triggers_wipe_resync(self) -> None:
        key = b"k" * 32
        leases = self._leases()
        assert verify_on_open(key, None, leases) is TamperResult.WIPE_AND_RESYNC
        assert verify_on_open(key, "", leases) is TamperResult.WIPE_AND_RESYNC

    def test_tampered_lease_expiry_triggers_wipe_resync(self) -> None:
        key = b"k" * 32
        leases = self._leases()
        digest = compute_integrity_hmac(key, leases)
        # Attacker extends rec-a's lease expiry on disk.
        tampered = [
            LeaseRow("rec-a", 1000, 1000 + 365 * 86400, ClassificationLevel.INTERNAL),
            leases[1],
        ]
        assert (
            verify_on_open(key, digest, tampered)
            is TamperResult.WIPE_AND_RESYNC
        )

    def test_tampered_schema_triggers_wipe_resync(self) -> None:
        key = b"k" * 32
        leases = self._leases()
        digest = compute_integrity_hmac(key, leases)
        evil_schema = CACHE_SCHEMA + "\nDROP TABLE cache_lease;"
        assert (
            verify_on_open(key, digest, leases, schema=evil_schema)
            is TamperResult.WIPE_AND_RESYNC
        )

    def test_wrong_key_triggers_wipe_resync(self) -> None:
        leases = self._leases()
        digest = compute_integrity_hmac(b"k" * 32, leases)
        assert (
            verify_on_open(b"x" * 32, digest, leases)
            is TamperResult.WIPE_AND_RESYNC
        )

    def test_digest_independent_of_lease_row_order(self) -> None:
        key = b"k" * 32
        leases = self._leases()
        reordered = list(reversed(leases))
        assert compute_integrity_hmac(key, leases) == compute_integrity_hmac(
            key, reordered
        )

    def test_schema_version_bump_changes_digest(self) -> None:
        key = b"k" * 32
        leases = self._leases()
        d1 = compute_integrity_hmac(key, leases, schema_version=CACHE_SCHEMA_VERSION)
        d2 = compute_integrity_hmac(
            key, leases, schema_version=CACHE_SCHEMA_VERSION + 1
        )
        assert d1 != d2

    def test_schema_mirrors_record_chunk_embedding_with_acl_lease_columns(
        self,
    ) -> None:
        # S-188 AC-2: schema must mirror record + chunk + embedding subset with
        # ACL + classification + lease columns.
        assert "cached_record" in CACHE_SCHEMA
        assert "cached_chunk" in CACHE_SCHEMA
        assert "cached_embedding" in CACHE_SCHEMA
        assert "cache_lease" in CACHE_SCHEMA
        assert "acl_allow" in CACHE_SCHEMA
        assert "classification" in CACHE_SCHEMA
        assert "lease_expires_at" in CACHE_SCHEMA
