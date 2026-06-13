"""DepthFusion identity package — OIDC (Entra ID) authentication primitives.

Public surface:

* :class:`Principal`, :class:`DeviceCodeResult` — data models.
* :class:`JwksCache` — TTL'd, concurrency-safe JWKS fetcher.
* :class:`TokenValidator` — RS256 JWT validation (signature + claims).
* :class:`OidcClient` — public-client PKCE auth-code and device-code flows.
* Error hierarchy: :class:`IdentityError` and subclasses.
"""
from __future__ import annotations

from .errors import (
    IdentityError,
    JwksFetchError,
    OidcFlowError,
    TokenExpiredError,
    TokenInvalidError,
)
from .jwks_cache import JwksCache
from .models import DeviceCodeResult, Principal
from .oidc_client import OidcClient
from .principal_store import PrincipalStore
from .token_validator import TokenValidator

__all__ = [
    # models
    "Principal",
    "DeviceCodeResult",
    # services
    "JwksCache",
    "TokenValidator",
    "OidcClient",
    "PrincipalStore",
    # errors
    "IdentityError",
    "TokenExpiredError",
    "TokenInvalidError",
    "JwksFetchError",
    "OidcFlowError",
]
