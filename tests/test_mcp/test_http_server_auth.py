"""Integration tests for MCP HTTP/SSE server auth (S-154 T-540).

Uses FastAPI TestClient against the real ``app`` object.  Each test is
isolated: the module-level ``_token_validator`` global is reset via
monkeypatch so validator state never leaks across tests.

Test coverage:
  - Valid static token  (DEPTHFUSION_MCP_TOKEN) → 200 on /sse and /messages
  - No Authorization header                      → 401
  - Malformed / expired JWT (JWKS path)          → 401
  - Token present but no auth backend configured → 401 (fail-closed)
  - /health is always unauthenticated            → 200
"""
from __future__ import annotations

import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import depthfusion.mcp.http_server as _mod
from depthfusion.identity.errors import TokenExpiredError
from depthfusion.mcp.http_server import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_token_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset module-level _token_validator to None before every test."""
    monkeypatch.setattr(_mod, "_token_validator", None)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# /health — always unauthenticated
# ---------------------------------------------------------------------------

def test_health_no_auth(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Static-token auth (DEPTHFUSION_MCP_TOKEN path)
# ---------------------------------------------------------------------------

def test_sse_auth_passes_via_messages(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid static token → auth passes on /messages (404, not 401).

    We test auth acceptance via /messages rather than /sse to avoid blocking on
    the long-lived SSE generator.  Auth is the same dependency for both endpoints.
    """
    monkeypatch.setenv("DEPTHFUSION_MCP_TOKEN", "test-secret-token")
    monkeypatch.setattr(_mod, "_get_token_validator", lambda: None)

    resp = client.post(
        "/messages?sessionId=test-session",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
    )
    # Auth passed; unknown session → 404, not 401
    assert resp.status_code == 404


def test_messages_valid_static_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid static token → /messages returns 404 (no session) not 401."""
    monkeypatch.setenv("DEPTHFUSION_MCP_TOKEN", "test-secret-token")
    monkeypatch.setattr(_mod, "_get_token_validator", lambda: None)

    resp = client.post(
        "/messages?sessionId=nonexistent",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
    )
    # Auth passed; session doesn't exist → 404 (not 401)
    assert resp.status_code == 404


def test_sse_wrong_static_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong static token → 401."""
    monkeypatch.setenv("DEPTHFUSION_MCP_TOKEN", "correct-token")
    monkeypatch.setattr(_mod, "_get_token_validator", lambda: None)

    resp = client.get(
        "/sse",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# No Authorization header → 401 (fail-closed)
# ---------------------------------------------------------------------------

def test_sse_no_auth_header(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEPTHFUSION_MCP_TOKEN", "some-token")
    resp = client.get("/sse")
    assert resp.status_code == 401


def test_messages_no_auth_header(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEPTHFUSION_MCP_TOKEN", "some-token")
    resp = client.post(
        "/messages?sessionId=any",
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# No auth backend configured at all → 401 (fail-closed, T-539 requirement)
# ---------------------------------------------------------------------------

def test_sse_no_auth_backend_configured(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token present but no static token and no JWKS validator → 401."""
    # No DEPTHFUSION_MCP_TOKEN set, no JWKS vars, validator returns None
    monkeypatch.delenv("DEPTHFUSION_MCP_TOKEN", raising=False)
    monkeypatch.setattr(_mod, "_get_token_validator", lambda: None)

    resp = client.get(
        "/sse",
        headers={"Authorization": "Bearer some-bearer-token"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# JWKS path — expired/invalid JWT → 401
# ---------------------------------------------------------------------------

def test_sse_expired_jwt(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expired JWT when JWKS validator is configured → 401."""

    async def _raise_expired(token: str) -> None:
        raise TokenExpiredError("Token has expired")

    mock_validator = MagicMock()
    mock_validator.validate = AsyncMock(side_effect=TokenExpiredError("expired"))

    monkeypatch.setattr(_mod, "_get_token_validator", lambda: mock_validator)
    monkeypatch.delenv("DEPTHFUSION_MCP_TOKEN", raising=False)

    resp = client.get(
        "/sse",
        headers={"Authorization": "Bearer expired.jwt.token"},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json().get("detail", "").lower()


def test_sse_malformed_bearer_prefix(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authorization header without 'Bearer ' prefix → 401."""
    monkeypatch.setenv("DEPTHFUSION_MCP_TOKEN", "some-token")

    resp = client.get(
        "/sse",
        headers={"Authorization": "Token some-token"},
    )
    assert resp.status_code == 401
