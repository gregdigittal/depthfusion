"""DepthFusion identity package — OIDC (Entra ID) authentication primitives.

Public surface:

* :class:`Principal`, :class:`DeviceCodeResult` — data models.
* :class:`JwksCache` — TTL'd, concurrency-safe JWKS fetcher.
* :class:`TokenValidator` — RS256 JWT validation (signature + claims). *(lazy)*
* :class:`OidcClient` — public-client PKCE auth-code and device-code flows. *(lazy)*
* Error hierarchy: :class:`IdentityError` and subclasses.
* FastAPI deps: :class:`PrincipalDep`, :func:`make_require_principal`,
  :func:`require_principal`. *(lazy — requires ``fastapi`` installed)*
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
from .principal_store import PrincipalStore
from .service_account import (
    DEFAULT_CEILING,
    ServiceAccount,
    filter_records_by_ceiling,
    is_record_visible,
    issue_service_account,
)

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
    # fastapi deps (lazy — require fastapi)
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

# Names deferred until first access:
# - TokenValidator imports ``cryptography`` (RS256 primitives).
# - OidcClient imports TokenValidator, so it also pulls in ``cryptography``.
# - PrincipalDep / make_require_principal / require_principal import ``fastapi``.
# None of these are needed for the core MCP stdio path, so they are loaded on demand.
_LAZY: dict[str, str] = {
    "TokenValidator": ".token_validator",
    "OidcClient": ".oidc_client",
    "PrincipalDep": ".fastapi_deps",
    "make_require_principal": ".fastapi_deps",
    "require_principal": ".fastapi_deps",
}


def __getattr__(name: str) -> object:  # noqa: ANN401
    submodule = _LAZY.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(submodule, package=__name__)
    obj = getattr(mod, name)
    # Cache in the module namespace so subsequent accesses are O(1).
    globals()[name] = obj
    return obj
