"""Lease-based device credential refresh.

Each device credential has a finite *lease* — a window during which the
credential is considered valid without re-enrollment.  The default lease
is 24 hours but can be overridden via the ``DEPTHFUSION_DEVICE_LEASE_HOURS``
environment variable.

A 5-minute clock-skew tolerance is applied: a credential that expired up to
5 minutes ago is still accepted, and a credential whose lease has not yet
technically begun (system clock drift) is also tolerated.

Typical usage::

    from depthfusion.identity.device_lease import DeviceLease, LeaseStatus

    lease = DeviceLease()

    status = lease.check("device-id-abc", issued_at=<unix_ts>)
    if status == LeaseStatus.EXPIRED:
        # trigger re-enrollment
        ...
    elif status == LeaseStatus.VALID:
        # proceed normally
        ...
"""
from __future__ import annotations

import os
import time
from enum import Enum

__all__ = [
    "DeviceLease",
    "LeaseStatus",
    "DEFAULT_LEASE_HOURS",
    "CLOCK_SKEW_TOLERANCE_SECONDS",
]

DEFAULT_LEASE_HOURS: int = 24
CLOCK_SKEW_TOLERANCE_SECONDS: int = 5 * 60  # 5 minutes


class LeaseStatus(str, Enum):
    """Result of a lease validity check.

    Attributes
    ----------
    VALID:
        The credential is within its lease window (including clock-skew
        tolerance on both ends).
    EXPIRED:
        The credential's lease has elapsed beyond the clock-skew tolerance;
        re-enrollment is required.
    MISSING:
        No ``issued_at`` timestamp was supplied (``None``); the credential
        has never been issued and re-enrollment is required.
    """

    VALID = "valid"
    EXPIRED = "expired"
    MISSING = "missing"


class DeviceLease:
    """Evaluates whether a device credential is within its lease window.

    Parameters
    ----------
    lease_hours:
        Duration of a single lease in hours.  Reads
        ``DEPTHFUSION_DEVICE_LEASE_HOURS`` from the environment and falls back
        to :data:`DEFAULT_LEASE_HOURS` (24) when the variable is absent or
        non-numeric.
    clock_skew_seconds:
        Clock-skew tolerance in seconds applied on *both* ends of the lease
        window.  Defaults to :data:`CLOCK_SKEW_TOLERANCE_SECONDS` (300 s /
        5 min).

    Examples
    --------
    >>> lease = DeviceLease()
    >>> import time
    >>> status = lease.check("dev-123", issued_at=time.time())
    >>> status == LeaseStatus.VALID
    True
    """

    def __init__(
        self,
        *,
        lease_hours: float | None = None,
        clock_skew_seconds: int = CLOCK_SKEW_TOLERANCE_SECONDS,
    ) -> None:
        if lease_hours is None:
            lease_hours = _read_lease_hours_from_env()
        self._lease_seconds: float = lease_hours * 3600
        self._skew: int = clock_skew_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def lease_seconds(self) -> float:
        """The configured lease duration in seconds."""
        return self._lease_seconds

    @property
    def clock_skew_seconds(self) -> int:
        """The clock-skew tolerance in seconds."""
        return self._skew

    def check(
        self,
        device_id: str,
        *,
        issued_at: float | None,
        now: float | None = None,
    ) -> LeaseStatus:
        """Check whether a device credential is within its lease window.

        Parameters
        ----------
        device_id:
            The device identifier being checked (used for logging only; not
            stored by this method).
        issued_at:
            Unix timestamp (seconds, float) when the credential was issued.
            Pass ``None`` to indicate that the credential has never been
            issued — this always returns :attr:`LeaseStatus.MISSING`.
        now:
            Override the current time (Unix timestamp, float).  Defaults to
            :func:`time.time()` when ``None``.  Useful for testing.

        Returns
        -------
        LeaseStatus
            :attr:`LeaseStatus.MISSING` when ``issued_at`` is ``None``.
            :attr:`LeaseStatus.EXPIRED` when the credential's lease has
            elapsed (plus the clock-skew tolerance).
            :attr:`LeaseStatus.VALID` otherwise.
        """
        if issued_at is None:
            return LeaseStatus.MISSING

        if now is None:
            now = time.time()

        # The lease expires at issued_at + lease_seconds.
        # We extend the expiry by the clock-skew tolerance so a credential
        # that expired *just* recently is still accepted.
        expiry = issued_at + self._lease_seconds + self._skew
        if now > expiry:
            return LeaseStatus.EXPIRED

        return LeaseStatus.VALID

    def is_valid(
        self,
        device_id: str,
        *,
        issued_at: float | None,
        now: float | None = None,
    ) -> bool:
        """Convenience wrapper — returns ``True`` only for :attr:`LeaseStatus.VALID`.

        Parameters
        ----------
        device_id:
            Passed through to :meth:`check`.
        issued_at:
            Passed through to :meth:`check`.
        now:
            Passed through to :meth:`check`.
        """
        return self.check(device_id, issued_at=issued_at, now=now) is LeaseStatus.VALID

    def needs_reenrollment(
        self,
        device_id: str,
        *,
        issued_at: float | None,
        now: float | None = None,
    ) -> bool:
        """Return ``True`` when the device must re-enroll.

        A device needs re-enrollment when its credential is either
        :attr:`LeaseStatus.MISSING` or :attr:`LeaseStatus.EXPIRED`.

        Parameters
        ----------
        device_id:
            Passed through to :meth:`check`.
        issued_at:
            Passed through to :meth:`check`.
        now:
            Passed through to :meth:`check`.
        """
        return self.check(device_id, issued_at=issued_at, now=now) is not LeaseStatus.VALID


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_lease_hours_from_env() -> float:
    """Read ``DEPTHFUSION_DEVICE_LEASE_HOURS`` from the environment.

    Returns :data:`DEFAULT_LEASE_HOURS` when the variable is absent, empty,
    or cannot be parsed as a positive float.
    """
    raw = os.environ.get("DEPTHFUSION_DEVICE_LEASE_HOURS", "").strip()
    if not raw:
        return float(DEFAULT_LEASE_HOURS)
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_LEASE_HOURS)
    if value <= 0:
        return float(DEFAULT_LEASE_HOURS)
    return value
