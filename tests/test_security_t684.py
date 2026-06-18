"""Regression tests for T-684 security fixes (F-001, F-002, F-006, F-008).

Each test class covers exactly one finding so failures are traceable to a
specific CVE/finding in docs/decisions/pentest-findings-S199.md.
"""
from __future__ import annotations

import logging
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# F-001: nonce=None bypass in TokenValidator
# ---------------------------------------------------------------------------


class TestF001NonceReplayBypass:
    """F-001: validate(token, nonce=None) must reject tokens that carry a nonce claim."""

    def _make_validator(self, claims: dict[str, Any]):
        """Return a TokenValidator whose JWKS lookup and signature verify always pass."""
        from depthfusion.identity.token_validator import TokenValidator

        jwks_cache = MagicMock()
        # _jwk_to_public_key and public_key.verify are called inside validate();
        # mock the whole chain so we can test claim logic in isolation.
        public_key = MagicMock()
        public_key.verify.return_value = None  # no exception = signature OK
        jwks_cache.get_key = AsyncMock(return_value={"kty": "RSA", "n": "AA", "e": "AQ"})

        validator = TokenValidator(
            jwks_cache=jwks_cache,
            expected_issuer="https://issuer.example",
            expected_audience="api://audience",
        )

        # Patch the internal decode so we control the claims returned.
        import base64, json

        _HEADER_CLAIMS = {"alg": "RS256", "kid": "test-kid"}

        def _fake_decode(segment: str, what: str) -> dict:
            # Return RS256 header for header decodes, actual claims for payload.
            return _HEADER_CLAIMS if what == "JWT header" else claims

        validator._decode_json_segment = _fake_decode  # type: ignore[method-assign]

        # Patch _split to return three dummy segments.
        validator._split = lambda token: ("hdr", "pay", "sig")  # type: ignore[method-assign]

        # Patch signature verification to always pass.
        from cryptography.hazmat.primitives.asymmetric import rsa
        real_key = MagicMock()
        real_key.verify.return_value = None

        import depthfusion.identity.token_validator as _mod
        original_jwk_to_key = _mod._jwk_to_public_key
        _mod._jwk_to_public_key = lambda jwk: real_key  # type: ignore[assignment]

        validator._jwk_to_public_key_patch = (_mod, original_jwk_to_key)
        return validator

    @pytest.mark.asyncio
    async def test_token_with_nonce_claim_and_no_nonce_arg_raises(self):
        """Regression: validate() must raise when token has nonce but caller omits nonce=."""
        from depthfusion.identity.errors import TokenInvalidError

        future_exp = time.time() + 3600
        claims = {
            "sub": "user-1",
            "exp": future_exp,
            "iss": "https://issuer.example",
            "aud": "api://audience",
            "nonce": "abc123",  # token carries a nonce
        }
        validator = self._make_validator(claims)
        # _make_validator stores the TRUE original in _jwk_to_public_key_patch[1].
        # Capturing _mod._jwk_to_public_key AFTER the call would snapshot the mock, not
        # the real function, leaving the module polluted after teardown.
        _restore_mod, _restore_fn = validator._jwk_to_public_key_patch
        try:
            with pytest.raises(TokenInvalidError, match="nonce"):
                await validator.validate("fake.jwt.token")  # nonce= omitted (default None)
        finally:
            _restore_mod._jwk_to_public_key = _restore_fn

    @pytest.mark.asyncio
    async def test_token_with_nonce_claim_and_correct_nonce_passes(self):
        """validate() must succeed when the correct nonce is supplied."""
        future_exp = time.time() + 3600
        claims = {
            "sub": "user-1",
            "exp": future_exp,
            "iss": "https://issuer.example",
            "aud": "api://audience",
            "nonce": "abc123",
        }
        validator = self._make_validator(claims)
        _restore_mod, _restore_fn = validator._jwk_to_public_key_patch
        try:
            result = await validator.validate("fake.jwt.token", nonce="abc123")
            assert result["nonce"] == "abc123"
        finally:
            _restore_mod._jwk_to_public_key = _restore_fn

    @pytest.mark.asyncio
    async def test_token_without_nonce_claim_and_no_nonce_arg_passes(self):
        """validate() must succeed for nonce-free tokens when nonce= is omitted."""
        future_exp = time.time() + 3600
        claims = {
            "sub": "user-1",
            "exp": future_exp,
            "iss": "https://issuer.example",
            "aud": "api://audience",
            # No 'nonce' key
        }
        validator = self._make_validator(claims)
        _restore_mod, _restore_fn = validator._jwk_to_public_key_patch
        try:
            result = await validator.validate("fake.jwt.token")
            assert result["sub"] == "user-1"
        finally:
            _restore_mod._jwk_to_public_key = _restore_fn


