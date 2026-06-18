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
        import contextlib
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


# ---------------------------------------------------------------------------
# Lease lifecycle: issuance/renewal, purge, offline query, revocation matrix
# (E-58 S-190, T-657/T-658/T-659/T-660)
# ---------------------------------------------------------------------------

from depthfusion.cache import (  # noqa: E402
    DEFAULT_LEASE_SECONDS,
    InMemoryLeaseStore,
    Lease,
    LeaseManager,
    LeaseStatus,
    OfflineDocument,
    OfflineQueryEngine,
    PurgeEngine,
    PurgeTrigger,
    RenewalDeniedError,
    ttl_for_classification,
)


class _FakeCacheWiper:
    """Records which cache records were wiped."""

    def __init__(self) -> None:
        self.wiped_records: list[str] = []
        self.wiped_all: int = 0

    def wipe_record(self, record_id: str) -> None:
        self.wiped_records.append(record_id)

    def wipe_all(self) -> None:
        self.wiped_all += 1
        self.wiped_records.append("*ALL*")


class _FakeTokenWiper:
    """Records token wipes."""

    def __init__(self) -> None:
        self.wipes: int = 0

    def wipe_token(self) -> None:
        self.wipes += 1


@pytest.fixture()
def lease_store() -> InMemoryLeaseStore:
    return InMemoryLeaseStore()


@pytest.fixture()
def cache_wiper() -> "_FakeCacheWiper":
    return _FakeCacheWiper()


@pytest.fixture()
def token_wiper() -> "_FakeTokenWiper":
    return _FakeTokenWiper()


class TestClassificationTtl:
    def test_default_is_seven_days(self) -> None:
        assert DEFAULT_LEASE_SECONDS == 7 * 24 * 3600
        assert ttl_for_classification(ClassificationLevel.PUBLIC) == DEFAULT_LEASE_SECONDS
        assert ttl_for_classification(ClassificationLevel.INTERNAL) == DEFAULT_LEASE_SECONDS

    def test_confidential_is_48h(self) -> None:
        assert ttl_for_classification(ClassificationLevel.CONFIDENTIAL) == 48 * 3600

    def test_restricted_is_24h_and_shorter_than_confidential(self) -> None:
        assert ttl_for_classification(ClassificationLevel.RESTRICTED) == 24 * 3600
        assert ttl_for_classification(
            ClassificationLevel.RESTRICTED
        ) < ttl_for_classification(ClassificationLevel.CONFIDENTIAL)


class TestLeaseIssuance:
    def test_issue_scales_ttl_by_classification(
        self, lease_store, cache_wiper, token_wiper
    ) -> None:
        mgr = LeaseManager(lease_store, cache_wiper, token_wiper)
        now = 1_000_000.0
        pub = mgr.issue("rec-pub", ClassificationLevel.PUBLIC, now=now)
        conf = mgr.issue("rec-conf", ClassificationLevel.CONFIDENTIAL, now=now)

        assert pub.expires_at == now + DEFAULT_LEASE_SECONDS
        assert conf.expires_at == now + 48 * 3600
        # Positive: a freshly-issued lease is valid now.
        assert pub.is_valid(now) is True
        assert conf.status(now) is LeaseStatus.VALID

    def test_issued_lease_is_persisted(
        self, lease_store, cache_wiper, token_wiper
    ) -> None:
        mgr = LeaseManager(lease_store, cache_wiper, token_wiper)
        mgr.issue("rec-1", ClassificationLevel.INTERNAL, now=500.0)
        stored = lease_store.get("rec-1")
        assert stored is not None
        assert stored.record_id == "rec-1"

    def test_expired_lease_negative_case(self) -> None:
        lease = Lease(
            record_id="r",
            classification=ClassificationLevel.CONFIDENTIAL,
            issued_at=0.0,
            expires_at=48 * 3600,
        )
        # Just before expiry → valid; after → expired.
        assert lease.is_valid(48 * 3600 - 1) is True
        assert lease.status(48 * 3600 + 1) is LeaseStatus.EXPIRED


