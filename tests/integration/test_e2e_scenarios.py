"""Additional auth E2E scenarios for the DepthFusion identity layer (E-63 T-691).

This module extends the scenario coverage in ``test_oidc_e2e.py``. That file
already covers, against the MCP HTTP server:
  * unauthenticated (no Authorization header) -> HTTP 401
  * wrong / structurally-invalid token        -> HTTP 401
  * valid token                               -> HTTP 200

The missing scenario added here is the one the existing suite does not have:
  * authenticated with an **EXPIRED** token   -> HTTP 401

How this exercises ``require_principal`` over the real HTTP layer
-----------------------------------------------------------------
The task requires asserting "the 401 error envelope shape produced by
``require_principal``" through the application's HTTP surface — not by calling
the dependency in isolation. We therefore drive the expired-token request
through a genuine FastAPI route on ``depthfusion.api.rest.app``.

``depthfusion.api.rest.app`` mounts protected routes (e.g. ``GET /status``)
behind ``Depends(require_principal)``. ``require_principal`` delegates to a
module-level inner dependency, ``_require_principal_dep`` — the established
test seam other rest API tests use via ``app.dependency_overrides`` (see
``tests/test_integration/test_rest_api.py`` and
``tests/test_integration/test_route_authz.py``).

To exercise the EXPIRED-token path we override ``_require_principal_dep`` with a
**real** :class:`~depthfusion.identity.fastapi_deps.PrincipalDep` bound to a stub
:class:`TokenValidator` whose ``validate`` raises :class:`TokenExpiredError`.
The override is the genuine production dependency object — only the validator
behind it is stubbed — so the request flows through the real
``PrincipalDep.__call__`` -> ``_make_401`` mapping and FastAPI's exception
handling, producing the actual HTTP 401 JSON response. We then assert the
serialized response envelope:

    HTTP 401
    body:    {"detail": {"error": "token_expired", "detail": "<message>"}}
    headers: WWW-Authenticate: Bearer

No Azure credentials are required (the validator is a local stub that raises),
so this runs unconditionally and quickly in CI. Crucially, it uses a plain
JSON ``GET`` route rather than the streaming ``/sse`` endpoint, so the test
client never blocks on an open SSE stream (the hang seen in a prior attempt).

NOTE: this module deliberately does NOT use ``from __future__ import
annotations``. The rest app's routes use ``Depends(require_principal)`` with
runtime-resolved annotations; importing the app and overriding its inner dep
must keep FastAPI's signature introspection working, mirroring the
no-future-annotations convention in ``test_route_authz.py``.
"""
from typing import Any, Optional

import pytest
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient

from depthfusion.identity.errors import TokenExpiredError, TokenInvalidError
from depthfusion.identity.fastapi_deps import PrincipalDep
from depthfusion.identity.models import Principal

# ---------------------------------------------------------------------------
# Stub validator — no network / JWKS / Azure dependency.
# ---------------------------------------------------------------------------


class _RaisingValidator:
    """Minimal stand-in for :class:`TokenValidator`.

    Implements the single ``validate`` coroutine that ``PrincipalDep`` awaits.
    Constructed with an exception instance to raise, so a test can simulate an
    expired (or otherwise rejected) token without any network/JWKS dependency
    while still exercising the *real* ``PrincipalDep`` -> ``_make_401`` mapping.
    """

    def __init__(self, raises: Exception) -> None:
        self._raises = raises

    async def validate(
        self, token: str, nonce: Optional[str] = None
    ) -> "dict[str, Any]":
        raise self._raises


# ``auto_error=False`` so a *missing* credential yields ``None`` (which the real
# PrincipalDep maps to its own 401 envelope) instead of FastAPI's default 403 —
# matching the scheme used inside depthfusion.identity.fastapi_deps.
_test_bearer = HTTPBearer(auto_error=False)


def _make_expired_override(error: Exception):
    """Build a FastAPI dependency override that runs the *real* auth logic.

    Returns an ``async`` callable suitable for ``app.dependency_overrides``. The
    override constructs the genuine :class:`PrincipalDep` (bound to a stub
    validator that raises ``error``) and delegates to its ``__call__`` — so the
    production ``PrincipalDep`` -> ``_make_401`` mapping runs unchanged and
    surfaces the standard 401 envelope through the HTTP route.

    Why an explicit override function rather than overriding with a bare
    ``PrincipalDep`` instance: ``depthfusion.identity.fastapi_deps`` uses
    ``from __future__ import annotations``, so ``PrincipalDep.__call__``'s
    ``credentials`` parameter carries a *string* annotation. When FastAPI
    re-introspects an overridden instance ``__call__`` it cannot resolve that
    string to ``Depends(HTTPBearer)`` and mis-classifies ``credentials`` as a
    required query field (HTTP 422). Declaring the sub-dependency here — in a
    module without future-annotations — lets FastAPI resolve the ``HTTPBearer``
    injection correctly while still executing the real dependency body.
    """
    real_dep = PrincipalDep(_RaisingValidator(error))  # type: ignore[arg-type]

    async def _override(
        credentials: HTTPAuthorizationCredentials = Depends(_test_bearer),
    ) -> Principal:
        return await real_dep(credentials=credentials)

    return _override