# ---------------------------------------------------------------------------
# F-002: Dual Role enum — MEMBER blocked from INTERNAL data
# ---------------------------------------------------------------------------


class TestF002MemberRoleInternalAccess:
    """F-002: principal with groups=['member'] must be permitted to read INTERNAL data."""

    def test_role_enum_has_member(self):
        """classification.Role must contain MEMBER with value 'member'."""
        from depthfusion.authz.classification import Role

        assert hasattr(Role, "MEMBER"), "Role enum missing MEMBER"
        assert Role.MEMBER.value == "member"

    def test_internal_policy_allows_member(self):
        """CLASSIFICATION_POLICY[INTERNAL].allowed_roles must include Role.MEMBER."""
        from depthfusion.authz.classification import (
            CLASSIFICATION_POLICY,
            ClassificationLevel,
            Role,
        )

        internal_policy = CLASSIFICATION_POLICY[ClassificationLevel.INTERNAL]
        assert Role.MEMBER in internal_policy["allowed_roles"], (
            "Role.MEMBER must be in INTERNAL allowed_roles"
        )

    def test_member_principal_can_read_internal_record_in_acl(self):
        """PolicyEngine must allow a MEMBER principal to read an INTERNAL record they are in."""
        try:
            from depthfusion.authz.policy_engine import get_policy_engine
            from depthfusion.identity.models import Principal
            from depthfusion.authz.roles import Capability
        except ImportError as exc:
            pytest.skip(f"policy engine unavailable: {exc}")

        engine = get_policy_engine()
        principal = Principal(principal_id="member-user", groups=["member"])
        resource = {
            "acl_allow": ["member-user"],
            "classification": "internal",
        }
        decision = engine.decide(principal, Capability.READ_OWN_RECORDS, resource)
        assert decision.allow, (
            f"MEMBER principal denied INTERNAL record in their ACL. "
            f"Reason: {getattr(decision, 'reason', 'unknown')}"
        )

    def test_viewer_still_allowed_for_internal(self):
        """Regression: VIEWER role must still have INTERNAL access after the fix."""
        from depthfusion.authz.classification import (
            CLASSIFICATION_POLICY,
            ClassificationLevel,
            Role,
        )

        internal_policy = CLASSIFICATION_POLICY[ClassificationLevel.INTERNAL]
        assert Role.VIEWER in internal_policy["allowed_roles"]

    def test_member_not_in_confidential_policy(self):
        """MEMBER must NOT have CONFIDENTIAL access (role hierarchy preserved)."""
        from depthfusion.authz.classification import (
            CLASSIFICATION_POLICY,
            ClassificationLevel,
            Role,
        )

        confidential_policy = CLASSIFICATION_POLICY[ClassificationLevel.CONFIDENTIAL]
        assert Role.MEMBER not in confidential_policy["allowed_roles"], (
            "MEMBER must not have CONFIDENTIAL access"
        )


# ---------------------------------------------------------------------------
# F-006: CacheManager ephemeral key warning
# ---------------------------------------------------------------------------


