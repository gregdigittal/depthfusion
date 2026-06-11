"""Tests for the ``require_principal`` FastAPI dependency and 401 envelope.

A minimal FastAPI app wires the dependency to a single protected route. The
underlying :class:`TokenValidator.validate` coroutine is replaced with an
``AsyncMock`` so these tests exercise the dependency's branching and error
envelope without any real crypto / JWKS.

NOTE: this module deliberately does **not** use ``from __future__ import
annotations``. The protected route's parameter is annotated with
``Annotated[Principal, Depends(require_principal)]`` where ``require_principal``
is a function-local closure variable. Under PEP 563 string annotations FastAPI
cannot resolve that local at signature-introspection time and would mis-classify
the parameter as a query field (yielding 422s). Keeping annotations live (real
objects) lets FastAPI see the embedded ``Depends`` marker.
"""

from typing import Annotated
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from depthfusion.identity import make_require_principal
from depthfusion.identity.errors import TokenExpiredError, TokenInvalidError
from depthfusion.identity.models import Principal
from depthfusion.identity.token_validator import TokenValidator


def _build_client(validator: TokenValidator) -> TestClient:
    """Build a TestClient over an app whose ``/me`` route requires a principal."""
    app = FastAPI()
    require_principal = make_require_principal(validator)

    @app.get("/me")
    async def me(  # noqa: ANN202 - test route
        principal: Annotated[Principal, Depends(require_principal)],
    ) -> dict[str, object]:
        return {
            "principal_id": principal.principal_id,
            "upn": principal.upn,
            "display_name": principal.display_name,
            "groups": principal.groups,
        }

    return TestClient(app)


@pytest.fixture()
def validator() -> MagicMock:
    """A TokenValidator stand-in with an async ``validate`` method."""
    mock = MagicMock(spec=TokenValidator)
    mock.validate = AsyncMock()
    return mock


def test_valid_token_returns_200_and_principal(validator: MagicMock) -> None:
    validator.validate.return_value = {
        "sub": "user-123",
        "preferred_username": "alice@example.com",
        "name": "Alice Example",
        "groups": ["g-admins", "g-eng"],
    }
    client = _build_client(validator)

    resp = client.get("/me", headers={"Authorization": "Bearer good.token.here"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "principal_id": "user-123",
        "upn": "alice@example.com",
        "display_name": "Alice Example",
        "groups": ["g-admins", "g-eng"],
    }
    validator.validate.assert_awaited_once_with("good.token.here")


def test_valid_token_with_minimal_claims_defaults(validator: MagicMock) -> None:
    """Only ``sub`` is required; other fields default sensibly."""
    validator.validate.return_value = {"sub": "user-min"}
    client = _build_client(validator)

    resp = client.get("/me", headers={"Authorization": "Bearer ok"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "principal_id": "user-min",
        "upn": "",
        "display_name": "",
        "groups": [],
    }


def test_missing_auth_header_returns_401_missing_token(validator: MagicMock) -> None:
    client = _build_client(validator)

    resp = client.get("/me")

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "missing_token"
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    validator.validate.assert_not_awaited()


def test_expired_token_returns_401_token_expired(validator: MagicMock) -> None:
    validator.validate.side_effect = TokenExpiredError("JWT has expired")
    client = _build_client(validator)

    resp = client.get("/me", headers={"Authorization": "Bearer expired"})

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "token_expired"
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_invalid_token_returns_401_invalid_token(validator: MagicMock) -> None:
    validator.validate.side_effect = TokenInvalidError("bad signature")
    client = _build_client(validator)

    resp = client.get("/me", headers={"Authorization": "Bearer nope"})

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_token"
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_all_401s_carry_www_authenticate_bearer(validator: MagicMock) -> None:
    """Every 401 path emits the Bearer challenge header."""
    client = _build_client(validator)

    # missing token
    r1 = client.get("/me")
    # expired
    validator.validate.side_effect = TokenExpiredError("expired")
    r2 = client.get("/me", headers={"Authorization": "Bearer x"})
    # invalid
    validator.validate.side_effect = TokenInvalidError("invalid")
    r3 = client.get("/me", headers={"Authorization": "Bearer y"})

    for resp in (r1, r2, r3):
        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"] == "Bearer"
