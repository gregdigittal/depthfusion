"""Tests for DepthFusion V2 Sync Engine — E-52 / S-167.

Covers:
- T-583: change-log table + cursor API
- T-584: pull endpoint ACL trim + pagination
- T-585: push endpoint validation + ACL enforcement
- T-586: client sync engine behaviour (scheduler/retry equivalent — unit coverage)
- T-587: loss-of-access propagation

Key assertions:
- Client cannot pull records it is not in acl_allow for
- Server rejects pushes where principal is not in acl_allow
- Server rejects pushes that attempt to widen acl_allow
- Server rejects pushes that loosen classification
- Cursor-based pagination returns only new deltas
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from depthfusion.sync.engine import Record, SyncEngine, SyncResult, _classification_rank, _server_wins_on_acl

# ---------------------------------------------------------------------------
# Minimal Principal stub (no OIDC / token stack)
# ---------------------------------------------------------------------------


@dataclass
class _Principal:
    principal_id: str
    upn: str = ""
    display_name: str = ""
    groups: list[str] = field(default_factory=list)
    device_id: str | None = None
    access_token: str | None = None
    id_token: str | None = None
    expires_at: float | None = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine() -> SyncEngine:
    """In-memory SyncEngine — isolated per test."""
    return SyncEngine(db_path=":memory:")


@pytest.fixture()
def alice() -> _Principal:
    return _Principal(principal_id="alice", groups=["member"])


@pytest.fixture()
def bob() -> _Principal:
    return _Principal(principal_id="bob", groups=["member"])


@pytest.fixture()
def admin() -> _Principal:
    return _Principal(principal_id="admin-user", groups=["admin"])


# ---------------------------------------------------------------------------
# Helper: build a record visible to a specific principal
# ---------------------------------------------------------------------------


def _record(
    principal_id: str,
    acl_allow: list[str] | None = None,
    classification: str = "internal",
    payload: dict | None = None,
    record_id: str | None = None,
) -> Record:
    import uuid

    return Record(
        record_id=record_id or str(uuid.uuid4()),
        principal_id=principal_id,
        acl_allow=acl_allow if acl_allow is not None else [principal_id],
        classification=classification,
        payload=payload or {"data": "test"},
    )


# ===========================================================================
# T-583: change-log table + cursor
# ===========================================================================


class TestCursorModel:
    def test_initial_token_is_zero(self, engine: SyncEngine) -> None:
        assert engine.latest_token() == 0

    def test_token_increments_after_push(self, engine: SyncEngine, alice: _Principal) -> None:
        r = _record("alice", acl_allow=["alice"])
        engine.sync_push(alice, [r])
        assert engine.latest_token() == 1

    def test_token_increments_per_record(self, engine: SyncEngine, alice: _Principal) -> None:
        records = [_record("alice", acl_allow=["alice"]) for _ in range(5)]
        engine.sync_push(alice, records)
        assert engine.latest_token() == 5

    def test_datetime_for_token_zero_returns_min(self, engine: SyncEngine) -> None:
        dt = engine.datetime_for_token(0)
        assert dt == datetime.min

    def test_datetime_for_token_nonexistent_returns_min(self, engine: SyncEngine) -> None:
        dt = engine.datetime_for_token(9999)
        assert dt == datetime.min

    def test_datetime_for_token_returns_valid_datetime(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        r = _record("alice", acl_allow=["alice"])
        engine.sync_push(alice, [r])
        token = engine.latest_token()
        dt = engine.datetime_for_token(token)
        assert isinstance(dt, datetime)
        assert dt != datetime.min


# ===========================================================================
# T-585: push endpoint — ACL validation
# ===========================================================================


class TestSyncPush:
    def test_push_valid_record_accepted(self, engine: SyncEngine, alice: _Principal) -> None:
        r = _record("alice", acl_allow=["alice"])
        result = engine.sync_push(alice, [r])
        assert r.record_id in result.accepted
        assert not result.rejected

    def test_push_stamps_owner(self, engine: SyncEngine, alice: _Principal) -> None:
        """principal_id is always set to the pushing principal, not whatever the client claimed."""
        r = _record("impersonated-user", acl_allow=["alice"])
        engine.sync_push(alice, [r])
        # Verify record stored with alice's principal_id
        pulled = engine.sync_pull(alice, since=datetime.min)
        assert any(rec.principal_id == "alice" for rec in pulled if rec.record_id == r.record_id)

    def test_push_rejected_when_principal_not_in_acl(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        """A record where alice is NOT in acl_allow must be rejected."""
        r = _record("alice", acl_allow=["bob"])  # alice absent from acl_allow
        result = engine.sync_push(alice, [r])
        assert r.record_id in result.rejected
        assert r.record_id not in result.accepted
        assert "acl" in result.rejected[r.record_id].lower() or "not listed" in result.rejected[r.record_id].lower()

    def test_push_rejects_unknown_classification(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        import uuid

        # Record.__post_init__ validates classification at construction time.
        with pytest.raises(ValueError, match="classification"):
            Record(
                record_id=str(uuid.uuid4()),
                principal_id="alice",
                acl_allow=["alice"],
                classification="top-secret",  # invalid
                payload={},
            )

    def test_push_rejects_acl_widening_attempt(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        """Push that adds a new principal to acl_allow must be rejected."""
        # First push: alice creates the record with acl_allow=[alice]
        r = _record("alice", acl_allow=["alice"])
        result = engine.sync_push(alice, [r])
        assert r.record_id in result.accepted

        # Second push: alice tries to add bob to acl_allow
        r_widened = Record(
            record_id=r.record_id,
            principal_id="alice",
            acl_allow=["alice", "bob"],  # widened — bob added
            classification="internal",
            payload={"data": "updated"},
        )
        result2 = engine.sync_push(alice, [r_widened])
        assert r.record_id in result2.rejected
        assert "widen" in result2.rejected[r.record_id].lower() or "authoritative" in result2.rejected[r.record_id].lower()

    def test_push_rejects_classification_loosening(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        """Client cannot push a less-restrictive classification than the server has."""
        # Server record has classification=confidential
        r = _record("alice", acl_allow=["alice"], classification="confidential")
        result = engine.sync_push(alice, [r])
        assert r.record_id in result.accepted

        # Client tries to loosen to internal
        r_loose = Record(
            record_id=r.record_id,
            principal_id="alice",
            acl_allow=["alice"],
            classification="internal",  # less restrictive
            payload={"data": "updated"},
        )
        result2 = engine.sync_push(alice, [r_loose])
        assert r.record_id in result2.rejected

    def test_push_allows_payload_update_within_acl(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        """Payload updates (same ACL) are accepted — last-writer-wins for payload."""
        r = _record("alice", acl_allow=["alice"], classification="internal")
        engine.sync_push(alice, [r])

        r_updated = Record(
            record_id=r.record_id,
            principal_id="alice",
            acl_allow=["alice"],
            classification="internal",
            payload={"data": "new-value"},
        )
        result = engine.sync_push(alice, [r_updated])
        assert r.record_id in result.accepted

        pulled = engine.sync_pull(alice, since=datetime.min)
        matching = [rec for rec in pulled if rec.record_id == r.record_id]
        assert matching[0].payload["data"] == "new-value"

    def test_push_partial_batch(self, engine: SyncEngine, alice: _Principal) -> None:
        """Some records in a batch can be accepted while others are rejected."""
        good = _record("alice", acl_allow=["alice"])
        bad = _record("alice", acl_allow=["bob"])  # alice not in ACL

        result = engine.sync_push(alice, [good, bad])
        assert good.record_id in result.accepted
        assert bad.record_id in result.rejected

    def test_push_multiple_principals_in_acl(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        """Record with acl_allow=[alice, bob] — alice can push it."""
        r = _record("alice", acl_allow=["alice", "bob"])
        result = engine.sync_push(alice, [r])
        assert r.record_id in result.accepted


# ===========================================================================
# T-584: pull endpoint — ACL trim
# ===========================================================================


class TestSyncPull:
    def test_pull_returns_own_records(self, engine: SyncEngine, alice: _Principal) -> None:
        r = _record("alice", acl_allow=["alice"])
        engine.sync_push(alice, [r])
        pulled = engine.sync_pull(alice, since=datetime.min)
        assert any(rec.record_id == r.record_id for rec in pulled)

    def test_pull_excludes_unauthorized_records(
        self, engine: SyncEngine, alice: _Principal, bob: _Principal
    ) -> None:
        """bob's records must not appear in alice's pull."""
        r_bob = _record("bob", acl_allow=["bob"])
        engine.sync_push(bob, [r_bob])

        pulled_alice = engine.sync_pull(alice, since=datetime.min)
        assert not any(rec.record_id == r_bob.record_id for rec in pulled_alice)

    def test_pull_shared_record_visible_to_both(
        self, engine: SyncEngine, alice: _Principal, bob: _Principal
    ) -> None:
        """A record with acl_allow=[alice, bob] appears in both principals' pull."""
        r = _record("alice", acl_allow=["alice", "bob"])
        engine.sync_push(alice, [r])

        pulled_alice = engine.sync_pull(alice, since=datetime.min)
        pulled_bob = engine.sync_pull(bob, since=datetime.min)

        assert any(rec.record_id == r.record_id for rec in pulled_alice)
        assert any(rec.record_id == r.record_id for rec in pulled_bob)

    def test_pull_delta_since_cursor(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        """Pull with since=token should return only records created after that token."""
        r1 = _record("alice", acl_allow=["alice"])
        engine.sync_push(alice, [r1])
        token_after_r1 = engine.latest_token()

        r2 = _record("alice", acl_allow=["alice"])
        engine.sync_push(alice, [r2])

        since_dt = engine.datetime_for_token(token_after_r1)
        pulled = engine.sync_pull(alice, since=since_dt)

        pulled_ids = {rec.record_id for rec in pulled}
        assert r2.record_id in pulled_ids
        assert r1.record_id not in pulled_ids

    def test_pull_empty_when_no_new_records(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        r = _record("alice", acl_allow=["alice"])
        engine.sync_push(alice, [r])

        # Grab current high-water mark
        token = engine.latest_token()
        since_dt = engine.datetime_for_token(token)

        # No new records — delta should be empty
        pulled = engine.sync_pull(alice, since=since_dt)
        assert pulled == []

    def test_pull_respects_limit(self, engine: SyncEngine, alice: _Principal) -> None:
        records = [_record("alice", acl_allow=["alice"]) for _ in range(10)]
        engine.sync_push(alice, records)

        pulled = engine.sync_pull(alice, since=datetime.min, limit=3)
        assert len(pulled) <= 3

    def test_pull_does_not_return_records_for_revoked_principal(
        self, engine: SyncEngine, alice: _Principal, bob: _Principal
    ) -> None:
        """T-587: Loss-of-access propagation.

        If a record is later updated by the server to remove alice from
        acl_allow, alice must not see it on the next pull.
        """
        # alice and bob share a record initially
        r = _record("bob", acl_allow=["alice", "bob"])
        engine.sync_push(bob, [r])

        # Verify alice sees it
        pulled = engine.sync_pull(alice, since=datetime.min)
        assert any(rec.record_id == r.record_id for rec in pulled)

        # Token after initial state
        token_initial = engine.latest_token()

        # Server removes alice from acl_allow (bob updates — bob is the owner)
        r_updated = Record(
            record_id=r.record_id,
            principal_id="bob",
            acl_allow=["bob"],  # alice removed
            classification="internal",
            payload={"data": "alice removed"},
        )
        engine.sync_push(bob, [r_updated])

        # alice pulls delta since token_initial — should NOT see the updated record
        since_dt = engine.datetime_for_token(token_initial)
        pulled_delta = engine.sync_pull(alice, since=since_dt)
        assert not any(rec.record_id == r.record_id for rec in pulled_delta)

        # bob should see the updated record in the delta
        pulled_bob = engine.sync_pull(bob, since=since_dt)
        assert any(rec.record_id == r.record_id for rec in pulled_bob)


# ===========================================================================
# Conflict resolution helpers
# ===========================================================================


class TestAclWideningHelper:
    def test_same_acl_and_classification_allowed(self) -> None:
        server = _record("alice", acl_allow=["alice"], classification="internal")
        client = _record("alice", acl_allow=["alice"], classification="internal")
        assert _server_wins_on_acl(server, client) is True

    def test_narrowed_acl_allowed(self) -> None:
        """Client removing a principal from acl_allow is allowed (stricter)."""
        server = _record("alice", acl_allow=["alice", "bob"], classification="internal")
        client = _record("alice", acl_allow=["alice"], classification="internal")
        assert _server_wins_on_acl(server, client) is True

    def test_widened_acl_rejected(self) -> None:
        server = _record("alice", acl_allow=["alice"], classification="internal")
        client = _record("alice", acl_allow=["alice", "charlie"], classification="internal")
        assert _server_wins_on_acl(server, client) is False

    def test_looser_classification_rejected(self) -> None:
        server = _record("alice", acl_allow=["alice"], classification="confidential")
        client = _record("alice", acl_allow=["alice"], classification="internal")
        assert _server_wins_on_acl(server, client) is False

    def test_stricter_classification_allowed(self) -> None:
        server = _record("alice", acl_allow=["alice"], classification="internal")
        client = _record("alice", acl_allow=["alice"], classification="confidential")
        assert _server_wins_on_acl(server, client) is True

    def test_classification_rank_ordering(self) -> None:
        assert _classification_rank("restricted") < _classification_rank("confidential")
        assert _classification_rank("confidential") < _classification_rank("internal")
        assert _classification_rank("internal") < _classification_rank("public")


# ===========================================================================
# T-583: first-push record (new record, no server version)
# ===========================================================================


class TestNewRecordPush:
    def test_new_record_accepted_without_server_version(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        """A brand-new record ID has no server version — widening check is skipped."""
        r = _record("alice", acl_allow=["alice", "bob"])
        result = engine.sync_push(alice, [r])
        assert r.record_id in result.accepted

    def test_new_record_is_pullable_after_push(
        self, engine: SyncEngine, alice: _Principal, bob: _Principal
    ) -> None:
        r = _record("alice", acl_allow=["alice", "bob"])
        engine.sync_push(alice, [r])

        pulled_alice = engine.sync_pull(alice, since=datetime.min)
        pulled_bob = engine.sync_pull(bob, since=datetime.min)

        assert any(rec.record_id == r.record_id for rec in pulled_alice)
        assert any(rec.record_id == r.record_id for rec in pulled_bob)


# ===========================================================================
# T-586: idempotency
# ===========================================================================


class TestIdempotency:
    def test_duplicate_push_is_idempotent(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        """Pushing the same record twice should not duplicate it in pull results."""
        r = _record("alice", acl_allow=["alice"])
        engine.sync_push(alice, [r])
        engine.sync_push(alice, [r])  # same record again

        pulled = engine.sync_pull(alice, since=datetime.min)
        matching = [rec for rec in pulled if rec.record_id == r.record_id]
        assert len(matching) == 1

    def test_resumable_after_partial_batch(
        self, engine: SyncEngine, alice: _Principal
    ) -> None:
        """Pushing a partial batch can be retried — already-accepted records are idempotent."""
        r1 = _record("alice", acl_allow=["alice"])
        r2 = _record("alice", acl_allow=["alice"])

        # Push both
        result = engine.sync_push(alice, [r1, r2])
        assert set(result.accepted) == {r1.record_id, r2.record_id}

        # Retry the push — both should still be accepted
        result2 = engine.sync_push(alice, [r1, r2])
        assert set(result2.accepted) == {r1.record_id, r2.record_id}
        assert not result2.rejected