class TestF006EphemeralKeyWarning:
    """F-006: CacheManager(key=None) must emit a WARNING log."""

    def test_key_none_emits_warning(self, caplog):
        """CacheManager constructed with key=None must log at WARNING level."""
        from depthfusion.cache.manager import CacheManager

        with caplog.at_level(logging.WARNING, logger="depthfusion.cache.manager"):
            CacheManager(db_path=":memory:", key=None)

        warning_records = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert warning_records, "Expected a WARNING log when key=None"
        assert any("DEPTHFUSION_CACHE_KEY" in r.message for r in warning_records), (
            "WARNING must mention DEPTHFUSION_CACHE_KEY so operators know the fix"
        )

    def test_key_none_warning_mentions_restart(self, caplog):
        """The warning must mention that data will be lost on restart."""
        from depthfusion.cache.manager import CacheManager

        with caplog.at_level(logging.WARNING, logger="depthfusion.cache.manager"):
            CacheManager(db_path=":memory:", key=None)

        combined = " ".join(r.message for r in caplog.records)
        assert "restart" in combined.lower()

    def test_explicit_key_no_warning(self, caplog):
        """CacheManager with an explicit key must NOT emit the ephemeral-key warning."""
        from cryptography.fernet import Fernet
        from depthfusion.cache.manager import CacheManager

        key = Fernet.generate_key()
        with caplog.at_level(logging.WARNING, logger="depthfusion.cache.manager"):
            CacheManager(db_path=":memory:", key=key)

        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "ephemeral" in r.message.lower()
        ]
        assert not warning_records, (
            "No ephemeral-key warning should fire when a real key is provided"
        )

    def test_cache_still_functional_with_no_key(self):
        """CacheManager with key=None must still put/get correctly within one process."""
        from depthfusion.cache.manager import CacheManager

        cm = CacheManager(db_path=":memory:", key=None)
        entry = cm.put("path/a", "principal-1", b"payload data")
        result = cm.get("path/a", "principal-1")
        # get() returns a CacheEntry on hit, None on miss/decryption failure.
        assert result is not None, "Cache get must return entry on hit"
        assert result.path == "path/a"


# ---------------------------------------------------------------------------
# F-008: PurgeEngine HWM persisted across restarts
# ---------------------------------------------------------------------------


