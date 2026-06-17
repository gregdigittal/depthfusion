"""Route-authorization sweep — T-550 / S-157.

Verifies that every non-health route in rest.py, events.py, and mcp/http_server.py:
  1. Accepts requests when a valid Principal is injected via dependency override.
  2. Returns a 401 or 503 when no auth is provided (i.e., the dependency gate fires).
  3. Never bypasses the ``require_principal`` dependency.

These tests use ``app.dependency_overrides`` to inject a known Principal so the
route body runs, and a separate pass without the override to confirm the gate.

NOTE: This module deliberately does NOT use ``from __future__ import annotations``
so that FastAPI can resolve ``Annotated[Principal, Depends(...)]`` at route
registration time (PEP 563 string-ification breaks embedded Depends).
"""
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from depthfusion.identity.models import Principal


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _fake_principal() -> Principal:
    return Principal(
        principal_id="test-user-001",
        upn="test@example.com",
        display_name="Test User",
        groups=["g-admins"],
    )


def _make_override(dep_callable):
    """Return a FastAPI dependency override that yields _fake_principal()."""
    async def _override():
        return _fake_principal()
    return _override


# ===========================================================================
# rest.py + events.py (shared FastAPI app)
# ===========================================================================

@pytest.fixture()
def rest_client_authed(tmp_path, monkeypatch):
    """TestClient with a valid Principal injected — routes run normally."""
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))

    from depthfusion.api.auth import require_principal
    from depthfusion.api.rest import app

    async def _override():
        return _fake_principal()

    app.dependency_overrides[require_principal] = _override
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def rest_client_no_auth(tmp_path, monkeypatch):
    """TestClient with NO dependency override — the auth gate must fire.

    OIDC env vars are absent so ``_require_principal_dep`` is an
    ``_UnconfiguredPrincipalDep`` that always returns 503.
    We must NOT reload ``auth_mod`` here — reloading creates new function
    objects and breaks the identity contract that ``app.dependency_overrides``
    relies on.  The module-level ``_require_principal_dep`` is already an
    unconfigured sentinel when tests start (no OIDC vars in the test env).
    """
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))
    monkeypatch.delenv("DEPTHFUSION_JWKS_URI", raising=False)
    monkeypatch.delenv("DEPTHFUSION_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("DEPTHFUSION_OIDC_AUDIENCE", raising=False)

    from depthfusion.api.rest import app
    app.dependency_overrides.clear()
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Health route is unauthenticated
# ---------------------------------------------------------------------------

def test_route_health_is_public(rest_client_no_auth):
    """/health must respond 200 without any auth."""
    resp = rest_client_no_auth.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Core REST routes — authenticated access succeeds
# ---------------------------------------------------------------------------

def test_route_cognitive_state_authed(rest_client_authed):
    resp = rest_client_authed.get("/v1/cognitive-state?project_id=test")
    assert resp.status_code == 200
    assert "total_memories" in resp.json()


def test_route_memories_authed(rest_client_authed):
    resp = rest_client_authed.get("/v1/memories?project_id=test")
    assert resp.status_code == 200
    assert "memories" in resp.json()


def test_route_status_authed(rest_client_authed):
    resp = rest_client_authed.get("/status")
    assert resp.status_code == 200


def test_route_capabilities_authed(rest_client_authed):
    resp = rest_client_authed.get("/capabilities")
    assert resp.status_code == 200


def test_route_discoveries_list_authed(rest_client_authed, tmp_path, monkeypatch):
    resp = rest_client_authed.get("/discoveries")
    assert resp.status_code == 200
    assert "items" in resp.json()


def test_route_query_discoveries_authed(rest_client_authed):
    resp = rest_client_authed.get("/query/discoveries")
    assert resp.status_code == 200
    assert "items" in resp.json()


def test_route_query_sessions_authed(rest_client_authed):
    resp = rest_client_authed.get("/query/sessions")
    assert resp.status_code == 200
    assert "items" in resp.json()


def test_route_query_aggregate_authed(rest_client_authed):
    resp = rest_client_authed.get("/query/aggregate")
    assert resp.status_code == 200
    assert "total_events" in resp.json()


def test_route_query_telemetry_authed(rest_client_authed):
    resp = rest_client_authed.get("/query/telemetry")
    assert resp.status_code == 200
    assert "rows" in resp.json()


def test_route_query_telemetry_aggregate_authed(rest_client_authed):
    resp = rest_client_authed.get("/query/telemetry/aggregate")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# All protected routes return 401 / 503 without auth (no override)
# ---------------------------------------------------------------------------
# We patch the dep to NOT be overridden; the _UnconfiguredPrincipalDep returns 503
# since OIDC env vars are absent.

PROTECTED_REST_ROUTES = [
    ("GET", "/v1/cognitive-state?project_id=test"),
    ("GET", "/v1/memories?project_id=test"),
    ("GET", "/status"),
    ("GET", "/tiers/status"),
    ("GET", "/capabilities"),
    ("GET", "/hnsw/capability"),
    ("GET", "/cognitive-state"),
    ("GET", "/graph/status"),
    ("GET", "/discoveries"),
    ("GET", "/query/discoveries"),
    ("GET", "/query/sessions"),
    ("GET", "/query/aggregate"),
    ("GET", "/query/telemetry"),
    ("GET", "/query/telemetry/aggregate"),
    ("POST", "/session/seed"),
    ("POST", "/session/compress"),
    ("POST", "/session/tags"),
    ("POST", "/recall"),
    ("POST", "/recall/feedback"),
    ("POST", "/context"),
    ("POST", "/context/retrieve"),
    ("POST", "/auto-learn"),
    ("POST", "/graph/traverse"),
    ("POST", "/run/recursive"),
    ("POST", "/discoveries/inspect"),
    ("POST", "/discoveries/confirm"),
    ("POST", "/discoveries/pin"),
    ("POST", "/discoveries/supersede"),
    ("POST", "/discoveries/prune"),
    ("POST", "/telemetry"),
    ("POST", "/decisions"),
    ("POST", "/incidents"),
    ("POST", "/outcomes"),
    ("POST", "/skills/candidates"),
]


def test_route_walker_rest_all_require_auth(tmp_path, monkeypatch):
    """Walk every protected REST route and confirm no route lets through unauthenticated requests.

    With OIDC env vars absent, _UnconfiguredPrincipalDep returns 503.
    Either 401 (if a configured validator rejects) or 503 (unconfigured) is
    acceptable — both indicate the auth gate fired instead of serving content.

    NOTE: Do NOT reload depthfusion.api.auth here.  Reloading creates new
    function objects for ``require_principal``, breaking the identity contract
    that routes (which captured the old reference) and ``dependency_overrides``
    both rely on.
    """
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))
    monkeypatch.delenv("DEPTHFUSION_JWKS_URI", raising=False)
    monkeypatch.delenv("DEPTHFUSION_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("DEPTHFUSION_OIDC_AUDIENCE", raising=False)

    from depthfusion.api.rest import app
    app.dependency_overrides.clear()

    with TestClient(app, raise_server_exceptions=False) as client:
        for method, path in PROTECTED_REST_ROUTES:
            if method == "GET":
                resp = client.get(path)
            else:
                resp = getattr(client, method.lower())(path, json={})
            assert resp.status_code in (401, 503), (
                f"{method} {path} returned {resp.status_code} without auth — "
                "expected 401 or 503 (auth gate must fire)"
            )


def test_route_health_not_in_protected_list():
    """/health must not appear in the protected route list (it's public)."""
    for _method, path in PROTECTED_REST_ROUTES:
        assert path != "/health", "/health must be public, not in protected list"


# ===========================================================================
# events.py router (mounted on rest.py app)
# ===========================================================================

PROTECTED_EVENTS_ROUTES = [
    ("POST", "/v1/events/publish"),
    ("GET", "/v1/events/seed?projects=test"),
    ("GET", "/v1/graph/agent/agent-1/trail"),
    ("GET", "/v1/graph/memory/mem-1/observers"),
]


def test_route_walker_events_all_require_auth(tmp_path, monkeypatch):
    """Every fabric route must reject unauthenticated requests.

    NOTE: Do NOT reload depthfusion.api.auth — see walker test above.
    """
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))
    monkeypatch.delenv("DEPTHFUSION_JWKS_URI", raising=False)
    monkeypatch.delenv("DEPTHFUSION_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("DEPTHFUSION_OIDC_AUDIENCE", raising=False)

    from depthfusion.api.rest import app
    app.dependency_overrides.clear()

    with TestClient(app, raise_server_exceptions=False) as client:
        for method, path in PROTECTED_EVENTS_ROUTES:
            if method == "GET":
                resp = client.get(path)
            else:
                resp = getattr(client, method.lower())(path, json={})
            assert resp.status_code in (401, 503), (
                f"{method} {path} returned {resp.status_code} without auth — "
                "expected 401 or 503"
            )


def test_route_events_publish_authed(rest_client_authed):
    """POST /v1/events/publish responds (not 401/503) when authed."""
    body = {
        "agent_id": "agent-test",
        "project_slug": "test-proj",
        "memory_refs": [],
    }
    resp = rest_client_authed.post("/v1/events/publish", json=body)
    # 200 or any non-auth error is fine — we just confirm auth gate passed
    assert resp.status_code not in (401, 403, 503)


def test_route_events_seed_authed(rest_client_authed):
    """GET /v1/events/seed responds when authed."""
    resp = rest_client_authed.get("/v1/events/seed?projects=test")
    assert resp.status_code not in (401, 403, 503)


# ===========================================================================
# mcp/http_server.py
# ===========================================================================

@pytest.fixture()
def mcp_client_authed():
    """TestClient for the MCP HTTP/SSE app with Principal injected."""
    from depthfusion.api.auth import require_principal
    from depthfusion.mcp.http_server import app as mcp_app

    async def _override():
        return _fake_principal()

    mcp_app.dependency_overrides[require_principal] = _override
    client = TestClient(mcp_app, raise_server_exceptions=False)
    yield client
    mcp_app.dependency_overrides.clear()


@pytest.fixture()
def mcp_client_no_auth(monkeypatch):
    """TestClient for MCP app with NO override — gate must fire.

    OIDC vars absent → ``_UnconfiguredPrincipalDep`` already in place.
    Do NOT reload auth_mod — see rest_client_no_auth docstring.
    """
    monkeypatch.delenv("DEPTHFUSION_JWKS_URI", raising=False)
    monkeypatch.delenv("DEPTHFUSION_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("DEPTHFUSION_OIDC_AUDIENCE", raising=False)

    from depthfusion.mcp.http_server import app as mcp_app
    mcp_app.dependency_overrides.clear()
    client = TestClient(mcp_app, raise_server_exceptions=False)
    yield client
    mcp_app.dependency_overrides.clear()


def test_route_mcp_health_is_public(mcp_client_no_auth):
    """/health on the MCP server is unauthenticated."""
    resp = mcp_client_no_auth.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_route_mcp_sse_requires_auth(mcp_client_no_auth):
    """GET /sse must be rejected without auth."""
    resp = mcp_client_no_auth.get("/sse")
    assert resp.status_code in (401, 503)


def test_route_mcp_messages_requires_auth(mcp_client_no_auth):
    """POST /messages must be rejected without auth."""
    resp = mcp_client_no_auth.post("/messages?sessionId=fake", json={})
    assert resp.status_code in (401, 503)


def test_route_mcp_sse_authed():
    """GET /sse has ``require_principal`` in its dependency graph.

    The SSE endpoint streams indefinitely and cannot be consumed by TestClient
    without hanging (the async generator loops until the client disconnects,
    but TestClient's ASGI runner doesn't propagate disconnection during
    ``stream()``).  We therefore verify auth presence structurally: inspect
    FastAPI's route dependency list and confirm ``require_principal`` appears.

    The behavioural proof (that the gate fires without auth) is already covered
    by ``test_route_mcp_sse_requires_auth``, which confirms 401/503 without
    a credential.
    """
    from depthfusion.api.auth import require_principal
    from depthfusion.mcp.http_server import app as mcp_app

    sse_route = next(
        (r for r in mcp_app.routes if getattr(r, "path", None) == "/sse"),
        None,
    )
    assert sse_route is not None, "GET /sse route not found on MCP app"

    # FastAPI stores dependencies in route.dependencies (list of Depends) and
    # also in route.dependant.dependencies (recursive).  We check both.
    deps = getattr(sse_route, "dependencies", [])
    dep_callables = [d.dependency for d in deps]

    # Also check via route.dependant if available
    dependant = getattr(sse_route, "dependant", None)
    if dependant:
        for dep_model in dependant.dependencies:
            dep_callables.append(dep_model.call)

    assert require_principal in dep_callables, (
        "require_principal not found in GET /sse route dependencies — "
        "auth sweep incomplete"
    )


def test_route_mcp_messages_session_not_found_authed(mcp_client_authed):
    """POST /messages with no session returns 404 (auth passed, session missing)."""
    resp = mcp_client_authed.post("/messages?sessionId=nonexistent", json={"jsonrpc": "2.0"})
    # Auth passed; session not found → 404
    assert resp.status_code == 404
