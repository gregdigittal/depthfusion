"""Data models for the identity package.

``Principal`` is the authenticated-user record produced once an OIDC token has
been validated.  ``DeviceCodeResult`` is the provider response that begins a
device-authorization flow (RFC 8628).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Principal:
    """An authenticated caller, derived from validated OIDC token claims.

    Attributes
    ----------
    principal_id:
        Stable, unique subject identifier — the ``sub`` claim.
    upn:
        User principal name — the ``preferred_username`` claim (often the
        e-mail / UPN in Entra ID).
    display_name:
        Human-readable name — the ``name`` claim.
    groups:
        Group object-ids / names the principal belongs to (``groups`` claim).
        Defaults to an empty list.
    device_id:
        Identifier of the device the principal authenticated from, if known.
    access_token:
        The raw access token (opaque to consumers; used for downstream calls).
    id_token:
        The raw ID token (the JWT that was validated).
    expires_at:
        Unix timestamp (seconds, float) at which the access token expires.
    """

    principal_id: str
    upn: str = ""
    display_name: str = ""
    groups: list[str] = field(default_factory=list)
    device_id: str | None = None
    access_token: str | None = None
    id_token: str | None = None
    expires_at: float | None = None


@dataclass
class DeviceCodeResult:
    """Provider response that initiates the device-authorization flow.

    Mirrors the RFC 8628 device-authorization response.

    Attributes
    ----------
    device_code:
        Opaque code the client polls the token endpoint with.
    user_code:
        Short code the user types at ``verification_uri``.
    verification_uri:
        URL the user visits to enter ``user_code``.
    expires_in:
        Lifetime of ``device_code`` / ``user_code`` in seconds.
    interval:
        Minimum seconds the client must wait between poll attempts.
    verification_uri_complete:
        Optional URL pre-filled with the user code (RFC 8628 §3.2).
    message:
        Optional human-readable instruction returned by the provider.
    """

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int = 5
    verification_uri_complete: str | None = None
    message: str | None = None


__all__ = ["Principal", "DeviceCodeResult"]