class TestF008HwmPersisted:
    """F-008: PurgeEngine HWM must survive a simulated process restart."""

    def _make_engine_and_manager(self, store, wall):
        """Helper: build a (manager, engine) pair sharing the given store."""
        from depthfusion.cache.lease_lifecycle import LeaseManager, PurgeEngine

        class _NullCacheWiper:
            def wipe_record(self, record_id: str) -> None:
                pass

            def wipe_all(self) -> None:
                pass

        class _NullTokenWiper:
            def wipe_token(self) -> None:
                pass

        wiper = _NullCacheWiper()
        twiper = _NullTokenWiper()

        mgr = LeaseManager(
            store=store,
            cache_wiper=wiper,
            token_wiper=twiper,
            time_fn=lambda: wall[0],
        )
        engine = PurgeEngine(
            store=store,
            cache_wiper=wiper,
            token_wiper=twiper,
            time_fn=lambda: wall[0],
        )
        return mgr, engine

    def test_hwm_persisted_to_store(self):
        """After _effective_now advances HWM, store.get_hwm() must return the new value."""
        from depthfusion.cache.lease_lifecycle import InMemoryLeaseStore, PurgeEngine

        store = InMemoryLeaseStore()
        wall = [1000.0]

        class _NullCacheWiper:
            def wipe_record(self, record_id): pass
            def wipe_all(self): pass

        class _NullTokenWiper:
            def wipe_token(self): pass

        engine = PurgeEngine(
            store=store,
            cache_wiper=_NullCacheWiper(),
            token_wiper=_NullTokenWiper(),
            time_fn=lambda: wall[0],
        )
        engine._effective_now(1000.0)
        assert store.get_hwm() == 1000.0, "HWM must be persisted to the store"

        wall[0] = 2000.0
        engine._effective_now(2000.0)
        assert store.get_hwm() == 2000.0

    def test_fresh_engine_loads_hwm_from_store(self):
        """A new PurgeEngine must initialize _high_water_mark from the store."""
        from depthfusion.cache.lease_lifecycle import InMemoryLeaseStore, PurgeEngine

        store = InMemoryLeaseStore()
        store.set_hwm(5000.0)  # simulate persisted HWM from a previous run

        class _NullCacheWiper:
            def wipe_record(self, record_id): pass
            def wipe_all(self): pass

        class _NullTokenWiper:
            def wipe_token(self): pass

        engine = PurgeEngine(
            store=store,
            cache_wiper=_NullCacheWiper(),
            token_wiper=_NullTokenWiper(),
        )
        assert engine.high_water_mark == 5000.0, (
            "PurgeEngine must load persisted HWM from store at construction"
        )

    def test_restart_with_rollback_blocks_revival(self):
        """After fix: process restart + clock rollback must not revive an expired lease.

        Scenario (mirrors AV-05-T3 pentest):
          T=100: lease issued, expires_at=150 (TTL=50s — short enough to expire before T=200)
          T=200: engine1 runs, persists HWM=200 to the store
          T=50:  process "restarts" (new PurgeEngine), clock rolled back
          Expected: effective_now >= 200 (HWM loaded from store), lease EXPIRED since 200 > 150
        """
        from depthfusion.cache.lease_lifecycle import (
            ClassificationLevel,
            InMemoryLeaseStore,
            Lease,
            LeaseStatus,
            PurgeEngine,
        )

        store = InMemoryLeaseStore()
        wall = [100.0]

        class _NullCacheWiper:
            def wipe_record(self, record_id): pass
            def wipe_all(self): pass

        class _NullTokenWiper:
            def wipe_token(self): pass

        wiper = _NullCacheWiper()
        twiper = _NullTokenWiper()

        # Issue a lease with a short TTL (expires_at=150) directly, bypassing
        # ClassificationLevel.PUBLIC's 7-day default TTL.  This mirrors the pentest
        # scenario where the lease expires naturally before the clock is rolled back.
        lease = Lease(
            record_id="record-hwm",
            classification=ClassificationLevel.PUBLIC,
            issued_at=100.0,
            expires_at=150.0,  # expires 50s after issue — before the T=200 HWM snapshot
        )
        store.upsert(lease)

        # Advance clock so HWM anchors at 200
        wall[0] = 200.0
        engine1 = PurgeEngine(
            store=store, cache_wiper=wiper, token_wiper=twiper,
            time_fn=lambda: wall[0],
        )
        engine1.run_on_timer()  # persists HWM=200 to the store

        # Simulate process restart (new PurgeEngine instance)
        wall[0] = 50.0  # clock rolled back
        engine2 = PurgeEngine(
            store=store, cache_wiper=wiper, token_wiper=twiper,
            time_fn=lambda: wall[0],
        )
        # Without the fix: engine2._high_water_mark would be 0.0 → effective_now=50
        # With the fix: engine2._high_water_mark loads 200 from store → effective_now=200
        effective_now, _ = engine2._effective_now(None)

        assert effective_now >= 200.0, (
            f"After fix, effective_now must be >= persisted HWM (200), got {effective_now}"
        )
        assert lease.status(effective_now) == LeaseStatus.EXPIRED, (
            "Lease must remain EXPIRED after restart+rollback when HWM is persisted"
        )

    def test_in_memory_store_get_set_hwm(self):
        """InMemoryLeaseStore.get_hwm/set_hwm must round-trip correctly."""
        from depthfusion.cache.lease_lifecycle import InMemoryLeaseStore

        store = InMemoryLeaseStore()
        assert store.get_hwm() == 0.0
        store.set_hwm(12345.678)
        assert store.get_hwm() == 12345.678
        store.set_hwm(0.0)
        assert store.get_hwm() == 0.0