class TestLeaseRenewal:
    def test_renew_granted_extends_from_now(
        self, lease_store, cache_wiper, token_wiper
    ) -> None:
        mgr = LeaseManager(lease_store, cache_wiper, token_wiper)
        mgr.issue("rec-1", ClassificationLevel.CONFIDENTIAL, now=1000.0)
        outcome = mgr.renew("rec-1", server_grants=True, now=2000.0)
        assert outcome.renewed is True
        assert outcome.lease is not None
        # Extended from the renewal instant, not the original issue time.
        assert outcome.lease.expires_at == 2000.0 + 48 * 3600
        assert lease_store.get("rec-1").expires_at == 2000.0 + 48 * 3600

    def test_renew_denied_wipes_cache_and_token(
        self, lease_store, cache_wiper, token_wiper
    ) -> None:
        mgr = LeaseManager(lease_store, cache_wiper, token_wiper)
        mgr.issue("rec-1", ClassificationLevel.PUBLIC, now=1000.0)
        with pytest.raises(RenewalDeniedError):
            mgr.renew("rec-1", server_grants=False, now=1500.0)
        # Full wipe: the lease is gone, the cache record was wiped, token wiped.
        assert lease_store.get("rec-1") is None
        assert "rec-1" in cache_wiper.wiped_records
        assert token_wiper.wipes == 1

    def test_renew_unknown_record_denies_and_wipes_token(
        self, lease_store, cache_wiper, token_wiper
    ) -> None:
        mgr = LeaseManager(lease_store, cache_wiper, token_wiper)
        with pytest.raises(RenewalDeniedError):
            mgr.renew("ghost", server_grants=True, now=100.0)
        assert token_wiper.wipes == 1


class TestPurgeEngine:
    def _seed(self, store: InMemoryLeaseStore) -> None:
        # Two leases: one already expired, one valid far in the future.
        store.upsert(
            Lease("expired", ClassificationLevel.PUBLIC, issued_at=0.0, expires_at=100.0)
        )
        store.upsert(
            Lease(
                "valid",
                ClassificationLevel.PUBLIC,
                issued_at=0.0,
                expires_at=10_000_000.0,
            )
        )

    def test_purge_on_start(self, lease_store, cache_wiper, token_wiper) -> None:
        self._seed(lease_store)
        engine = PurgeEngine(lease_store, cache_wiper, token_wiper)
        result = engine.run_on_start(now=200.0)
        assert result.trigger is PurgeTrigger.STARTUP
        # Positive: expired purged; Negative: valid retained.
        assert "expired" in result.purged_record_ids
        assert lease_store.get("expired") is None
        assert lease_store.get("valid") is not None
        assert "expired" in cache_wiper.wiped_records

    def test_purge_on_timer(self, lease_store, cache_wiper, token_wiper) -> None:
        self._seed(lease_store)
        engine = PurgeEngine(lease_store, cache_wiper, token_wiper)
        result = engine.run_on_timer(now=200.0)
        assert result.trigger is PurgeTrigger.TIMER
        assert result.purged_count == 1
        assert lease_store.get("valid") is not None

    def test_purge_on_revoke_full_wipe(
        self, lease_store, cache_wiper, token_wiper
    ) -> None:
        self._seed(lease_store)
        engine = PurgeEngine(lease_store, cache_wiper, token_wiper)
        result = engine.run_on_revoke(now=200.0)
        assert result.trigger is PurgeTrigger.REVOKE
        assert result.full_wipe is True
        # Everything is wiped — even the still-valid lease.
        assert lease_store.all_leases() == []
        assert cache_wiper.wiped_all == 1
        assert token_wiper.wipes == 1

    def test_clock_rollback_cannot_revive_expired_lease(
        self, lease_store, cache_wiper, token_wiper
    ) -> None:
        # Issue a lease that expires at t=100. Advance the engine to t=500
        # (sets the high-water mark), purging it. Then roll the clock back to
        # t=50 — the lease must NOT come back to life.
        lease_store.upsert(
            Lease("r", ClassificationLevel.PUBLIC, issued_at=0.0, expires_at=100.0)
        )
        engine = PurgeEngine(lease_store, cache_wiper, token_wiper)
        engine.run_on_timer(now=500.0)
        assert lease_store.get("r") is None  # purged at t=500

        # Re-add a lease that, by wall clock, would still be valid at the
        # rolled-back time but expired relative to the high-water mark.
        lease_store.upsert(
            Lease("r2", ClassificationLevel.PUBLIC, issued_at=0.0, expires_at=200.0)
        )
        result = engine.run_on_timer(now=50.0)  # clock rolled back
        assert result.clock_tamper_detected is True
        # effective_now is max(50, 500) = 500 → r2 (expires_at=200) is expired.
        assert lease_store.get("r2") is None


