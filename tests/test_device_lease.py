"""Tests for DeviceLease — lease-based credential refresh.

Covers:
- DEFAULT_LEASE_HOURS is 24
- CLOCK_SKEW_TOLERANCE_SECONDS is 300 (5 minutes)
- check() returns MISSING when issued_at is None
- check() returns VALID for a freshly issued credential
- check() returns VALID when now == issued_at (same moment)
- check() returns VALID just before expiry (no skew applied)
- check() returns VALID within clock-skew tolerance after expiry
- check() returns EXPIRED beyond clock-skew tolerance
- check() returns EXPIRED for a very old credential
- is_valid() returns True / False mirroring check() == VALID
- needs_reenrollment() returns True for MISSING and EXPIRED, False for VALID
- custom lease_hours constructor overrides default
- DEPTHFUSION_DEVICE_LEASE_HOURS env var configures the lease
- env var falls back to default for invalid/empty/negative values
- device_id argument does not affect the result (any string accepted)
- clock_skew_seconds constructor arg is respected
"""
from __future__ import annotations

import time

import pytest

from depthfusion.identity.device_lease import (
    CLOCK_SKEW_TOLERANCE_SECONDS,
    DEFAULT_LEASE_HOURS,
    DeviceLease,
    LeaseStatus,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_lease_hours(self) -> None:
        assert DEFAULT_LEASE_HOURS == 24

    def test_clock_skew_tolerance_seconds(self) -> None:
        assert CLOCK_SKEW_TOLERANCE_SECONDS == 300


# ---------------------------------------------------------------------------
# LeaseStatus enum
# ---------------------------------------------------------------------------


class TestLeaseStatus:
    def test_values_are_stable_strings(self) -> None:
        assert LeaseStatus.VALID == "valid"
        assert LeaseStatus.EXPIRED == "expired"
        assert LeaseStatus.MISSING == "missing"


# ---------------------------------------------------------------------------
# DeviceLease.check()
# ---------------------------------------------------------------------------


class TestDeviceLeaseCheck:
    """Unit tests for DeviceLease.check() in isolation."""

    def _lease(self, **kw) -> DeviceLease:  # type: ignore[no-untyped-def]
        return DeviceLease(lease_hours=24, **kw)

    def test_missing_when_issued_at_none(self) -> None:
        lease = self._lease()
        status = lease.check("dev-x", issued_at=None)
        assert status is LeaseStatus.MISSING

    def test_valid_freshly_issued(self) -> None:
        lease = self._lease()
        now = time.time()
        status = lease.check("dev-x", issued_at=now, now=now)
        assert status is LeaseStatus.VALID

    def test_valid_same_moment(self) -> None:
        lease = self._lease()
        t = 1_700_000_000.0
        assert lease.check("dev-x", issued_at=t, now=t) is LeaseStatus.VALID

    def test_valid_one_second_before_expiry(self) -> None:
        lease = self._lease()
        t = 1_700_000_000.0
        expiry = t + lease.lease_seconds  # without skew
        # one second before the raw expiry — skew not yet needed
        status = lease.check("dev-x", issued_at=t, now=expiry - 1)
        assert status is LeaseStatus.VALID

    def test_valid_exactly_at_raw_expiry(self) -> None:
        """At the exact raw expiry moment the lease is still VALID (skew applies)."""
        lease = self._lease()
        t = 1_700_000_000.0
        expiry = t + lease.lease_seconds
        status = lease.check("dev-x", issued_at=t, now=expiry)
        assert status is LeaseStatus.VALID

    def test_valid_within_clock_skew(self) -> None:
        """A credential expired 4 minutes ago is still VALID (within 5-min skew)."""
        lease = self._lease()
        t = 1_700_000_000.0
        raw_expiry = t + lease.lease_seconds
        # 4 minutes past raw expiry — inside the 5-minute window
        four_minutes = 4 * 60
        status = lease.check("dev-x", issued_at=t, now=raw_expiry + four_minutes)
        assert status is LeaseStatus.VALID

    def test_valid_exactly_at_skew_boundary(self) -> None:
        """At exactly issued_at + lease + skew the lease is still VALID."""
        lease = self._lease()
        t = 1_700_000_000.0
        boundary = t + lease.lease_seconds + lease.clock_skew_seconds
        status = lease.check("dev-x", issued_at=t, now=boundary)
        assert status is LeaseStatus.VALID

    def test_expired_one_second_past_skew(self) -> None:
        """One second past issued_at + lease + skew yields EXPIRED."""
        lease = self._lease()
        t = 1_700_000_000.0
        past_boundary = t + lease.lease_seconds + lease.clock_skew_seconds + 1
        status = lease.check("dev-x", issued_at=t, now=past_boundary)
        assert status is LeaseStatus.EXPIRED

    def test_expired_very_old_credential(self) -> None:
        """A 48-hour-old credential (vs 24-hour lease) is EXPIRED."""
        lease = self._lease()
        t = 1_700_000_000.0
        forty_eight_hours_ago = t - 48 * 3600
        status = lease.check("dev-x", issued_at=forty_eight_hours_ago, now=t)
        assert status is LeaseStatus.EXPIRED

    def test_device_id_does_not_affect_result(self) -> None:
        """The device_id string has no effect on the lease check."""
        lease = self._lease()
        t = 1_700_000_000.0
        for dev_id in ("dev-1", "abc-xyz", "", "a" * 200):
            assert lease.check(dev_id, issued_at=t, now=t) is LeaseStatus.VALID


# ---------------------------------------------------------------------------
# is_valid() and needs_reenrollment()
# ---------------------------------------------------------------------------


class TestConvenienceHelpers:
    def _lease(self) -> DeviceLease:
        return DeviceLease(lease_hours=24)

    def test_is_valid_true_for_valid(self) -> None:
        lease = self._lease()
        t = time.time()
        assert lease.is_valid("d", issued_at=t, now=t) is True

    def test_is_valid_false_for_expired(self) -> None:
        lease = self._lease()
        t = 1_700_000_000.0
        old = t - 48 * 3600
        assert lease.is_valid("d", issued_at=old, now=t) is False

    def test_is_valid_false_for_missing(self) -> None:
        lease = self._lease()
        assert lease.is_valid("d", issued_at=None) is False

    def test_needs_reenrollment_true_for_expired(self) -> None:
        lease = self._lease()
        t = 1_700_000_000.0
        old = t - 48 * 3600
        assert lease.needs_reenrollment("d", issued_at=old, now=t) is True

    def test_needs_reenrollment_true_for_missing(self) -> None:
        lease = self._lease()
        assert lease.needs_reenrollment("d", issued_at=None) is True

    def test_needs_reenrollment_false_for_valid(self) -> None:
        lease = self._lease()
        t = time.time()
        assert lease.needs_reenrollment("d", issued_at=t, now=t) is False


# ---------------------------------------------------------------------------
# Custom lease_hours constructor
# ---------------------------------------------------------------------------


class TestCustomLeaseHours:
    def test_one_hour_lease_expires_correctly(self) -> None:
        lease = DeviceLease(lease_hours=1)
        assert lease.lease_seconds == 3600.0

        t = 1_700_000_000.0
        # 59 minutes in — still valid
        assert lease.check("d", issued_at=t, now=t + 59 * 60) is LeaseStatus.VALID
        # 1 hour + skew + 1s — expired
        expired_at = t + 3600 + lease.clock_skew_seconds + 1
        assert lease.check("d", issued_at=t, now=expired_at) is LeaseStatus.EXPIRED

    def test_fractional_lease_hours(self) -> None:
        """0.5 hours = 30 minutes."""
        lease = DeviceLease(lease_hours=0.5)
        assert lease.lease_seconds == 1800.0

        t = 1_700_000_000.0
        assert lease.check("d", issued_at=t, now=t + 29 * 60) is LeaseStatus.VALID
        expired_at = t + 1800 + lease.clock_skew_seconds + 1
        assert lease.check("d", issued_at=t, now=expired_at) is LeaseStatus.EXPIRED


# ---------------------------------------------------------------------------
# Custom clock_skew_seconds constructor
# ---------------------------------------------------------------------------


class TestCustomClockSkew:
    def test_zero_skew_expires_immediately_after_lease(self) -> None:
        lease = DeviceLease(lease_hours=1, clock_skew_seconds=0)
        t = 1_700_000_000.0
        # exactly at expiry — boundary is now <= expiry, not <
        assert lease.check("d", issued_at=t, now=t + 3600) is LeaseStatus.VALID
        # one second past — expired with no tolerance
        assert lease.check("d", issued_at=t, now=t + 3601) is LeaseStatus.EXPIRED

    def test_large_skew_extends_window(self) -> None:
        """A 1-hour skew accepts credentials expired up to 1 hour ago."""
        one_hour = 3600
        lease = DeviceLease(lease_hours=24, clock_skew_seconds=one_hour)
        t = 1_700_000_000.0
        raw_expiry = t + 24 * 3600
        # 59 minutes past raw expiry — inside 1-hour skew
        assert lease.check("d", issued_at=t, now=raw_expiry + 59 * 60) is LeaseStatus.VALID
        # 61 minutes past raw expiry — outside 1-hour skew
        assert lease.check("d", issued_at=t, now=raw_expiry + 61 * 60) is LeaseStatus.EXPIRED


# ---------------------------------------------------------------------------
# Environment variable configuration
# ---------------------------------------------------------------------------


class TestEnvVar:
    def test_env_var_sets_lease_hours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEPTHFUSION_DEVICE_LEASE_HOURS", "48")
        lease = DeviceLease()
        assert lease.lease_seconds == 48 * 3600

    def test_env_var_fractional(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEPTHFUSION_DEVICE_LEASE_HOURS", "0.5")
        lease = DeviceLease()
        assert lease.lease_seconds == 0.5 * 3600

    def test_env_var_invalid_string_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEPTHFUSION_DEVICE_LEASE_HOURS", "not-a-number")
        lease = DeviceLease()
        assert lease.lease_seconds == DEFAULT_LEASE_HOURS * 3600

    def test_env_var_empty_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEPTHFUSION_DEVICE_LEASE_HOURS", "")
        lease = DeviceLease()
        assert lease.lease_seconds == DEFAULT_LEASE_HOURS * 3600

    def test_env_var_zero_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEPTHFUSION_DEVICE_LEASE_HOURS", "0")
        lease = DeviceLease()
        assert lease.lease_seconds == DEFAULT_LEASE_HOURS * 3600

    def test_env_var_negative_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEPTHFUSION_DEVICE_LEASE_HOURS", "-5")
        lease = DeviceLease()
        assert lease.lease_seconds == DEFAULT_LEASE_HOURS * 3600

    def test_env_var_absent_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DEPTHFUSION_DEVICE_LEASE_HOURS", raising=False)
        lease = DeviceLease()
        assert lease.lease_seconds == DEFAULT_LEASE_HOURS * 3600

    def test_explicit_constructor_arg_overrides_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """lease_hours kwarg beats the env var."""
        monkeypatch.setenv("DEPTHFUSION_DEVICE_LEASE_HOURS", "99")
        lease = DeviceLease(lease_hours=12)
        assert lease.lease_seconds == 12 * 3600


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_lease_seconds_property(self) -> None:
        lease = DeviceLease(lease_hours=6)
        assert lease.lease_seconds == 6 * 3600

    def test_clock_skew_seconds_property(self) -> None:
        lease = DeviceLease(clock_skew_seconds=120)
        assert lease.clock_skew_seconds == 120
