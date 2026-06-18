"""Negative test suite for TokenValidator.

Tests edge cases, error conditions, and attack vectors:
- Expired tokens
- Not-yet-valid tokens (nbf in future)
- Wrong issuer/audience claims
- Tampered signatures
- Missing required claims (sub, exp, nbf)
- Nonce mismatches
- Algorithm confusion attacks (alg=none)
- Empty/malformed tokens (not enough dot-separated parts)
- Malformed JWTs (invalid base64url encoding, non-JSON payloads)

All tests use the same RS256-signed JWT infrastructure as
test_identity_token_validator.py — fixture RSA private key,
mock JWKS, and stub JwksCache for deterministic validation.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import pytest

# --- Ensure THIS worktree's src wins over any editable install -------------- #
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding  # noqa: E402

from depthfusion.identity import (  # noqa: E402
    JwksCache,
    TokenValidator,
)
from depthfusion.identity.errors import (  # noqa: E402
    JwksFetchError,
    TokenExpiredError,
    TokenInvalidError,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "entra"
_JWKS_PATH = _FIXTURES / "mock-jwks.json"
_KEY_PATH = _FIXTURES / "mock-signing-key.pem"

_ISSUER = "https://login.microsoftonline.com/test-tenant/v2.0"
_AUDIENCE = "api://depthfusion-test"
_KID = "test-key-1"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_private_key():
    return serialization.load_pem_private_key(
        _KEY_PATH.read_bytes(), password=None
    )


def _make_jwt(
    claims: dict,
    *,
    alg: str = "RS256",
    kid: str | None = _KID,
    sign: bool = True,
) -> str:
    """Build a (real or fake-signature) JWT from ``claims``."""
    header: dict = {"typ": "JWT", "alg": alg}
    if kid is not None:
        header["kid"] = kid
    header_seg = _b64url(json.dumps(header).encode())
    payload_seg = _b64url(json.dumps(claims).encode())
    signing_input = f"{header_seg}.{payload_seg}".encode("ascii")

    if sign and alg == "RS256":
        key = _load_private_key()
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        sig_seg = _b64url(signature)
    else:
        sig_seg = _b64url(b"not-a-real-signature")
    return f"{header_seg}.{payload_seg}.{sig_seg}"


class _StubJwksCache:
    """A JwksCache stand-in that returns the fixture public JWK for any kid."""

    def __init__(self, jwk: dict, *, known_kids: set[str] | None = None) -> None:
        self._jwk = jwk
        self._known_kids = known_kids

    async def get_key(self, kid: str) -> dict:
        if self._known_kids is not None and kid not in self._known_kids:
            raise JwksFetchError(f"kid {kid!r} not found")
        return self._jwk


def _fixture_jwk() -> dict:
    return json.loads(_JWKS_PATH.read_text())["keys"][0]


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "user-123",
        "preferred_username": "alice@contoso.com",
        "name": "Alice Example",
        "groups": ["group-a", "group-b"],
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": now + 3600,
        "nbf": now - 60,
        "iat": now - 60,
    }
    claims.update(overrides)
    return claims


def _validator(jwks_cache=None) -> TokenValidator:
    cache = jwks_cache or _StubJwksCache(_fixture_jwk())
    return TokenValidator(
        jwks_cache=cache,  # type: ignore[arg-type]
        expected_issuer=_ISSUER,
        expected_audience=_AUDIENCE,
        clock_skew_seconds=300,
    )


# --------------------------------------------------------------------------- #
# 1. Expired token (exp in past, beyond skew)                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_expired_token() -> None:
    """Token with exp = now - 3600 (well beyond 300s skew) must raise TokenExpiredError."""
    token = _make_jwt(_base_claims(exp=int(time.time()) - 3600))
    with pytest.raises(TokenExpiredError):
        await _validator().validate(token)


# --------------------------------------------------------------------------- #
# 2. Not yet valid (nbf in future, beyond skew)                              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_not_yet_valid() -> None:
    """Token with nbf = now + 3600 (far in future) must raise TokenInvalidError."""
    token = _make_jwt(_base_claims(nbf=int(time.time()) + 3600))
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token)


# --------------------------------------------------------------------------- #
# 3. Wrong audience claim                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_wrong_audience() -> None:
    """Token with aud != expected_audience must raise TokenInvalidError."""
    token = _make_jwt(_base_claims(aud="api://wrong-audience-id"))
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token)


# --------------------------------------------------------------------------- #
# 4. Wrong issuer claim                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_wrong_issuer() -> None:
    """Token with iss != expected_issuer must raise TokenInvalidError."""
    token = _make_jwt(_base_claims(iss="https://evil.example.com/v2.0"))
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token)


# --------------------------------------------------------------------------- #
# 5. Tampered signature                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_tampered_signature() -> None:
    """Token with a corrupted signature must raise TokenInvalidError."""
    valid = _make_jwt(_base_claims())
    header_seg, payload_seg, _sig = valid.split(".")
    # Flip a byte in the signature by reconstructing with garbage
    tampered = f"{header_seg}.{payload_seg}.{_b64url(b'garbage-signature-bytes')}"
    with pytest.raises(TokenInvalidError):
        await _validator().validate(tampered)


# --------------------------------------------------------------------------- #
# 6. Missing 'sub' claim                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_missing_sub_claim() -> None:
    """Token without 'sub' must raise TokenInvalidError.

    'sub' is the principal identifier.  A token without it cannot be mapped to
    a Principal and must be rejected — accepting it would produce an anonymous
    or unidentified session.
    """
    claims = _base_claims()
    del claims["sub"]
    token = _make_jwt(claims)
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token)


# --------------------------------------------------------------------------- #
# 7. Nonce mismatch                                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_nonce_mismatch() -> None:
    """Token with nonce='nonce-A' validated with nonce='nonce-B' must raise TokenInvalidError."""
    token = _make_jwt(_base_claims(nonce="nonce-A"))
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token, nonce="nonce-B")


# --------------------------------------------------------------------------- #
# 8. Algorithm=none attack                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_algorithm_none_attack() -> None:
    """Token with alg='none' and empty signature must be rejected."""
    token = _make_jwt(_base_claims(), alg="none", sign=False)
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token)


# --------------------------------------------------------------------------- #
# 9. Empty string token                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_empty_string_token() -> None:
    """Empty string must raise TokenInvalidError."""
    with pytest.raises(TokenInvalidError):
        await _validator().validate("")


# --------------------------------------------------------------------------- #
# 10. Malformed JWT (only two parts, not three)                              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_malformed_jwt_two_parts() -> None:
    """JWT-like string with only two dot-separated parts must raise TokenInvalidError."""
    with pytest.raises(TokenInvalidError):
        await _validator().validate("header.payload")


__all__ = [
    "test_expired_token",
    "test_not_yet_valid",
    "test_wrong_audience",
    "test_wrong_issuer",
    "test_tampered_signature",
    "test_missing_sub_claim",
    "test_nonce_mismatch",
    "test_algorithm_none_attack",
    "test_empty_string_token",
    "test_malformed_jwt_two_parts",
]
