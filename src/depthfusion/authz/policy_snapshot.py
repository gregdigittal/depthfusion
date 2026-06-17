"""Server-signed offline policy snapshot (E-50 S-191, T-662).

When the device is **offline**, the central policy decision point cannot reach
the server to re-derive the authoritative classification policy. A naïve
implementation would let the device evaluate against whatever copy of the
policy happens to sit on disk — which is *forgeable*: an attacker who can edit
the encrypted cache could widen ``allowed_roles`` for ``restricted`` data and
self-grant access while offline.

This module closes that hole. The server emits a **signed policy snapshot**:

* a *versioned*, *expiring* capture of the classification policy
  (the role × classification access rules used by the policy engine), plus
* an HMAC-SHA256 signature over the canonical serialisation, keyed by a secret
  the device never sees in plaintext beyond what the keyring holds.

The snapshot is embedded in the encrypted offline cache. At offline-evaluation
time the policy engine **loads and verifies** the snapshot before trusting it:

* a *tampered* snapshot (signature mismatch) → refused → deny.
* an *unsigned* / missing-signature snapshot → refused → deny.
* an *expired* snapshot → refused → deny.
* a *valid* snapshot → its policy is used, yielding the *same* decision the
  online path would (per S-191 AC-3).

Security rules
--------------
* The signing key is sourced from ``process.env`` (``DF_POLICY_SNAPSHOT_KEY``)
  or supplied explicitly by the keyring-backed caller — **never hardcoded**.
* Signature comparison uses :func:`hmac.compare_digest` (constant-time).
* The canonical serialisation is order-independent (levels + roles are sorted)
  so a re-ordering on disk is not a false-positive tamper, but any value change
  is detected.
* Fallback is always **deny**, never allow — an unverifiable policy must never
  widen access.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Sequence

import structlog

from depthfusion.authz.classification import CLASSIFICATION_POLICY

log = structlog.get_logger(__name__)

__all__ = [
    "SNAPSHOT_KEY_ENV",
    "DEFAULT_SNAPSHOT_TTL_SECONDS",
    "SnapshotVerification",
    "PolicySnapshotError",
    "SignedPolicySnapshot",
    "sign_policy_snapshot",
    "verify_policy_snapshot",
    "current_classification_policy_payload",
]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

#: Environment variable holding the snapshot signing key (hex or raw bytes).
SNAPSHOT_KEY_ENV = "DF_POLICY_SNAPSHOT_KEY"

#: Default validity window for a snapshot: 7 days. Offline devices re-sync (and
#: receive a fresh snapshot) at least this often; an older snapshot is refused.
DEFAULT_SNAPSHOT_TTL_SECONDS: int = 7 * 24 * 3600


class SnapshotVerification(str, Enum):
    """Outcome of verifying a snapshot's signature + freshness."""

    OK = "ok"
    """Signature matched and the snapshot has not expired — trust it."""

    UNSIGNED = "unsigned"
    """No signature present — refuse (deny)."""

    TAMPERED = "tampered"
    """Signature mismatch — the snapshot was altered — refuse (deny)."""

    EXPIRED = "expired"
    """Snapshot is past its ``expires_at`` — refuse (deny)."""


class PolicySnapshotError(RuntimeError):
    """Raised when a signing key cannot be resolved."""


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


def _coerce_key(key: Optional[bytes | str]) -> bytes:
    """Resolve the signing key from an explicit arg or the environment.

    Order: explicit *key* → ``DF_POLICY_SNAPSHOT_KEY`` env var. A hex string is
    decoded to bytes when it is valid hex; otherwise the raw UTF-8 bytes are
    used. Never logs the key.
    """
    raw = key if key is not None else os.environ.get(SNAPSHOT_KEY_ENV)
    if not raw:
        raise PolicySnapshotError(
            "No policy-snapshot signing key available "
            f"(set {SNAPSHOT_KEY_ENV} or pass key=...)."
        )
    if isinstance(raw, bytes):
        return raw
    # Try hex first (server keys are typically hex-encoded); fall back to UTF-8.
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return raw.encode("utf-8")


# ---------------------------------------------------------------------------
# Canonical policy payload
# ---------------------------------------------------------------------------


def current_classification_policy_payload() -> dict[str, list[str]]:
    """Capture the live classification policy as a JSON-safe payload.

    The payload maps each classification level (string) to the sorted list of
    role values permitted at that level — exactly the ``allowed_roles`` the
    policy engine consults in its classification check. Sorting makes the
    capture deterministic so two captures of the same policy sign identically.
    """
    payload: dict[str, list[str]] = {}
    for level, rules in CLASSIFICATION_POLICY.items():
        payload[level.value] = sorted(r.value for r in rules["allowed_roles"])
    return payload


