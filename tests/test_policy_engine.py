"""Tests for T-568/T-570: PolicyEngine.decide() — correctness and property tests.

Coverage areas:

Unit tests (deterministic):
- Unknown action → deny (safe default)
- RBAC check: principal without required capability → deny
- ACL check: principal not in acl_allow → deny
- Admin override: READ_ALL_RECORDS bypasses ACL for read capabilities
- Admin override: WRITE_ALL_RECORDS bypasses ACL for write capabilities
- Classification check: correct role → allow; missing role → deny
- Unknown classification label → deny
- Cache hit returns same decision
- Cache invalidation clears entries for a principal
- allow=True only when all three checks pass

Property tests (parametrised exhaustive):
- For every (principal, resource) combination where principal is NOT in acl_allow
  and does NOT hold READ_ALL_RECORDS / WRITE_ALL_RECORDS: decide() must return
  allow=False (no ACL bypass through any capability).
- Owner principal (all capabilities + explicit ACL membership) → always allow for
  all known capabilities.
- Classification-excluded principal → always deny regardless of capability / ACL.
"""
from __future__ import annotations

import threading
import time

import pytest

from depthfusion.authz.policy_engine import (
    PolicyDecision,
    PolicyEngine,
    _DecisionCache,
    _make_cache_key,
    _resolve_capability,
)
from depthfusion.authz.roles import Capability, Role, ROLE_CAPABILITIES
from depthfusion.identity.models import Principal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _principal(
    pid: str = "user-1",
    groups: list[str] | None = None,
) -> Principal:
    return Principal(
        principal_id=pid,
        upn=f"{pid}@example.com",
        display_name=pid,
        groups=groups or [],
    )


def _owner(pid: str = "owner-1") -> Principal:
    return _principal(pid=pid, groups=[Role.OWNER.value])


def _admin(pid: str = "admin-1") -> Principal:
    return _principal(pid=pid, groups=[Role.ADMIN.value])


def _member(pid: str = "member-1") -> Principal:
    return _principal(pid=pid, groups=[Role.MEMBER.value])


def _viewer(pid: str = "viewer-1") -> Principal:
    return _principal(pid=pid, groups=[Role.VIEWER.value])


def _no_role(pid: str = "nobody-1") -> Principal:
    return _principal(pid=pid, groups=[])


# ---------------------------------------------------------------------------
# _resolve_capability (pure helper)
# ---------------------------------------------------------------------------


class TestResolveCapability:
    def test_known_string(self) -> None:
        assert _resolve_capability("read_own_records") == Capability.READ_OWN_RECORDS

    def test_capability_passthrough(self) -> None:
        cap = Capability.WRITE_OWN_RECORDS
        assert _resolve_capability(cap) is cap

    def test_unknown_string_returns_none(self) -> None:
        assert _resolve_capability("totally_unknown_action") is None

    def test_empty_string_returns_none(self) -> None:
        assert _resolve_capability("") is None


# ---------------------------------------------------------------------------
# PolicyDecision dataclass
# ---------------------------------------------------------------------------


class TestPolicyDecision:
    def test_frozen(self) -> None:
        d = PolicyDecision(allow=True, reason="ok")
        with pytest.raises((AttributeError, TypeError)):
            d.allow = False  # type: ignore[misc]

    def test_capability_optional(self) -> None:
        d = PolicyDecision(allow=False, reason="denied")
        assert d.capability is None

    def test_with_capability(self) -> None:
        d = PolicyDecision(allow=True, reason="ok", capability=Capability.READ_OWN_RECORDS)
        assert d.capability == Capability.READ_OWN_RECORDS


# ---------------------------------------------------------------------------
# _DecisionCache
# ---------------------------------------------------------------------------


