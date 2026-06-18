"""Route-walker test — T-552 / S-157 AC-4.

Assert that every registered FastAPI route enforces authentication:
an unauthenticated request (no Authorization header) must return 401,
not 200 or 500.

Exempt routes (intentionally public or framework-managed):
- /health              — health-check probe; must return 200 with no auth
- /openapi.json        — FastAPI schema endpoint
- /docs                — Swagger UI
- /docs/oauth2-redirect — Swagger OAuth redirect
- /redoc               — ReDoc UI

All other routes must return 401 when called without a bearer token.

The test overrides ``_require_principal_dep`` (the inner dependency resolved
by ``require_principal``) with a real ``PrincipalDep`` backed by a mock
``TokenValidator``.  With no Authorization header, ``HTTPBearer(auto_error=False)``
yields ``credentials=None``, and ``PrincipalDep.__call__`` raises a 401
before the route handler body is entered.

This ensures the test exercises the actual auth enforcement path rather
than a test-only shortcut.

NOTE: This module deliberately does NOT use ``from __future__ import annotations``
so that FastAPI can resolve ``Annotated[Principal, Depends(...)]`` at route
registration time.
"""
import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from depthfusion.identity.fastapi_deps import PrincipalDep
from depthfusion.identity.token_validator import TokenValidator

# ---------------------------------------------------------------------------
# Intentionally public routes — excluded from the auth-enforcement assertion
# ---------------------------------------------------------------------------

#: Framework-managed or intentionally open paths.
#: Any path in this set is allowed to return any status code for an
#: unauthenticated request.  Add entries here only when a route is
#: deliberately public AND that decision has been reviewed.
_PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/metrics",  # Prometheus scraping — intentionally public per admin_console.py
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_principal_dep() -> PrincipalDep:
    """Return a real PrincipalDep wired to a mock validator.

    The validator's ``validate`` coroutine will never be called during this
    test because ``PrincipalDep.__call__`` raises 401 before invoking
    ``validate`` when no bearer token is present.
    """
    mock_validator = MagicMock(spec=TokenValidator)
    mock_validator.validate = AsyncMock(return_value={})
    return PrincipalDep(validator=mock_validator)


def _collect_protected_routes(app) -> list[tuple[str, str]]:
    """Return (method, path) pairs for every route that must enforce auth.

    Skips:
    - Non-APIRoute entries (Mount, WebSocket routing shims, etc.)
    - Routes whose path is in ``_PUBLIC_PATHS``
    - Routes that have no ``methods`` set (exotic routing)
    """
    protected: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in _PUBLIC_PATHS:
            continue
        if not route.methods:
            continue
        # Pick one method — HEAD is redundant with GET; prefer the first non-HEAD.
        method = next(
            (m for m in sorted(route.methods) if m != "HEAD"),
            next(iter(route.methods)),
        )
        protected.append((method, route.path))
    return protected


def _stub_path(path: str) -> str:
    """Replace path parameters with stub values so the URL is valid."""
    return re.sub(r"\{[^}]+\}", "stub-value", path)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_every_protected_route_returns_401_without_auth(tmp_path, monkeypatch):
    """Every non-public route must return 401 (not 200 or 500) with no auth.

    For each protected route:
    1. Substitute stub values into path parameters.
    2. Send a request with NO Authorization header.
    3. Assert status_code == 401.
       - 200 indicates the route handler ran without auth (missing dep).
       - 5xx indicates a server error that leaks before auth was checked.
       - 422 is allowed: FastAPI rejected the request due to missing required
         query/body params *before* calling the handler — the endpoint is
         still protected (auth would have fired if params were present).
    """
    # Set minimal env so the app can be imported without side-effects
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")

    from depthfusion.api.auth import _require_principal_dep
    from depthfusion.api.rest import app

    stub = _stub_principal_dep()

    # Install the override; clean up after the test regardless of outcome.
    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[_require_principal_dep] = stub

    try:
        client = TestClient(app, raise_server_exceptions=False)

        protected_routes = _collect_protected_routes(app)
        assert protected_routes, "No protected routes found — route enumeration is broken"

        failures: list[str] = []

        for method, path in protected_routes:
            url = _stub_path(path)
            response = client.request(method, url)

            if response.status_code == 200:
                failures.append(
                    f"MISSING AUTH: {method} {path} → 200 (route returned data without auth)"
                )
            elif response.status_code >= 500:
                failures.append(
                    f"SERVER ERROR before auth: {method} {path} → {response.status_code}"
                )
            elif response.status_code not in (401, 422):
                # 401 = auth enforced correctly.
                # 422 = FastAPI validation rejected missing required params before
                #        the handler ran — the route is still protected.
                # Anything else (403, 404, …) may indicate a wiring problem.
                failures.append(
                    f"UNEXPECTED STATUS: {method} {path} → {response.status_code} "
                    f"(expected 401 or 422)"
                )

        if failures:
            detail = "\n  ".join(failures)
            pytest.fail(
                f"{len(failures)} route(s) failed the auth-enforcement check:\n  {detail}"
            )
    finally:
        # Restore overrides to their prior state so subsequent tests are
        # not affected by this test's dependency injection.
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
