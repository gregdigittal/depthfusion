"""App-level authentication dependency — wires require_principal for REST and MCP routes.

All FastAPI routes in rest.py, events.py, and mcp/http_server.py use the
``require_principal`` dependency returned by :func:`get_require_principal`.

In production the dependency is backed by a real :class:`TokenValidator` that
validates RS256 JWTs against the Entra ID JWKS.

In tests, override via ``app.dependency_overrides[get_require_principal()]`` or
the simpler ``app.dependency_overrides[_require_principal_dep]`` pattern using
the module-level singleton exposed here.

Environment variables consumed
------------------------------
DEPTHFUSION_JWKS_URI
    Required in production. JWKS endpoint URL (e.g. Entra ID common JWKS).
DEPTHFUSION_OIDC_ISSUER
    Required in production. Token ``iss`` claim expected value.
DEPTHFUSION_OIDC_AUDIENCE
    Required in production. Token ``aud`` claim expected value.

When any of these is absent the dep returns a *disabled* sentinel that always
raises 503 with ``auth_not_configured`` so misconfigured servers fail loudly
rather than granting open access.
"""
from __future__ import annotations

import os
from typing import Annotated  # noqa: F401 — used in _UnconfiguredPrincipalDep signature

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from depthfusion.identity.fastapi_deps import PrincipalDep, make_require_principal
from depthfusion.identity.models import Principal

_bearer = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Sentinel dependency used when OIDC is not configured.
# ---------------------------------------------------------------------------

class _UnconfiguredPrincipalDep:
    """Returned when OIDC env vars are absent. Always raises 503."""

    async def __call__(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_bearer)
        ],
    ) -> Principal:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "auth_not_configured",
                "detail": (
                    "DEPTHFUSION_JWKS_URI / DEPTHFUSION_OIDC_ISSUER / "
                    "DEPTHFUSION_OIDC_AUDIENCE must be set to enable authentication."
                ),
            },
        )


def _build_principal_dep() -> PrincipalDep | _UnconfiguredPrincipalDep:
    """Build the per-process auth dependency from env vars.

    Returns a configured :class:`PrincipalDep` when all three OIDC env vars
    are present; otherwise returns :class:`_UnconfiguredPrincipalDep`.
    """
    jwks_uri = os.getenv("DEPTHFUSION_JWKS_URI", "").strip()
    issuer = os.getenv("DEPTHFUSION_OIDC_ISSUER", "").strip()
    audience = os.getenv("DEPTHFUSION_OIDC_AUDIENCE", "").strip()

    if not (jwks_uri and issuer and audience):
        return _UnconfiguredPrincipalDep()

    from depthfusion.identity.jwks_cache import JwksCache
    from depthfusion.identity.token_validator import TokenValidator

    cache = JwksCache(jwks_uri=jwks_uri)
    validator = TokenValidator(
        jwks_cache=cache,
        expected_issuer=issuer,
        expected_audience=audience,
    )
    return make_require_principal(validator)


# Module-level singleton.  Tests override this via app.dependency_overrides.
_require_principal_dep = _build_principal_dep()


async def require_principal(
    principal: Principal = Depends(_require_principal_dep),
) -> Principal:
    """FastAPI dependency — inject into every route that must be authenticated.

    Usage::

        from depthfusion.api.auth import require_principal

        @app.get("/protected")
        async def endpoint(principal: Annotated[Principal, Depends(require_principal)]):
            ...

    Tests override the inner dep::

        from depthfusion.api.auth import _require_principal_dep
        app.dependency_overrides[_require_principal_dep] = lambda: fake_principal
    """
    return principal


__all__ = ["require_principal", "_require_principal_dep"]
