"""Legacy-token compatibility shim for DepthFusion V2.

When the environment variable ``DEPTHFUSION_V2_LEGACY_AUTH=1`` is set, this
module allows callers to present the old ``DEPTHFUSION_API_TOKEN`` static
token in the ``Authorization: Bearer <token>`` header.  A matching token is
mapped to a :class:`~depthfusion.identity.models.Principal`-compatible
:class:`LegacyPrincipal` object, and a deprecation warning is logged on every
use via ``structlog``.

The shim is intentionally minimal:

* It is **opt-in**: do nothing unless ``DEPTHFUSION_V2_LEGACY_AUTH=1``.
* It provides **no capability elevation**: the resulting principal carries a
  fixed ``legacy:token`` principal id and belongs to no groups.
* Every authentication attempt that uses the legacy path is logged at
  ``warning`` level so operators can track and remove legacy consumers.

Usage
-----
Construct a :class:`LegacyTokenShim` with the expected static token value and
call :meth:`~LegacyTokenShim.authenticate` with the raw Bearer token string.
It returns a :class:`LegacyPrincipal` on success, or ``None`` if the shim is
disabled or the token does not match.

.. code-block:: python

    import os
    from depthfusion.identity.legacy_shim import LegacyTokenShim

    shim = LegacyTokenShim.from_env()
    principal = shim.authenticate(raw_bearer_token)
    if principal is None:
        # shim disabled or token mismatch — fall through to normal auth
        ...
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

import structlog

from .models import Principal

_ENV_ENABLE = "DEPTHFUSION_V2_LEGACY_AUTH"
_ENV_TOKEN = "DEPTHFUSION_API_TOKEN"

_log = structlog.get_logger(__name__)


@dataclass
class LegacyPrincipal(Principal):
    """A :class:`~depthfusion.identity.models.Principal` produced by the legacy-token shim.

    This is a thin subclass whose sole purpose is to be identifiable as
    originating from the legacy path — callers can branch on
    ``isinstance(principal, LegacyPrincipal)`` when they need to.

    All fields are inherited from :class:`~depthfusion.identity.models.Principal`.
    The constructor populates them with sensible defaults; callers should not
    need to override them.
    """

    # Inherits all fields from Principal; no additional fields.
    # Mark it explicitly so ``isinstance`` checks work.

    def __post_init__(self) -> None:  # noqa: D105
        # Nothing extra to initialise; present so subclass works cleanly as a
        # dataclass without surprising MRO behaviour.
        pass


class LegacyTokenShim:
    """Validate legacy ``DEPTHFUSION_API_TOKEN`` credentials.

    Parameters
    ----------
    expected_token:
        The raw token string that this shim will accept.  Must be non-empty.
    enabled:
        When ``False`` (default when ``DEPTHFUSION_V2_LEGACY_AUTH`` is not
        ``"1"``), :meth:`authenticate` always returns ``None`` without logging.
    """

    def __init__(self, expected_token: str, *, enabled: bool) -> None:
        if not expected_token:
            raise ValueError("expected_token must be a non-empty string")
        self._expected_token = expected_token
        self._enabled = enabled
        # Pre-compute a digest so comparison is constant-time and the raw
        # token is not stored in memory beyond initialisation.
        self._expected_digest = self._digest(expected_token)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "LegacyTokenShim":
        """Construct a shim from environment variables.

        Reads:

        * ``DEPTHFUSION_V2_LEGACY_AUTH`` — set to ``"1"`` to enable the shim.
        * ``DEPTHFUSION_API_TOKEN`` — the legacy token to accept (required
          when the shim is enabled; if absent, the shim is silently disabled).

        Returns
        -------
        LegacyTokenShim
            A shim instance.  If either env var is missing or the feature is
            not enabled, the shim is disabled and :meth:`authenticate` will
            always return ``None``.
        """
        enabled = os.environ.get(_ENV_ENABLE, "").strip() == "1"
        raw_token = os.environ.get(_ENV_TOKEN, "").strip()

        if enabled and not raw_token:
            _log.warning(
                "legacy_shim.misconfigured",
                reason=f"{_ENV_TOKEN} is not set but {_ENV_ENABLE}=1; shim disabled",
            )
            enabled = False

        if not raw_token:
            # Need a non-empty placeholder so the constructor doesn't raise.
            raw_token = "disabled-placeholder"

        return cls(raw_token, enabled=enabled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def authenticate(self, bearer_token: str) -> LegacyPrincipal | None:
        """Attempt to authenticate ``bearer_token`` via the legacy path.

        If the shim is disabled or the token does not match, returns ``None``
        immediately (no logging in the disabled case).

        If the token matches, emits a ``warning``-level deprecation log entry
        on **every call** so operators can identify and migrate legacy
        consumers, then returns a :class:`LegacyPrincipal`.

        Parameters
        ----------
        bearer_token:
            The raw value from the ``Authorization: Bearer <token>`` header.

        Returns
        -------
        LegacyPrincipal | None
            A principal on success, ``None`` when the shim cannot authenticate
            the token.
        """
        if not self._enabled:
            return None

        if not bearer_token or not isinstance(bearer_token, str):
            return None

        # Constant-time comparison to avoid timing oracles.
        if not hmac.compare_digest(self._digest(bearer_token), self._expected_digest):
            return None

        _log.warning(
            "legacy_shim.deprecated_token_used",
            message=(
                "A request was authenticated using the legacy DEPTHFUSION_API_TOKEN. "
                "This authentication path is deprecated and will be removed in a future "
                "release.  Please migrate to OIDC / device-code authentication."
            ),
            principal_id="legacy:token",
        )

        return LegacyPrincipal(
            principal_id="legacy:token",
            upn="",
            display_name="Legacy API Token",
            groups=[],
            device_id=None,
            access_token=None,
            id_token=None,
            expires_at=None,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """``True`` when the shim is active and will accept the legacy token."""
        return self._enabled

    @staticmethod
    def _digest(value: str) -> bytes:
        """Return a SHA-256 digest of ``value`` for constant-time comparison."""
        return hashlib.sha256(value.encode()).digest()


__all__ = ["LegacyPrincipal", "LegacyTokenShim"]
