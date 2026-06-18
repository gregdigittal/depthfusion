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
from .fastapi_deps import PrincipalDep, make_require_principal, require_principal
from .jwks_cache import JwksCache
from .models import DeviceCodeResult, Principal
from .oidc_client import OidcClient
from .principal_store import PrincipalStore
from .service_account import (
    DEFAULT_CEILING,
    ServiceAccount,
    filter_records_by_ceiling,
    is_record_visible,
    issue_service_account,
)
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
    # service accounts (T-624)
    "ServiceAccount",
    "issue_service_account",
    "is_record_visible",
    "filter_records_by_ceiling",
    "DEFAULT_CEILING",
    # fastapi deps
    "PrincipalDep",
    "make_require_principal",
    "require_principal",
    # errors
    "IdentityError",
    "TokenExpiredError",
    "TokenInvalidError",
    "JwksFetchError",
    "OidcFlowError",
]