class TestOfflineQueryEngine:
    def _docs(self) -> list[OfflineDocument]:
        return [
            OfflineDocument(
                "doc-quarterly",
                "quarterly revenue report finance numbers",
                embedding=(1.0, 0.0, 0.0),
            ),
            OfflineDocument(
                "doc-hr",
                "employee handbook vacation policy hr",
                embedding=(0.0, 1.0, 0.0),
            ),
            OfflineDocument(
                "doc-expired",
                "quarterly revenue secret leaked finance",
                embedding=(1.0, 0.0, 0.0),
            ),
        ]

    def _store_with_leases(self) -> InMemoryLeaseStore:
        store = InMemoryLeaseStore()
        store.upsert(
            Lease(
                "doc-quarterly",
                ClassificationLevel.INTERNAL,
                issued_at=0.0,
                expires_at=10_000.0,
            )
        )
        store.upsert(
            Lease(
                "doc-hr",
                ClassificationLevel.INTERNAL,
                issued_at=0.0,
                expires_at=10_000.0,
            )
        )
        # Expired lease — must NOT be searchable even though it matches.
        store.upsert(
            Lease(
                "doc-expired",
                ClassificationLevel.CONFIDENTIAL,
                issued_at=0.0,
                expires_at=10.0,
            )
        )
        return store

    def test_offline_search_returns_cached_results_with_subset_indicator(self) -> None:
        store = self._store_with_leases()
        engine = OfflineQueryEngine(store, self._docs())
        rs = engine.search(
            "quarterly revenue", query_embedding=(1.0, 0.0, 0.0), now=100.0
        )
        # Positive: the matching valid doc is returned.
        ids = [r.record_id for r in rs.results]
        assert "doc-quarterly" in ids
        # The "offline subset" indicator is always present + truthy.
        assert rs.offline_subset is True
        assert rs.indicator_label
        # Only the two valid-lease docs are in the searchable subset.
        assert rs.total_cached == 2

    def test_expired_lease_record_excluded_negative_case(self) -> None:
        store = self._store_with_leases()
        engine = OfflineQueryEngine(store, self._docs())
        rs = engine.search("quarterly revenue", now=100.0)  # doc-expired lease dead
        ids = [r.record_id for r in rs.results]
        assert "doc-expired" not in ids

    def test_empty_subset_after_revoke_returns_no_results(self) -> None:
        store = InMemoryLeaseStore()  # no leases at all
        engine = OfflineQueryEngine(store, self._docs())
        rs = engine.search("quarterly revenue", now=100.0)
        assert rs.results == []
        assert rs.total_cached == 0
        # Indicator still set so the UI shows the offline state.
        assert rs.offline_subset is True

    def test_vector_score_ranks_semantic_match(self) -> None:
        store = self._store_with_leases()
        engine = OfflineQueryEngine(store, self._docs(), alpha=1.0)  # pure vector
        rs = engine.search(
            "anything", query_embedding=(1.0, 0.0, 0.0), top_k=1, now=100.0
        )
        assert rs.results[0].record_id == "doc-quarterly"


class TestRevocationMatrix:
    """T-660: the three required revocation scenarios, end to end."""

    def test_online_revoke_full_wipe(self) -> None:
        store = InMemoryLeaseStore()
        cache = _FakeCacheWiper()
        token = _FakeTokenWiper()
        mgr = LeaseManager(store, cache, token)
        mgr.issue("a", ClassificationLevel.PUBLIC, now=0.0)
        mgr.issue("b", ClassificationLevel.CONFIDENTIAL, now=0.0)
        # Online: server returns a revoke signal.
        engine = PurgeEngine(store, cache, token)
        result = engine.run_on_revoke(now=100.0)
        assert result.full_wipe is True
        assert store.all_leases() == []  # negative: nothing serves after revoke
        assert token.wipes == 1

    def test_offline_lease_expiry_dies_without_server(self) -> None:
        store = InMemoryLeaseStore()
        cache = _FakeCacheWiper()
        token = _FakeTokenWiper()
        mgr = LeaseManager(store, cache, token)
        # Confidential → 48h lease.
        mgr.issue("c", ClassificationLevel.CONFIDENTIAL, now=0.0)
        engine = PurgeEngine(store, cache, token)
        # Positive: still valid within the window (no server contact needed).
        before = engine.run_on_timer(now=47 * 3600)
        assert "c" not in before.purged_record_ids
        assert store.get("c") is not None
        # Negative: after 48h offline, it dies at the timer with no server.
        after = engine.run_on_timer(now=49 * 3600)
        assert "c" in after.purged_record_ids
        assert store.get("c") is None

    def test_clock_rollback_tamper_does_not_extend_lease(self) -> None:
        store = InMemoryLeaseStore()
        cache = _FakeCacheWiper()
        token = _FakeTokenWiper()
        mgr = LeaseManager(store, cache, token)
        mgr.issue("d", ClassificationLevel.RESTRICTED, now=0.0)  # 24h lease
        engine = PurgeEngine(store, cache, token)
        # Advance past expiry — high-water mark moves to 25h, lease purged.
        engine.run_on_timer(now=25 * 3600)
        assert store.get("d") is None
        # Attacker rolls the clock back to t=1h and re-seeds a lease that, by
        # wall clock, looks valid; the engine evaluates against the high-water
        # mark and still purges it.
        store.upsert(
            Lease("d2", ClassificationLevel.RESTRICTED, issued_at=0.0, expires_at=24 * 3600)
        )
        result = engine.run_on_timer(now=1 * 3600)
        assert result.clock_tamper_detected is True
        assert store.get("d2") is None