def _canonical_bytes(
    version: int,
    issued_at: float,
    expires_at: float,
    policy: Mapping[str, Sequence[str]],
) -> bytes:
    """Deterministically serialise the snapshot body for signing.

    Order-independent: keys are sorted and each role list is sorted, so an
    on-disk re-ordering is not a tamper, but any value change (a widened
    ``allowed_roles``, a changed expiry, a bumped version) flips the digest.
    """
    canonical_policy = {
        level: sorted(roles) for level, roles in policy.items()
    }
    body = {
        "version": version,
        "issued_at": round(float(issued_at), 3),
        "expires_at": round(float(expires_at), 3),
        "policy": canonical_policy,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Signed snapshot value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignedPolicySnapshot:
    """A versioned, signed capture of the classification policy.

    Attributes
    ----------
    version:
        Monotonic policy version. A higher version supersedes a lower one.
    issued_at:
        Unix timestamp (seconds) when the server signed the snapshot.
    expires_at:
        Unix timestamp (seconds) after which the snapshot is refused.
    policy:
        ``{classification_level: [allowed_role_value, ...]}`` — the role ×
        classification access rules captured at signing time.
    signature:
        Hex HMAC-SHA256 over the canonical body. Empty string == unsigned.
    """

    version: int
    issued_at: float
    expires_at: float
    policy: dict[str, list[str]]
    signature: str = ""

    # -- serialisation (for embedding in the encrypted cache) ----------------

    def to_dict(self) -> dict:
        """Return a JSON-safe dict suitable for cache embedding."""
        return {
            "version": self.version,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "policy": {k: list(v) for k, v in self.policy.items()},
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "SignedPolicySnapshot":
        """Reconstruct a snapshot from its embedded dict form.

        Missing / malformed fields degrade to values that *fail* verification
        (e.g. empty signature → ``UNSIGNED``) rather than raising — the engine
        treats any unverifiable snapshot as deny.
        """
        raw_policy = data.get("policy") or {}
        policy = {
            str(level): [str(r) for r in (roles or [])]
            for level, roles in raw_policy.items()
        }
        return cls(
            version=int(data.get("version", 0)),
            issued_at=float(data.get("issued_at", 0.0)),
            expires_at=float(data.get("expires_at", 0.0)),
            policy=policy,
            signature=str(data.get("signature", "")),
        )

    def allowed_roles_for(self, classification: str) -> Optional[list[str]]:
        """Return the allowed role values for *classification*, or ``None``.

        ``None`` means the level is absent from the snapshot (caller denies).
        """
        return self.policy.get(classification)


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def sign_policy_snapshot(
    *,
    version: int,
    key: Optional[bytes | str] = None,
    policy: Optional[Mapping[str, Sequence[str]]] = None,
    now: Optional[float] = None,
    ttl_seconds: int = DEFAULT_SNAPSHOT_TTL_SECONDS,
) -> SignedPolicySnapshot:
    """Produce a server-signed, versioned, expiring policy snapshot.

    Parameters
    ----------
    version:
        Monotonic policy version stamped into the signed body.
    key:
        Signing key. Defaults to the ``DF_POLICY_SNAPSHOT_KEY`` environment
        variable. Raises :class:`PolicySnapshotError` if neither is set.
    policy:
        The role × classification mapping to capture. Defaults to the live
        :data:`CLASSIFICATION_POLICY` via
        :func:`current_classification_policy_payload`.
    now:
        Issue instant (Unix seconds). Defaults to :func:`time.time`.
    ttl_seconds:
        Validity window. ``expires_at = issued_at + ttl_seconds``.
    """
    signing_key = _coerce_key(key)
    issued = now if now is not None else time.time()
    expires = issued + max(0, ttl_seconds)
    body_policy = (
        dict(policy) if policy is not None
        else current_classification_policy_payload()
    )
    canonical = _canonical_bytes(version, issued, expires, body_policy)
    signature = hmac.new(signing_key, canonical, hashlib.sha256).hexdigest()
    log.info("policy_snapshot.signed", version=version, expires_at=expires)
    return SignedPolicySnapshot(
        version=version,
        issued_at=issued,
        expires_at=expires,
        policy={k: sorted(v) for k, v in body_policy.items()},
        signature=signature,
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_policy_snapshot(
    snapshot: SignedPolicySnapshot,
    *,
    key: Optional[bytes | str] = None,
    now: Optional[float] = None,
) -> SnapshotVerification:
    """Verify a snapshot's signature and freshness.

    Returns :attr:`SnapshotVerification.OK` only when the signature is present,
    matches a freshly-computed HMAC over the canonical body, **and** the
    snapshot has not expired. Every other outcome is a refusal the caller must
    treat as deny:

    * empty signature                → :attr:`SnapshotVerification.UNSIGNED`
    * signature mismatch             → :attr:`SnapshotVerification.TAMPERED`
    * past ``expires_at``            → :attr:`SnapshotVerification.EXPIRED`

    The signature check runs *before* the expiry check so a tampered expiry
    cannot masquerade as a benign "expired" outcome.
    """
    if not snapshot.signature:
        return SnapshotVerification.UNSIGNED

    try:
        signing_key = _coerce_key(key)
    except PolicySnapshotError:
        # No key → we cannot trust anything → treat as tampered (deny).
        log.warning("policy_snapshot.no_verify_key")
        return SnapshotVerification.TAMPERED

    canonical = _canonical_bytes(
        snapshot.version,
        snapshot.issued_at,
        snapshot.expires_at,
        snapshot.policy,
    )
    expected = hmac.new(signing_key, canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, snapshot.signature):
        log.warning("policy_snapshot.tampered", version=snapshot.version)
        return SnapshotVerification.TAMPERED

    moment = now if now is not None else time.time()
    if moment >= snapshot.expires_at:
        log.warning("policy_snapshot.expired", version=snapshot.version)
        return SnapshotVerification.EXPIRED

    return SnapshotVerification.OK
