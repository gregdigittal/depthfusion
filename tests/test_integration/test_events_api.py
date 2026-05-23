"""REST API tests for /v1/events/* endpoints — S-142 / T-488."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with a clean in-memory graph and no Redis dependency."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    monkeypatch.setenv("DEPTHFUSION_GRAPH_JSON", str(tmp_path / "graph.json"))
    monkeypatch.delenv("DEPTHFUSION_REDIS_URL", raising=False)
    monkeypatch.delenv("DEPTHFUSION_API_TOKEN", raising=False)

    # Reset module-level singleton so each test gets a fresh EventStore
    import depthfusion.api.events as ev_mod
    ev_mod._event_store = None

    from depthfusion.api.rest import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def authed_client(tmp_path, monkeypatch):
    """TestClient with Bearer token auth configured."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    monkeypatch.setenv("DEPTHFUSION_GRAPH_JSON", str(tmp_path / "graph.json"))
    monkeypatch.delenv("DEPTHFUSION_REDIS_URL", raising=False)
    monkeypatch.setenv("DEPTHFUSION_API_TOKEN", "test-token-secret")

    import depthfusion.api.events as ev_mod
    ev_mod._event_store = None

    from depthfusion.api.rest import app
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# POST /v1/events/publish — happy path (no auth required when no token set)
# ---------------------------------------------------------------------------

def test_publish_event_returns_event_id(client):
    resp = client.post("/v1/events/publish", json={
        "agent_id": "agent-a",
        "project_slug": "test-proj",
        "memory_refs": ["mem1", "mem2"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "event_id" in data
    assert data["indexed"] is True
    assert len(data["event_id"]) == 12


def test_publish_event_default_event_type(client):
    resp = client.post("/v1/events/publish", json={
        "agent_id": "agent-a",
        "project_slug": "proj",
        "memory_refs": ["m1"],
    })
    assert resp.status_code == 200


def test_publish_event_with_session_id(client):
    resp = client.post("/v1/events/publish", json={
        "agent_id": "agent-a",
        "project_slug": "proj",
        "memory_refs": ["m1"],
        "session_id": "sess-99",
    })
    assert resp.status_code == 200
    assert resp.json()["indexed"] is True


def test_publish_event_missing_required_fields(client):
    resp = client.post("/v1/events/publish", json={"agent_id": "agent-a"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /v1/events/publish — auth (401 paths)
# ---------------------------------------------------------------------------

def test_publish_returns_401_without_token(authed_client):
    resp = authed_client.post("/v1/events/publish", json={
        "agent_id": "agent-a",
        "project_slug": "proj",
        "memory_refs": ["m1"],
    })
    assert resp.status_code == 401


def test_publish_returns_401_with_wrong_token(authed_client):
    resp = authed_client.post(
        "/v1/events/publish",
        json={"agent_id": "agent-a", "project_slug": "proj", "memory_refs": ["m1"]},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


def test_publish_succeeds_with_correct_token(authed_client):
    resp = authed_client.post(
        "/v1/events/publish",
        json={"agent_id": "agent-a", "project_slug": "proj", "memory_refs": ["m1"]},
        headers={"Authorization": "Bearer test-token-secret"},
    )
    assert resp.status_code == 200
    assert "event_id" in resp.json()


# ---------------------------------------------------------------------------
# GET /v1/events/stream
# ---------------------------------------------------------------------------

def test_stream_requires_projects_param(client):
    resp = client.get("/v1/events/stream")
    assert resp.status_code == 422


def test_stream_returns_error_frame_when_no_backend(client):
    """Without Redis, subscribe_stream raises RuntimeError; endpoint returns error SSE frame."""
    resp = client.get("/v1/events/stream?projects=test-proj")
    # Status 200 (SSE starts before the error is detected)
    assert resp.status_code == 200
    content = resp.text
    assert "data:" in content
    assert "error" in content


def test_stream_returns_401_without_token(authed_client):
    resp = authed_client.get("/v1/events/stream?projects=test-proj")
    assert resp.status_code == 401


def test_stream_with_mocked_backend(tmp_path, monkeypatch):
    """Verify SSE output format when EventStore yields real events."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    monkeypatch.setenv("DEPTHFUSION_GRAPH_JSON", str(tmp_path / "graph.json"))
    monkeypatch.delenv("DEPTHFUSION_API_TOKEN", raising=False)

    import depthfusion.api.events as ev_mod
    ev_mod._event_store = None

    from depthfusion.core.event_store import InMemoryStreamBackend, EventStore
    from depthfusion.graph.store import get_store

    graph = get_store(graph_json_path=tmp_path / "graph.json")
    stream = InMemoryStreamBackend()
    ev_mod._event_store = EventStore(graph=graph, stream=stream)

    from depthfusion.api.rest import app
    client = TestClient(app, raise_server_exceptions=True)

    # Publish one event so the stream has something to yield
    pub_resp = client.post("/v1/events/publish", json={
        "agent_id": "agent-a",
        "project_slug": "test-proj",
        "memory_refs": ["mem1"],
    })
    assert pub_resp.status_code == 200

    stream_resp = client.get(
        "/v1/events/stream?projects=test-proj&since_id=0",
    )
    assert stream_resp.status_code == 200
    lines = [l for l in stream_resp.text.splitlines() if l.startswith("data:")]
    assert len(lines) == 1
    import json
    payload = json.loads(lines[0][len("data: "):])
    assert payload["type"] == "event"
    assert payload["project"] == "test-proj"


# ---------------------------------------------------------------------------
# Tailscale bind validation (T-485)
# ---------------------------------------------------------------------------

def test_tailscale_bind_requires_token(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_API_TAILSCALE", "1")
    monkeypatch.delenv("DEPTHFUSION_API_TOKEN", raising=False)

    from depthfusion.api.rest import validate_public_bind_config
    with pytest.raises(ValueError, match="DEPTHFUSION_API_TOKEN must be set"):
        validate_public_bind_config()


def test_tailscale_bind_passes_with_token(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_API_TAILSCALE", "1")
    monkeypatch.setenv("DEPTHFUSION_API_TOKEN", "some-token")
    monkeypatch.delenv("DEPTHFUSION_API_PUBLIC", raising=False)

    from depthfusion.api.rest import validate_public_bind_config
    validate_public_bind_config()  # must not raise


def test_get_tailscale_ip_returns_none_when_unavailable(monkeypatch):
    """get_tailscale_ip() must return None gracefully when tailscale not installed."""
    with patch("depthfusion.api.rest.subprocess.run", side_effect=FileNotFoundError):
        from importlib import reload
        import depthfusion.api.rest as rest_mod
        # Call directly without reload to avoid module-level side effects
        result = rest_mod.get_tailscale_ip()
    assert result is None


def test_get_tailscale_ip_returns_none_on_nonzero_exit(monkeypatch):
    import subprocess as _sp
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "not connected"
    with patch("depthfusion.api.rest.subprocess.run", return_value=mock_result):
        from depthfusion.api.rest import get_tailscale_ip
        result = get_tailscale_ip()
    assert result is None


def test_get_tailscale_ip_returns_ip_on_success():
    import subprocess as _sp
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "100.64.1.42\n"
    with patch("depthfusion.api.rest.subprocess.run", return_value=mock_result):
        from depthfusion.api.rest import get_tailscale_ip
        result = get_tailscale_ip()
    assert result == "100.64.1.42"