class TestDecisionCache:
    def _decision(self, allow: bool = True) -> PolicyDecision:
        return PolicyDecision(allow=allow, reason="test")

    def test_miss_returns_none(self) -> None:
        cache = _DecisionCache()
        assert cache.get(("x", "cap", (), "")) is None

    def test_hit_returns_decision(self) -> None:
        cache = _DecisionCache()
        d = self._decision()
        cache.put(("x", "cap", (), ""), d)
        assert cache.get(("x", "cap", (), "")) is d

    def test_expired_entry_returns_none(self) -> None:
        cache = _DecisionCache(ttl=0.01)
        d = self._decision()
        cache.put(("x", "cap", (), ""), d)
        time.sleep(0.05)
        assert cache.get(("x", "cap", (), "")) is None

    def test_invalidate_principal(self) -> None:
        cache = _DecisionCache()
        cache.put(("alice", "cap1", (), ""), self._decision())
        cache.put(("alice", "cap2", (), ""), self._decision())
        cache.put(("bob", "cap1", (), ""), self._decision())
        removed = cache.invalidate_principal("alice")
        assert removed == 2
        assert cache.get(("alice", "cap1", (), "")) is None
        assert cache.get(("bob", "cap1", (), "")) is not None

    def test_clear(self) -> None:
        cache = _DecisionCache()
        cache.put(("x", "c", (), ""), self._decision())
        cache.clear()
        assert cache.size == 0

    def test_max_size_eviction(self) -> None:
        cache = _DecisionCache(max_size=5)
        for i in range(10):
            cache.put((str(i), "cap", (), ""), self._decision())
        assert cache.size <= 5

    def test_thread_safety(self) -> None:
        cache = _DecisionCache()
        errors: list[Exception] = []

        def writer(n: int) -> None:
            try:
                for i in range(50):
                    cache.put((f"p{n}", f"cap{i}", (), ""), self._decision())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# ---------------------------------------------------------------------------
# PolicyEngine.decide() — unit tests
# ---------------------------------------------------------------------------


class TestPolicyEngineDecide:
    def setup_method(self) -> None:
        self.engine = PolicyEngine(cache_ttl=60.0)

    # -- Unknown action

    def test_unknown_action_string_denied(self) -> None:
        p = _member()
        dec = self.engine.decide(p, "nonexistent_action", {"acl_allow": [p.principal_id]})
        assert not dec.allow
        assert dec.capability is None

    # -- RBAC failures

    def test_no_role_denied(self) -> None:
        p = _no_role()
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id]},
        )
        assert not dec.allow
        assert p.principal_id in dec.reason or "capability" in dec.reason.lower()

    def test_viewer_write_denied(self) -> None:
        p = _viewer()
        dec = self.engine.decide(
            p,
            Capability.WRITE_OWN_RECORDS,
            {"acl_allow": [p.principal_id]},
        )
        assert not dec.allow

    def test_member_manage_users_denied(self) -> None:
        p = _member()
        dec = self.engine.decide(
            p,
            Capability.MANAGE_USERS,
            {"acl_allow": [p.principal_id]},
        )
        assert not dec.allow

    # -- ACL failures (capability present but not in ACL)

    def test_not_in_acl_denied(self) -> None:
        p = _member()
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": ["somebody-else"]},
        )
        assert not dec.allow
        assert "ACL" in dec.reason

    def test_empty_acl_denied(self) -> None:
        p = _member()
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": []},
        )
        assert not dec.allow

    def test_missing_acl_key_denied(self) -> None:
        p = _member()
        dec = self.engine.decide(p, Capability.READ_OWN_RECORDS, {})
        assert not dec.allow

    # -- Admin overrides

    def test_admin_read_all_bypasses_acl_for_read(self) -> None:
        p = _admin()
        # Admin has READ_ALL_RECORDS; resource has nobody in acl_allow
        dec = self.engine.decide(
            p,
            Capability.READ_SHARED_RECORDS,
            {"acl_allow": []},
        )
        assert dec.allow

    def test_owner_write_all_bypasses_acl_for_write(self) -> None:
        p = _owner()
        dec = self.engine.decide(
            p,
            Capability.WRITE_OWN_RECORDS,
            {"acl_allow": []},
        )
        assert dec.allow

    def test_member_no_bypass(self) -> None:
        # Member has neither READ_ALL_RECORDS nor WRITE_ALL_RECORDS → no override
        p = _member()
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": []},
        )
        assert not dec.allow

    # -- Classification checks

    def test_public_classification_any_role(self) -> None:
        p = _viewer()
        # viewer role maps to "viewer" in classification policy allowed_roles
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id], "classification": "public"},
        )
        assert dec.allow

    def test_restricted_classification_non_admin_denied(self) -> None:
        p = _member()
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id], "classification": "restricted"},
        )
        assert not dec.allow
        assert "classified" in dec.reason.lower() or "role" in dec.reason.lower()

    def test_restricted_classification_admin_allowed(self) -> None:
        # admin role → matches classification.Role.ADMIN
        p = _admin()
        dec = self.engine.decide(
            p,
            Capability.READ_ALL_RECORDS,  # admin has this
            {"acl_allow": [p.principal_id], "classification": "restricted"},
        )
        assert dec.allow

    def test_unknown_classification_denied(self) -> None:
        p = _owner()
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id], "classification": "top_secret_x"},
        )
        assert not dec.allow
        assert "unknown" in dec.reason.lower() or "deny" in dec.reason.lower()

    def test_no_classification_field_skips_check(self) -> None:
        p = _member()
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id]},
        )
        assert dec.allow

    # -- Happy path

    def test_member_read_own_in_acl_allowed(self) -> None:
        p = _member()
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id]},
        )
        assert dec.allow
        assert dec.capability == Capability.READ_OWN_RECORDS

    def test_string_action_resolves_correctly(self) -> None:
        p = _member()
        dec = self.engine.decide(
            p,
            "read_own_records",
            {"acl_allow": [p.principal_id]},
        )
        assert dec.allow

    # -- Cache behaviour

    def test_cache_hit_on_second_call(self) -> None:
        p = _member()
        resource = {"acl_allow": [p.principal_id]}
        d1 = self.engine.decide(p, Capability.READ_OWN_RECORDS, resource)
        d2 = self.engine.decide(p, Capability.READ_OWN_RECORDS, resource)
        assert d1 is d2  # same object from cache

    def test_cache_size_grows(self) -> None:
        before = self.engine.cache_size
        p = _member()
        self.engine.decide(
            p, Capability.READ_OWN_RECORDS, {"acl_allow": [p.principal_id]}
        )
        assert self.engine.cache_size == before + 1

    def test_invalidate_clears_principal_entries(self) -> None:
        p = _member()
        self.engine.decide(
            p, Capability.READ_OWN_RECORDS, {"acl_allow": [p.principal_id]}
        )
        removed = self.engine.invalidate(p.principal_id)
        assert removed >= 1
        assert self.engine.cache_size == 0

    def test_clear_cache(self) -> None:
        p = _member()
        self.engine.decide(
            p, Capability.READ_OWN_RECORDS, {"acl_allow": [p.principal_id]}
        )
        self.engine.clear_cache()
        assert self.engine.cache_size == 0

    # -- Return type contract

    def test_decide_never_raises(self) -> None:
        """decide() must always return a PolicyDecision, never raise."""
        p = _no_role()
        dec = self.engine.decide(p, "", {})
        assert isinstance(dec, PolicyDecision)
        assert isinstance(dec.allow, bool)
        assert isinstance(dec.reason, str)


