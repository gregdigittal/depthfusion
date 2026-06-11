"""JWKS (JSON Web Key Set) cache with TTL and concurrency-safe refresh.

The cache fetches the signing-key set from an OIDC provider's ``jwks_uri``
(for Entra ID this is the ``keys`` document referenced by the OpenID
configuration), keeps it in memory for ``ttl_seconds``, and refreshes it when
stale **or** when a requested ``kid`` is absent (covering key-rotation where a
new key id appears before the TTL elapses).

A single :class:`asyncio.Lock` serialises concurrent refreshes so that a burst
of validations triggers at most one network fetch.
"""
from __future__ import annotations

import asyncio
import os
import time

import httpx

from .errors import JwksFetchError

_DEFAULT_TTL_SECONDS = 3600
_DEFAULT_FETCH_TIMEOUT = 10.0


class JwksCache:
    """In-memory, TTL'd, concurrency-safe cache of a provider's JWKS.

    Parameters
    ----------
    jwks_uri:
        The fully-qualified URL of the JWKS document.
    ttl_seconds:
        Seconds a fetched key set is considered fresh. Defaults to 3600.
    timeout:
        Per-request httpx timeout in seconds for the JWKS fetch.
    """

    def __init__(
        self,
        jwks_uri: str,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        timeout: float = _DEFAULT_FETCH_TIMEOUT,
    ) -> None:
        if not jwks_uri:
            raise JwksFetchError("jwks_uri must be a non-empty URL")
        self._jwks_uri = jwks_uri
        self._ttl_seconds = ttl_seconds
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._keys: dict[str, dict] = {}
        self._fetched_at: float = 0.0

    @classmethod
    def from_env(
        cls,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        timeout: float = _DEFAULT_FETCH_TIMEOUT,
    ) -> "JwksCache":
        """Build a cache from ``DEPTHFUSION_JWKS_URI``.

        Raises
        ------
        JwksFetchError
            If ``DEPTHFUSION_JWKS_URI`` is unset or empty.
        """
        jwks_uri = os.environ.get("DEPTHFUSION_JWKS_URI", "").strip()
        if not jwks_uri:
            raise JwksFetchError(
                "DEPTHFUSION_JWKS_URI is not set; cannot build JwksCache"
            )
        return cls(jwks_uri, ttl_seconds=ttl_seconds, timeout=timeout)

    @property
    def jwks_uri(self) -> str:
        return self._jwks_uri

    def _is_stale(self) -> bool:
        if not self._keys:
            return True
        return (time.monotonic() - self._fetched_at) >= self._ttl_seconds

    async def get_key(self, kid: str) -> dict:
        """Return the JWK dict for ``kid``, fetching/refreshing as needed.

        The cache refreshes when it is stale, or when ``kid`` is not present in
        the currently-cached set (handling key rotation). Refreshes are
        serialised by an :class:`asyncio.Lock`; a second waiter re-checks the
        cache after acquiring the lock to avoid a redundant fetch.

        Raises
        ------
        JwksFetchError
            If the fetch fails or ``kid`` is still absent after a refresh.
        """
        if not kid:
            raise JwksFetchError("kid must be a non-empty string")

        # Fast path: fresh cache that already has the key.
        if not self._is_stale() and kid in self._keys:
            return self._keys[kid]

        async with self._lock:
            # Re-check inside the lock: another coroutine may have refreshed.
            if not self._is_stale() and kid in self._keys:
                return self._keys[kid]

            # Refresh if stale OR if the kid is absent (possible rotation).
            if self._is_stale() or kid not in self._keys:
                await self._refresh()

            key = self._keys.get(kid)
            if key is None:
                raise JwksFetchError(
                    f"signing key with kid={kid!r} not found in JWKS at "
                    f"{self._jwks_uri}"
                )
            return key

    async def _refresh(self) -> None:
        """Fetch the JWKS document and replace the in-memory key map.

        Caller must hold ``self._lock``.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self._jwks_uri)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise JwksFetchError(
                f"failed to fetch JWKS from {self._jwks_uri}: {exc}"
            ) from exc
        except ValueError as exc:  # JSON decode error
            raise JwksFetchError(
                f"JWKS at {self._jwks_uri} returned malformed JSON: {exc}"
            ) from exc

        keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys, list):
            raise JwksFetchError(
                f"JWKS at {self._jwks_uri} has no 'keys' array"
            )

        new_keys: dict[str, dict] = {}
        for jwk in keys:
            if isinstance(jwk, dict) and jwk.get("kid"):
                new_keys[jwk["kid"]] = jwk

        if not new_keys:
            raise JwksFetchError(
                f"JWKS at {self._jwks_uri} contained no keys with a 'kid'"
            )

        self._keys = new_keys
        self._fetched_at = time.monotonic()


__all__ = ["JwksCache"]
