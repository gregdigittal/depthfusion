"""RS256 JWT validation against a JWKS cache.

Signature verification uses the ``cryptography`` library directly (RSA + SHA-256
PKCS#1 v1.5) rather than delegating to PyJWT, so the validation logic — header
algorithm pinning, ``exp``/``nbf`` skew handling, issuer/audience/nonce checks —
is explicit and auditable in one place.

Security properties enforced
----------------------------
* ``alg`` MUST be ``RS256``. ``none`` and HMAC algorithms are rejected, closing
  the classic algorithm-confusion bypass.
* The signing key is selected by the token's ``kid`` and fetched through the
  :class:`~depthfusion.identity.jwks_cache.JwksCache` (no key material is taken
  from the token itself).
* ``exp`` (with ``+skew``) and ``nbf`` (with ``-skew``) are enforced.
* ``iss`` and ``aud`` must match the configured expectations.
* ``nonce`` is checked when provided by the caller (replay protection for the
  authorization-code flow).
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .errors import JwksFetchError, TokenExpiredError, TokenInvalidError
from .jwks_cache import JwksCache

_EXPECTED_ALG = "RS256"
_DEFAULT_CLOCK_SKEW_SECONDS = 300


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url segment, tolerating missing padding."""
    padding_needed = (-len(segment)) % 4
    try:
        return base64.urlsafe_b64decode(segment + ("=" * padding_needed))
    except (ValueError, TypeError) as exc:
        raise TokenInvalidError(f"malformed base64url segment: {exc}") from exc


def _b64url_uint(value: str) -> int:
    """Decode a base64url-encoded big-endian unsigned integer (JWK n/e)."""
    return int.from_bytes(_b64url_decode(value), "big")


def _jwk_to_public_key(jwk: dict) -> rsa.RSAPublicKey:
    """Construct an RSA public key from a JWK ``n``/``e`` pair."""
    if jwk.get("kty") != "RSA":
        raise TokenInvalidError(
            f"unsupported JWK key type {jwk.get('kty')!r}; expected RSA"
        )
    try:
        modulus = _b64url_uint(jwk["n"])
        exponent = _b64url_uint(jwk["e"])
    except KeyError as exc:
        raise TokenInvalidError(f"JWK missing required field {exc}") from exc
    return rsa.RSAPublicNumbers(e=exponent, n=modulus).public_key()


class TokenValidator:
    """Validate RS256 JWTs against a configured issuer, audience, and JWKS.

    Parameters
    ----------
    jwks_cache:
        Source of RSA signing keys, keyed by ``kid``.
    expected_issuer:
        The value the token's ``iss`` claim must equal.
    expected_audience:
        The value the token's ``aud`` claim must equal (or contain, if ``aud``
        is a list).
    clock_skew_seconds:
        Allowable clock drift applied to ``exp`` and ``nbf``. Defaults to 300.
    """

    def __init__(
        self,
        jwks_cache: JwksCache,
        expected_issuer: str,
        expected_audience: str,
        clock_skew_seconds: int = _DEFAULT_CLOCK_SKEW_SECONDS,
    ) -> None:
        self._jwks_cache = jwks_cache
        self._expected_issuer = expected_issuer
        self._expected_audience = expected_audience
        self._clock_skew = clock_skew_seconds

    @staticmethod
    def _split(token: str) -> tuple[str, str, str]:
        parts = token.split(".")
        if len(parts) != 3:
            raise TokenInvalidError(
                "token is not a well-formed JWT (expected 3 dot-separated parts)"
            )
        return parts[0], parts[1], parts[2]

    @staticmethod
    def _decode_json_segment(segment: str, what: str) -> dict[str, Any]:
        try:
            decoded = json.loads(_b64url_decode(segment))
        except (ValueError, json.JSONDecodeError) as exc:
            raise TokenInvalidError(f"{what} is not valid JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise TokenInvalidError(f"{what} is not a JSON object")
        return decoded

    async def validate(self, token: str, nonce: str | None = None) -> dict[str, Any]:
        """Validate ``token`` and return its claim set.

        Raises
        ------
        TokenInvalidError
            Malformed token, wrong algorithm, bad signature, or failed
            issuer / audience / nonce check.
        TokenExpiredError
            ``exp`` is in the past (beyond the allowed skew).
        JwksFetchError
            The signing key for the token's ``kid`` could not be obtained.
        """
        if not token or not isinstance(token, str):
            raise TokenInvalidError("token must be a non-empty string")

        header_seg, payload_seg, signature_seg = self._split(token)
        header = self._decode_json_segment(header_seg, "JWT header")

        # 1. Algorithm pinning — reject 'none', HS256, etc.
        alg = header.get("alg")
        if alg != _EXPECTED_ALG:
            raise TokenInvalidError(
                f"unexpected JWT alg {alg!r}; only {_EXPECTED_ALG} is accepted"
            )

        kid = header.get("kid")
        if not kid:
            raise TokenInvalidError("JWT header is missing 'kid'")

        # 2. Fetch the signing key and verify the signature.
        jwk = await self._jwks_cache.get_key(kid)  # may raise JwksFetchError
        public_key = _jwk_to_public_key(jwk)
        signing_input = f"{header_seg}.{payload_seg}".encode("ascii")
        signature = _b64url_decode(signature_seg)
        try:
            public_key.verify(
                signature,
                signing_input,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except InvalidSignature as exc:
            raise TokenInvalidError("JWT signature verification failed") from exc

        # 3. Decode claims and run claim checks.
        claims = self._decode_json_segment(payload_seg, "JWT payload")
        self._check_temporal(claims)
        self._check_issuer(claims)
        self._check_audience(claims)
        if nonce is not None:
            self._check_nonce(claims, nonce)

        return claims

    def _check_temporal(self, claims: dict[str, Any]) -> None:
        now = time.time()

        exp = claims.get("exp")
        if exp is None:
            raise TokenInvalidError("JWT is missing the 'exp' claim")
        try:
            exp_val = float(exp)
        except (TypeError, ValueError) as exc:
            raise TokenInvalidError("JWT 'exp' is not a number") from exc
        if (exp_val + self._clock_skew) < now:
            raise TokenExpiredError("JWT has expired")

        nbf = claims.get("nbf")
        if nbf is not None:
            try:
                nbf_val = float(nbf)
            except (TypeError, ValueError) as exc:
                raise TokenInvalidError("JWT 'nbf' is not a number") from exc
            if (nbf_val - self._clock_skew) > now:
                raise TokenInvalidError("JWT is not yet valid ('nbf' in future)")

    def _check_issuer(self, claims: dict[str, Any]) -> None:
        iss = claims.get("iss")
        if iss != self._expected_issuer:
            raise TokenInvalidError(
                f"JWT issuer {iss!r} does not match expected "
                f"{self._expected_issuer!r}"
            )

    def _check_audience(self, claims: dict[str, Any]) -> None:
        aud = claims.get("aud")
        if isinstance(aud, list):
            if self._expected_audience not in aud:
                raise TokenInvalidError(
                    f"JWT audience {aud!r} does not include expected "
                    f"{self._expected_audience!r}"
                )
        elif aud != self._expected_audience:
            raise TokenInvalidError(
                f"JWT audience {aud!r} does not match expected "
                f"{self._expected_audience!r}"
            )

    @staticmethod
    def _check_nonce(claims: dict[str, Any], expected_nonce: str) -> None:
        if claims.get("nonce") != expected_nonce:
            raise TokenInvalidError("JWT nonce does not match expected value")


__all__ = ["TokenValidator"]