# ---------------------------------------------------------------------------
# Property tests: no ACL bypass path exists for principals without override caps
# ---------------------------------------------------------------------------


class TestNoACLBypassProperty:
    """For every Capability × Role combination where the principal lacks the
    override capabilities (READ_ALL_RECORDS / WRITE_ALL_RECORDS), NOT being in
    acl_allow must always produce deny.

    This is the key security property: no capability should accidentally widen
    access to a resource the principal isn't explicitly permitted to reach,
    unless the explicit admin-override caps are present.
    """

    def setup_method(self) -> None:
        self.engine = PolicyEngine(cache_ttl=60.0)

    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
    @pytest.mark.parametrize("capability", list(Capability))
    def test_not_in_acl_always_denies_for_non_override_roles(
        self, role: Role, capability: Capability
    ) -> None:
        # member and viewer never hold READ_ALL_RECORDS or WRITE_ALL_RECORDS
        caps = ROLE_CAPABILITIES[role]
        assert Capability.READ_ALL_RECORDS not in caps
        assert Capability.WRITE_ALL_RECORDS not in caps

        p = _principal(pid="test-user", groups=[role.value])
        dec = self.engine.decide(
            p,
            capability,
            {"acl_allow": ["someone-else"]},  # principal not listed
        )
        assert not dec.allow, (
            f"Expected deny for role={role.value} capability={capability.value} "
            f"when not in ACL, but got allow=True"
        )


# ---------------------------------------------------------------------------
# Property tests: owner always allowed for all capabilities
# ---------------------------------------------------------------------------


