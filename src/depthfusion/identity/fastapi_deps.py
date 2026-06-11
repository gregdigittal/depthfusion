"""FastAPI dependency that turns a Bearer token into an authenticated Principal.

This module is the bridge between the transport-agnostic identity primitives
(:class:`~depthfusion.identity.token_validator.TokenValidator`,
:class:`~depthfusion.identity.models.Principal`) and a FastAPI HTTP surface.

It exposes :class:`PrincipalDep`, an injectable callable that:

* extracts the ``Authorization: Bearer <token>`` credential,
* validates it via the configured :class:`TokenValidator`, and
* returns a :class:`Principal` built from the token claims.

Every failure mode produces a *standard 401 error envelope* — a JSON body of the
shape ``{"error": "<code>", "detail": "<human message>"}`` together with the
``WWW-Authenticate: Bearer`` header — so clients can branch on a stable
``error`` code rather than parsing prose.

The dependency is constructed per-application via :func:`make_require_principal`
(aliased as ``require_principal``) so the validator is injected explicitly
rather than read from module-level global state.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import IdentityError, TokenExpiredError
from .models import Principal
from .token_validator import TokenValidator

# Module-level scheme. ``auto_error=False`` so a *missing* credential yields
# ``None`` (which we map to our own envelope) instead of FastAPI's default 403.
_bearer = HTTPBearer(auto_error=False)


def _make_401(detail: str, error_code: str = "unauthorized") -> HTTPException:
    """Build a 401 with the standard error envelope and Bearer challenge.

    Parameters
    ----------
    detail:
        Human-readable explanation of why authentication failed.
    error_code:
        Stable machine-readable code clients can branch on (e.g.
        ``missing_token``, ``token_expired``, ``invalid_token``).
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": error_code, "detail": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )


class PrincipalDep:
    """FastAPI dependency resolving a Bearer token to a :class:`Principal`.

    Instances are callable and intended to be used with ``Depends(...)``.
    Construct one per application with the application's configured
    :class:`TokenValidator`.
    """

    def __init__(self, validator: TokenValidator) -> None:
        self._validator = validator

    async def __call__(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_bearer)
        ],
    ) -> Principal:
        """Resolve the request's Bearer token into an authenticated principal.

        Raises
        ------
        HTTPException
            A 401 with the standard envelope when the credential is missing,
            expired, or otherwise invalid.
        """
        if credentials is None:
            raise _make_401("No Bearer token", "missing_token")

        try:
            claims = await self._validator.validate(credentials.credentials)
        except TokenExpiredError as exc:
            raise _make_401(str(exc) or "Token expired", "token_expired") from exc
        except IdentityError as exc:
            raise _make_401(str(exc) or "Invalid token", "invalid_token") from exc

        return Principal(
            principal_id=claims["sub"],
            upn=claims.get("preferred_username", ""),
            display_name=claims.get("name", ""),
            groups=claims.get("groups", []),
        )


def make_require_principal(validator: TokenValidator) -> PrincipalDep:
    """Create a :class:`PrincipalDep` bound to ``validator``.

    Use the returned object as a FastAPI dependency::

        require_principal = make_require_principal(app_validator)

        @app.get("/me")
        async def me(principal: Annotated[Principal, Depends(require_principal)]):
            return principal
    """
    return PrincipalDep(validator)


# Module-level alias matching the conventional dependency name.
require_principal = make_require_principal


__all__ = ["PrincipalDep", "make_require_principal", "require_principal"]
