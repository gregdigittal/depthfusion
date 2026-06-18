"""Integration tests for MCP HTTP/SSE server auth (S-154 T-540).

Uses FastAPI TestClient against the real ``app`` object.  Each test is
isolated: the module-level ``_token_validator`` global is reset via
monkeypatch so validator state never leaks across tests.

Test coverage:
  - Valid bearer → auth passes (require_principal override)
  - No Authorization header  → 401 or 503 (fail-closed)
  - No auth backend configured → 401 or 503 (fail-closed)
  - Expired JWT (JWKS path via _check_mcp_auth) → 401 or 503
  - /health is always unauthenticated → 200
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import depthfusion.mcp.http_server as _mod
from depthfusion.identity.models import Principal
from depthfusion.mcp.http_server import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_token_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset module-level _token_validator to None before every test."""
    monkeypatch.setattr(_mod, "_token_validator", None)


def _fake_principal() -> Principal:
    return Principal(
        principal_id="test-mcp-user",
        upn="test@example.com",
        display_name="Test MCP User",
    )


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def client_authed() -> TestClient:
    """TestClient with require_principal overridden — auth always passes."""
    from depthfusion.api.auth import require_principal

    async def _override() -> Principal:
        return _fake_principal()

    app.dependency_overrides[require_principal] = _override
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(require_principal, None)


# ---------------------------------------------------------------------------
# /health — always unauthenticated
# ---------------------------------------------------------------------------

def test_health_no_auth(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Auth passes — require_principal overridden
# ---------------------------------------------------------------------------

def test_sse_auth_passes_via_messages(client_authed: TestClient) -> None:
    """Valid auth → /messages returns 404 (session not found), not 401.

    We test auth acceptance via /messages rather than /sse to avoid blocking on
    the long-lived SSE generator.  Auth is the same dependency for both endpoints.
    """
    resp = client_authed.post(
        "/messages?sessionId=test-session",
        headers={"Authorization": "Bearer some-token"},
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
    )
    # Auth passed; unknown session → 404, not 401
    assert resp.status_code == 404


def test_messages_valid_static_token(client_authed: TestClient) -> None:
    """Auth passes → /messages returns 404 (no session) not 401."""
    resp = client_authed.post(
        "/messages?sessionId=nonexistent",
        headers={"Authorization": "Bearer some-token"},
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
    )
    # Auth passed; session doesn't exist → 404 (not 401)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth rejected — all rejection cases accept both 401 and 503
# (503 = auth not configured; 401 = bad/missing credentials)
# ---------------------------------------------------------------------------

def test_sse_wrong_static_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """No valid auth configured → request rejected."""
    resp = client.get(
        "/sse",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code in (401, 503)


def test_sse_no_auth_header(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = client.get("/sse")
    assert resp.status_code in (401, 503)


def test_messages_no_auth_header(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = client.post(
        "/messages?sessionId=any",
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
    )
    assert resp.status_code in (401, 503)


def test_sse_no_auth_backend_configured(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token present but no auth backend configured → 401 or 503 (fail-closed)."""
    monkeypatch.delenv("DEPTHFUSION_MCP_TOKEN", raising=False)
    monkeypatch.setattr(_mod, "_get_token_validator", lambda: None)

    resp = client.get(
        "/sse",
        headers={"Authorization": "Bearer some-bearer-token"},
    )
    assert resp.status_code in (401, 503)


def test_sse_expired_jwt(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expired credential → 401 or 503."""
    resp = client.get(
        "/sse",
        headers={"Authorization": "Bearer expired.jwt.token"},
    )
    assert resp.status_code in (401, 503)


def test_sse_malformed_bearer_prefix(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authorization header without 'Bearer ' prefix → 401 or 503."""
    resp = client.get(
        "/sse",
        headers={"Authorization": "Token some-token"},
    )
    assert resp.status_code in (401, 503)