class TestOwnerAlwaysAllowed:
    """Owner role holds all capabilities and the override caps; when explicitly
    listed in acl_allow, every capability must allow.
    """

    def setup_method(self) -> None:
        self.engine = PolicyEngine(cache_ttl=60.0)

    @pytest.mark.parametrize("capability", list(Capability))
    def test_owner_in_acl_always_allowed(self, capability: Capability) -> None:
        p = _owner()
        dec = self.engine.decide(
            p,
            capability,
            {"acl_allow": [p.principal_id]},
        )
        assert dec.allow, (
            f"Owner should be allowed for capability={capability.value}, "
            f"but got: {dec.reason}"
        )

    @pytest.mark.parametrize("capability", list(Capability))
    def test_owner_not_in_acl_still_allowed_via_override(
        self, capability: Capability
    ) -> None:
        p = _owner()
        dec = self.engine.decide(
            p,
            capability,
            {"acl_allow": []},  # not in ACL
        )
        # Owner has both READ_ALL_RECORDS and WRITE_ALL_RECORDS → override for
        # all read/write caps.  Admin caps (MANAGE_*, ASSIGN_*, VIEW_AUDIT_LOG)
        # are not covered by the override and will still deny without ACL entry.
        from depthfusion.authz.policy_engine import _READ_CAPS, _WRITE_CAPS  # noqa: PLC0415
        if capability in _READ_CAPS or capability in _WRITE_CAPS:
            assert dec.allow, (
                f"Owner should bypass ACL for {capability.value}, got deny: {dec.reason}"
            )


# ---------------------------------------------------------------------------
# Property tests: classification-excluded principal always denies
# ---------------------------------------------------------------------------


class TestClassificationExclusion:
    """A principal with a role not permitted for a given classification level
    must be denied even when they're in the ACL and hold the capability.
    """

    def setup_method(self) -> None:
        self.engine = PolicyEngine(cache_ttl=60.0)

    @pytest.mark.parametrize("classification", ["restricted", "confidential"])
    def test_viewer_excluded_from_restricted_confidential(
        self, classification: str
    ) -> None:
        # viewer role is NOT in allowed_roles for restricted or confidential
        p = _principal(pid="viewer-p", groups=["viewer"])
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id], "classification": classification},
        )
        assert not dec.allow, (
            f"Viewer should be denied {classification} data, but got allow=True"
        )

    @pytest.mark.parametrize("capability", [
        Capability.READ_OWN_RECORDS,
        Capability.READ_SHARED_RECORDS,
    ])
    def test_member_excluded_from_restricted(self, capability: Capability) -> None:
        # member role is NOT in allowed_roles for restricted
        p = _principal(pid="member-p", groups=["member"])
        dec = self.engine.decide(
            p,
            capability,
            {"acl_allow": [p.principal_id], "classification": "restricted"},
        )
        assert not dec.allow


# ---------------------------------------------------------------------------
# S-191 T-662 — Signed offline policy snapshot
# ---------------------------------------------------------------------------

from depthfusion.authz.policy_snapshot import (  # noqa: E402
    SNAPSHOT_KEY_ENV,
    PolicySnapshotError,
    SignedPolicySnapshot,
    SnapshotVerification,
    current_classification_policy_payload,
    sign_policy_snapshot,
    verify_policy_snapshot,
)

# A fixed, test-only signing key. Installed via the env var the production code
# reads, BEFORE any snapshot is signed/verified (per project keyring-test rule).
_TEST_SNAPSHOT_KEY = "00112233445566778899aabbccddeeff"


