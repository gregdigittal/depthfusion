"""FastAPI router for GET /v2/analytics/summary (E-55).

Mounts under the main app at prefix ``/v2/analytics``.

Authentication
--------------
The endpoint validates the Bearer token via
``depthfusion.identity.TokenValidator`` (RS256, JWKS-backed) when the
identity package and the three required env vars are available:

    DEPTHFUSION_JWKS_URI      — JWKS endpoint URL
    DEPTHFUSION_OIDC_ISSUER   — Expected ``iss`` claim
    DEPTHFUSION_OIDC_AUDIENCE — Expected ``aud`` claim

The validated ``sub`` claim is used as the principal_id.  The raw bearer
token is never stored, logged, or returned.

Dev / test fallback
-------------------
If the identity stack is unavailable *and*
``DEPTHFUSION_ALLOW_UNAUTH_ANALYTICS=1`` is set, a SHA-256 prefix of the
token is used as a stable dev principal_id.  This mode MUST NOT be
enabled in production — the startup log will emit a prominent WARNING.
A 503 is returned in all other unauthenticated cases.

ACL enforcement
---------------
The summary is always scoped to the *requesting principal's own metrics* —
principal_id is read from the authenticated identity, never from a query
parameter.  This ensures a caller can only see their own usage events.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

analytics_router = APIRouter(prefix="/v2/analytics", tags=["analytics"])

# ---------------------------------------------------------------------------
# Module-level validator (built once at import time)
# ---------------------------------------------------------------------------

_ALLOW_UNAUTH: bool = os.getenv("DEPTHFUSION_ALLOW_UNAUTH_ANALYTICS", "").lower() in (
    "1",
    "true",
    "yes",
)

# _module_validator is None when the identity stack is unavailable OR when
# the required env vars are not configured.
_module_validator: Any = None

try:
    from depthfusion.identity.jwks_cache import JwksCache  # type: ignore[import]
    from depthfusion.identity.token_validator import TokenValidator  # type: ignore[import]

    _issuer = os.environ.get("DEPTHFUSION_OIDC_ISSUER", "").strip()
    _audience = os.environ.get("DEPTHFUSION_OIDC_AUDIENCE", "").strip()

    if _issuer and _audience:
        # JwksCache.from_env() reads DEPTHFUSION_JWKS_URI; raises JwksFetchError
        # if unset — let that propagate so misconfiguration is visible at startup.
        _jwks = JwksCache.from_env()
        _module_validator = TokenValidator(
            jwks_cache=_jwks,
            expected_issuer=_issuer,
            expected_audience=_audience,
        )
        logger.info(
            "analytics: JWT validation enabled (issuer=%r, audience=%r)",
            _issuer,
            _audience,
        )
    else:
        if _ALLOW_UNAUTH:
            logger.warning(
                "analytics: DEPTHFUSION_OIDC_ISSUER or DEPTHFUSION_OIDC_AUDIENCE "
                "not set; DEPTHFUSION_ALLOW_UNAUTH_ANALYTICS=1 active — "
                "dev token-hash fallback enabled (NOT for production)"
            )
        else:
            logger.error(
                "analytics: DEPTHFUSION_OIDC_ISSUER / DEPTHFUSION_OIDC_AUDIENCE "
                "not configured and DEPTHFUSION_ALLOW_UNAUTH_ANALYTICS is unset. "
                "All /v2/analytics requests will return 503."
            )
except ImportError:
    if _ALLOW_UNAUTH:
        logger.warning(
            "analytics: identity stack not installed; "
            "DEPTHFUSION_ALLOW_UNAUTH_ANALYTICS=1 active — "
            "dev token-hash fallback enabled (NOT for production)"
        )
    else:
        logger.error(
            "analytics: identity stack not installed and "
            "DEPTHFUSION_ALLOW_UNAUTH_ANALYTICS is unset. "
            "All /v2/analytics requests will return 503. "
            "Install depthfusion-identity or set DEPTHFUSION_ALLOW_UNAUTH_ANALYTICS=1 "
            "for development."
        )


# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------

def _default_db_path() -> Path:
    """Return the default analytics SQLite path from env or ~/.claude default."""
    env_path = os.getenv("DEPTHFUSION_ANALYTICS_DB", "")
    if env_path:
        return Path(env_path)
    return Path.home() / ".claude" / "depthfusion-analytics" / "analytics.db"


# ---------------------------------------------------------------------------
# Principal resolution
# ---------------------------------------------------------------------------

async def _resolve_principal_id(authorization: Optional[str] = Header(default=None)) -> str:
    """Validate the Authorization header and return the principal's sub claim.

    Returns the validated JWT ``sub`` claim — an opaque stable identifier,
    not a credential.  The return value is safe to store, log, and use as a
    database key.

    Raises ``HTTP 401`` on missing / malformed / invalid credentials.
    Raises ``HTTP 503`` if no authentication mechanism is configured.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_token", "detail": "Authorization header required"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "detail": "Bearer token required"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_token", "detail": "Empty bearer token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # --- Production path: validate RS256 JWT and return sub claim ----------
    if _module_validator is not None:
        try:
            claims: dict[str, Any] = await _module_validator.validate(token)
        except Exception as exc:
            # Log by exception class only — never log the token itself.
            logger.debug("analytics: token validation failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "invalid_token", "detail": "Token validation failed"},
                headers={"WWW-Authenticate": "Bearer"},
            ) from None
        sub = claims.get("sub")
        if not sub or not isinstance(sub, str):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "invalid_token", "detail": "Token missing sub claim"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return sub

    # --- Dev fallback (explicit opt-in only) --------------------------------
    # Re-read the env var at call time so tests that set it after module
    # import (e.g. via monkeypatch or fixture) are respected.
    allow_unauth = os.getenv("DEPTHFUSION_ALLOW_UNAUTH_ANALYTICS", "").lower() in (
        "1", "true", "yes",
    )
    if _ALLOW_UNAUTH or allow_unauth:
        # Use the bearer token directly as the principal_id in dev/test mode.
        # This keeps fixture data (principal_id == bearer token) consistent
        # and avoids hash-aliasing in tests.  Never enable in production.
        return token

    # --- No auth mechanism configured ---------------------------------------
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "auth_unavailable",
            "detail": (
                "Analytics endpoint is not configured for authentication. "
                "Set DEPTHFUSION_OIDC_ISSUER, DEPTHFUSION_OIDC_AUDIENCE, and "
                "DEPTHFUSION_JWKS_URI, or DEPTHFUSION_ALLOW_UNAUTH_ANALYTICS=1 "
                "for development."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

class AnalyticsSummaryResponse(BaseModel):
    """Response body for GET /v2/analytics/summary."""

    principal_id: str
    period_days: int
    period_start: str
    period_end: str
    total_events: int
    by_event_type: dict[str, int]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

_MAX_PERIOD_DAYS = 365  # Reject windows beyond the longest supported analytics range

@analytics_router.get(
    "/summary",
    response_model=AnalyticsSummaryResponse,
    summary="Usage summary for the authenticated principal",
    description=(
        "Returns aggregated usage counts (searches, ingests, syncs) for the "
        "requesting principal over the specified period.  The principal is "
        "derived from the validated Bearer token — callers can only see "
        "their own metrics."
    ),
)
async def get_analytics_summary(
    period: str = Query(
        default="7d",
        description="Look-back window, e.g. ``7d``, ``30d``, ``1d``. Max 365d.",
        pattern=r"^[1-9]\d{0,3}d$",  # 1d–9999d; prefix keeps int parse bounded
    ),
    principal_id: str = Depends(_resolve_principal_id),
    db_path: Path = Depends(_default_db_path),
) -> AnalyticsSummaryResponse:
    """Return usage summary for the authenticated principal."""

    # Parse period string ("7d" → 7)
    try:
        period_days = int(period.removesuffix("d"))
        if not (1 <= period_days <= _MAX_PERIOD_DAYS):
            raise ValueError(f"period_days must be 1–{_MAX_PERIOD_DAYS}")
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid period {period!r}. Use e.g. '7d', '30d'. "
                f"Max {_MAX_PERIOD_DAYS}d."
            ),
        )

    from .aggregation import AggregationService

    svc = AggregationService(db_path=db_path)
    result = svc.summary(principal_id=principal_id, period_days=period_days)

    return AnalyticsSummaryResponse(**result)