# ---------------------------------------------------------------------------
# Harness — drive a request through the real rest.app HTTP layer with a
# PrincipalDep whose validator raises the chosen identity error.
# ---------------------------------------------------------------------------


@pytest.fixture()
def expired_token_client(tmp_path, monkeypatch):
    """TestClient for ``rest.app`` whose auth dep rejects with TokenExpiredError.

    The real ``PrincipalDep`` is wired in via ``app.dependency_overrides`` on
    the module-level ``_require_principal_dep`` seam, backed by a stub validator
    that raises :class:`TokenExpiredError`. A Bearer header is supplied on the
    request, so the credential-present branch runs and ``validate`` is awaited —
    surfacing the 401 envelope through the HTTP route exactly as production
    would for a genuinely expired JWT.
    """
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))

    from depthfusion.api.auth import _require_principal_dep
    from depthfusion.api.rest import app

    original = dict(app.dependency_overrides)
    app.dependency_overrides[_require_principal_dep] = _make_expired_override(
        TokenExpiredError("exp claim is in the past")
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)


# ---------------------------------------------------------------------------
# Expired-token scenario — always runs (no integration marker, no Azure creds)
# E-63 T-691: authenticated-with-EXPIRED-token -> HTTP 401
# ---------------------------------------------------------------------------


def test_expired_token_returns_401_over_http(expired_token_client: TestClient) -> None:
    """A request carrying an EXPIRED Bearer token to a protected route -> 401.

    Drives ``GET /status`` (mounted behind ``Depends(require_principal)``) with
    an ``Authorization: Bearer <token>`` header. The configured validator raises
    :class:`TokenExpiredError`, which ``require_principal`` maps through
    ``_make_401`` to an HTTP 401 carrying the standard nested error envelope and
    a ``WWW-Authenticate: Bearer`` challenge. We assert the *serialized HTTP
    response*, i.e. the envelope shape clients actually receive.
    """
    resp = expired_token_client.get(
        "/status",
        headers={"Authorization": "Bearer expired.jwt.token"},
    )

    # Status: an expired token is unauthorized over the HTTP layer.
    assert resp.status_code == 401, (
        f"Expected HTTP 401 for an expired JWT, got {resp.status_code}: {resp.text}"
    )

    # RFC 6750 challenge header on the actual response.
    assert resp.headers.get("WWW-Authenticate") == "Bearer", (
        f"Expected 'WWW-Authenticate: Bearer' on the response, "
        f"got: {resp.headers.get('WWW-Authenticate')!r}"
    )

    # Envelope shape produced by require_principal: FastAPI serializes the
    # HTTPException ``detail`` under a top-level ``detail`` key, so the response
    # JSON is {"detail": {"error": "token_expired", "detail": "<message>"}}.
    body = resp.json()
    assert isinstance(body, dict) and "detail" in body, (
        f"Expected a JSON object with a 'detail' envelope, got: {body!r}"
    )
    envelope = body["detail"]
    assert isinstance(envelope, dict), (
        f"Expected require_principal's nested-object envelope, got: {envelope!r}"
    )
    assert envelope.get("error") == "token_expired", (
        f"Expected error code 'token_expired', got: {envelope.get('error')!r}"
    )
    assert envelope.get("detail") == "exp claim is in the past", (
        f"Expected the validator's expiry message to surface in 'detail', "
        f"got: {envelope.get('detail')!r}"
    )


def test_expired_token_envelope_distinct_from_invalid_token(
    tmp_path, monkeypatch
) -> None:
    """The expired-token code differs from the generic invalid-token code (HTTP).

    ``require_principal`` maps ``TokenExpiredError`` to ``token_expired`` and
    every other ``IdentityError`` (e.g. ``TokenInvalidError``) to
    ``invalid_token``. Both surface as HTTP 401, but the machine-readable
    ``error`` code lets clients branch — assert the two are not conflated when
    observed through the real HTTP response envelope.
    """
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))

    from depthfusion.api.auth import _require_principal_dep
    from depthfusion.api.rest import app

    def _envelope_for(error: Exception) -> dict:
        original = dict(app.dependency_overrides)
        app.dependency_overrides[_require_principal_dep] = _make_expired_override(error)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/status", headers={"Authorization": "Bearer some.token"}
            )
            assert resp.status_code == 401, (
                f"Expected HTTP 401, got {resp.status_code}: {resp.text}"
            )
            return resp.json()["detail"]
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original)

    expired_env = _envelope_for(TokenExpiredError("token has expired"))
    invalid_env = _envelope_for(TokenInvalidError("bad signature"))

    assert expired_env.get("error") == "token_expired", (
        f"Expected 'token_expired' for an expired token, got: {expired_env.get('error')!r}"
    )
    assert invalid_env.get("error") == "invalid_token", (
        f"Expected 'invalid_token' for a structurally-invalid token, "
        f"got: {invalid_env.get('error')!r}"
    )
    assert expired_env.get("error") != invalid_env.get("error"), (
        "Expired and invalid tokens must surface distinct error codes."
    )
