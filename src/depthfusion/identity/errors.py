"""Identity / OIDC error hierarchy.

All errors raised by the :mod:`depthfusion.identity` package derive from
:class:`IdentityError` so callers can catch the whole family with a single
``except IdentityError``.

The hierarchy intentionally distinguishes *token* problems (expired, invalid
signature/claims) from *transport* problems (JWKS fetch) and *flow* problems
(OIDC authorization-code / device-code exchange) so the MCP/API layer can map
each to an appropriate response without string matching on messages.
"""
from __future__ import annotations


class IdentityError(Exception):
    """Base class for every error raised by the identity package."""


class TokenExpiredError(IdentityError):
    """The JWT's ``exp`` claim is in the past (allowing for clock skew)."""


class TokenInvalidError(IdentityError):
    """The JWT failed a structural, signature, or claim validation check.

    Raised for a wrong issuer/audience, a nonce mismatch, an unexpected
    algorithm, a malformed token, or a failed signature verification.
    """


class JwksFetchError(IdentityError):
    """The signing-key set could not be fetched or contained no usable key.

    Raised when the JWKS endpoint is unreachable, returns a non-2xx status,
    returns malformed JSON, is misconfigured (e.g. ``DEPTHFUSION_JWKS_URI``
    unset), or does not contain the requested ``kid`` even after a refresh.
    """


class OidcFlowError(IdentityError):
    """An OIDC authorization-code or device-code flow step failed.

    Raised when the token endpoint returns an error, the device-code poll
    times out, or the provider reports ``authorization_declined`` /
    ``expired_token`` / ``access_denied``.
    """


__all__ = [
    "IdentityError",
    "TokenExpiredError",
    "TokenInvalidError",
    "JwksFetchError",
    "OidcFlowError",
]