@pytest.fixture()
def snapshot_key_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Install a test-only signing key in the environment the code reads."""
    monkeypatch.setenv(SNAPSHOT_KEY_ENV, _TEST_SNAPSHOT_KEY)
    return _TEST_SNAPSHOT_KEY


class TestPolicySnapshotSigning:
    """Snapshot signing + verification primitives (positive AND negative)."""

    def test_sign_then_verify_ok(self, snapshot_key_env: str) -> None:
        snap = sign_policy_snapshot(version=1)
        assert snap.signature  # non-empty signature was produced
        # Positive case: a freshly-signed, unexpired snapshot verifies OK.
        assert verify_policy_snapshot(snap) is SnapshotVerification.OK

    def test_sign_with_explicit_key(self) -> None:
        snap = sign_policy_snapshot(version=2, key=_TEST_SNAPSHOT_KEY)
        assert (
            verify_policy_snapshot(snap, key=_TEST_SNAPSHOT_KEY)
            is SnapshotVerification.OK
        )

    def test_missing_key_raises_on_sign(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SNAPSHOT_KEY_ENV, raising=False)
        with pytest.raises(PolicySnapshotError):
            sign_policy_snapshot(version=1)

    def test_unsigned_snapshot_refused(self, snapshot_key_env: str) -> None:
        # Negative case: a snapshot with no signature is UNSIGNED (deny).
        snap = sign_policy_snapshot(version=1)
        unsigned = SignedPolicySnapshot(
            version=snap.version,
            issued_at=snap.issued_at,
            expires_at=snap.expires_at,
            policy=snap.policy,
            signature="",
        )
        assert verify_policy_snapshot(unsigned) is SnapshotVerification.UNSIGNED

    def test_tampered_policy_refused(self, snapshot_key_env: str) -> None:
        # Negative case: widening allowed_roles after signing breaks the HMAC.
        snap = sign_policy_snapshot(version=1)
        tampered_policy = {k: list(v) for k, v in snap.policy.items()}
        tampered_policy["restricted"] = sorted(
            set(tampered_policy.get("restricted", [])) | {"viewer", "member"}
        )
        tampered = SignedPolicySnapshot(
            version=snap.version,
            issued_at=snap.issued_at,
            expires_at=snap.expires_at,
            policy=tampered_policy,
            signature=snap.signature,  # stale signature over old body
        )
        assert verify_policy_snapshot(tampered) is SnapshotVerification.TAMPERED

    def test_tampered_signature_refused(self, snapshot_key_env: str) -> None:
        snap = sign_policy_snapshot(version=1)
        forged = SignedPolicySnapshot(
            version=snap.version,
            issued_at=snap.issued_at,
            expires_at=snap.expires_at,
            policy=snap.policy,
            signature="deadbeef" * 8,  # 64 hex chars, but wrong
        )
        assert verify_policy_snapshot(forged) is SnapshotVerification.TAMPERED

    def test_wrong_key_refused(self, snapshot_key_env: str) -> None:
        snap = sign_policy_snapshot(version=1)
        assert (
            verify_policy_snapshot(snap, key="ffffffffffffffffffffffffffffffff")
            is SnapshotVerification.TAMPERED
        )

    def test_expired_snapshot_refused(self, snapshot_key_env: str) -> None:
        # issued in the past with a short TTL → expired by `now`.
        snap = sign_policy_snapshot(version=1, now=1000.0, ttl_seconds=10)
        # valid just before expiry
        assert (
            verify_policy_snapshot(snap, now=1005.0) is SnapshotVerification.OK
        )
        # refused at/after expiry
        assert (
            verify_policy_snapshot(snap, now=2000.0)
            is SnapshotVerification.EXPIRED
        )

    def test_roundtrip_dict_preserves_verification(self, snapshot_key_env: str) -> None:
        snap = sign_policy_snapshot(version=3)
        restored = SignedPolicySnapshot.from_dict(snap.to_dict())
        assert verify_policy_snapshot(restored) is SnapshotVerification.OK

    def test_payload_matches_live_policy(self) -> None:
        payload = current_classification_policy_payload()
        # restricted must NOT grant viewer/member (sanity vs the live policy).
        assert "restricted" in payload
        assert "viewer" not in payload["restricted"]
        assert "member" not in payload["restricted"]


class TestPolicyEngineOfflineSnapshot:
    """Wire-up of the signed snapshot into ``PolicyEngine.decide`` (S-191 AC-3)."""

    def setup_method(self) -> None:
        self.engine = PolicyEngine(cache_ttl=60.0)

    def test_offline_matches_online_for_allowed(self, snapshot_key_env: str) -> None:
        # admin reading restricted: online allows; offline (signed snapshot)
        # must yield the SAME decision.
        p = _admin()
        resource = {
            "acl_allow": [p.principal_id],
            "classification": "restricted",
        }
        online = self.engine.decide(p, Capability.READ_ALL_RECORDS, resource)
        assert online.allow

        snap = sign_policy_snapshot(version=1)
        offline = self.engine.decide(
            p, Capability.READ_ALL_RECORDS, resource, offline_snapshot=snap
        )
        assert offline.allow == online.allow == True  # noqa: E712

    def test_offline_matches_online_for_denied(self, snapshot_key_env: str) -> None:
        # viewer reading restricted: online denies; offline must also deny.
        p = _principal(pid="viewer-x", groups=["viewer"])
        resource = {
            "acl_allow": [p.principal_id],
            "classification": "restricted",
        }
        online = self.engine.decide(p, Capability.READ_OWN_RECORDS, resource)
        assert not online.allow

        snap = sign_policy_snapshot(version=1)
        offline = self.engine.decide(
            p, Capability.READ_OWN_RECORDS, resource, offline_snapshot=snap
        )
        assert offline.allow == online.allow == False  # noqa: E712

    def test_tampered_snapshot_denies_not_allows(self, snapshot_key_env: str) -> None:
        # Attacker widens restricted to include viewer in the offline snapshot.
        # The engine MUST deny (refuse the snapshot), not honour the forgery.
        p = _principal(pid="viewer-x", groups=["viewer"])
        snap = sign_policy_snapshot(version=1)
        tampered_policy = {k: list(v) for k, v in snap.policy.items()}
        tampered_policy["restricted"] = sorted(
            set(tampered_policy.get("restricted", [])) | {"viewer"}
        )
        tampered = SignedPolicySnapshot(
            version=snap.version,
            issued_at=snap.issued_at,
            expires_at=snap.expires_at,
            policy=tampered_policy,
            signature=snap.signature,
        )
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id], "classification": "restricted"},
            offline_snapshot=tampered,
        )
        assert not dec.allow
        assert "snapshot" in dec.reason.lower()

    def test_unsigned_snapshot_denies(self, snapshot_key_env: str) -> None:
        p = _admin()
        snap = sign_policy_snapshot(version=1)
        unsigned = SignedPolicySnapshot(
            version=snap.version,
            issued_at=snap.issued_at,
            expires_at=snap.expires_at,
            policy=snap.policy,
            signature="",
        )
        dec = self.engine.decide(
            p,
            Capability.READ_ALL_RECORDS,
            {"acl_allow": [p.principal_id], "classification": "restricted"},
            offline_snapshot=unsigned,
        )
        # Even though admin would normally be allowed, an unsigned snapshot
        # forces deny (fail-closed).
        assert not dec.allow

    def test_expired_snapshot_denies(self, snapshot_key_env: str) -> None:
        p = _admin()
        snap = sign_policy_snapshot(version=1, now=1000.0, ttl_seconds=10)
        dec = self.engine.decide(
            p,
            Capability.READ_ALL_RECORDS,
            {"acl_allow": [p.principal_id], "classification": "restricted"},
            offline_snapshot=snap,
            # decide() uses time.time() for expiry; snapshot is long expired.
        )
        assert not dec.allow

    def test_offline_no_classification_still_allows(self, snapshot_key_env: str) -> None:
        # No classification on the resource → snapshot is irrelevant; the
        # RBAC + ACL checks alone decide. A signed (but unconsulted) snapshot
        # must not break a normal allow.
        p = _member()
        snap = sign_policy_snapshot(version=1)
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id]},
            offline_snapshot=snap,
        )
        assert dec.allow

    def test_offline_rbac_failure_denies_before_snapshot(
        self, snapshot_key_env: str
    ) -> None:
        # A principal lacking the capability is denied at the RBAC stage even
        # with a perfectly valid snapshot — order is preserved.
        p = _no_role()
        snap = sign_policy_snapshot(version=1)
        dec = self.engine.decide(
            p,
            Capability.READ_OWN_RECORDS,
            {"acl_allow": [p.principal_id], "classification": "public"},
            offline_snapshot=snap,
        )
        assert not dec.allow

    def test_offline_decisions_not_cached(self, snapshot_key_env: str) -> None:
        # Offline decisions must not poison the online cache.
        before = self.engine.cache_size
        p = _admin()
        snap = sign_policy_snapshot(version=1)
        self.engine.decide(
            p,
            Capability.READ_ALL_RECORDS,
            {"acl_allow": [p.principal_id], "classification": "restricted"},
            offline_snapshot=snap,
        )
        assert self.engine.cache_size == before
