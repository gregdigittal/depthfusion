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


# ---------------------------------------------------------------------------
# Helpers for S-144 provenance tests
# ---------------------------------------------------------------------------

def _fabric_client(tmp_path, monkeypatch):
    """TestClient wired to a real in-memory EventStore (no Redis, no auth)."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    monkeypatch.setenv("DEPTHFUSION_GRAPH_JSON", str(tmp_path / "graph.json"))
    monkeypatch.delenv("DEPTHFUSION_REDIS_URL", raising=False)
    monkeypatch.delenv("DEPTHFUSION_API_TOKEN", raising=False)

    import depthfusion.api.events as ev_mod
    ev_mod._event_store = None

    from depthfusion.api.rest import app
    return TestClient(app, raise_server_exceptions=True)


def _setup_fabric_store(tmp_path):
    """Return a real EventStore backed by a tmp-path SQLite graph."""
    from depthfusion.core.event_store import EventStore, InMemoryStreamBackend
    from depthfusion.graph.store import get_store
    graph = get_store(graph_json_path=tmp_path / "graph.json")
    return EventStore(graph=graph, stream=InMemoryStreamBackend())


# ---------------------------------------------------------------------------
# GET /v1/graph/agent/{agent_id}/trail  (S-144 / T-494 / T-496)
# ---------------------------------------------------------------------------

class TestAgentTrail:
    def test_trail_returns_empty_list_for_unknown_agent(self, tmp_path, monkeypatch):
        client = _fabric_client(tmp_path, monkeypatch)
        resp = client.get("/v1/graph/agent/ghost-agent/trail")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trail"] == []
        assert data["count"] == 0

    def test_trail_returns_event_for_publishing_agent(self, tmp_path, monkeypatch):
        import asyncio
        client = _fabric_client(tmp_path, monkeypatch)

        # Pre-seed state via publish endpoint (creates AGENT_PUBLISHED EventEntity)
        resp = client.post("/v1/events/publish", json={
            "agent_id": "trail-agent",
            "project_slug": "proj-a",
            "memory_refs": ["mem-x"],
        })
        assert resp.status_code == 200

        trail_resp = client.get("/v1/graph/agent/trail-agent/trail")
        assert trail_resp.status_code == 200
        data = trail_resp.json()
        assert data["count"] >= 1
        entry = data["trail"][0]
        assert entry["project"] == "proj-a"
        assert "entity_id" in entry
        assert "first_seen" in entry

    def test_trail_filters_by_project(self, tmp_path, monkeypatch):
        client = _fabric_client(tmp_path, monkeypatch)

        # Publish to two projects
        for slug in ("proj-x", "proj-y"):
            client.post("/v1/events/publish", json={
                "agent_id": "multi-proj-agent",
                "project_slug": slug,
                "memory_refs": ["mem-1"],
            })

        # Filter to proj-x only
        resp = client.get("/v1/graph/agent/multi-proj-agent/trail?project=proj-x")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert all(e["project"] == "proj-x" for e in data["trail"])

    def test_trail_sorted_ascending_by_timestamp(self, tmp_path, monkeypatch):
        client = _fabric_client(tmp_path, monkeypatch)

        for i in range(3):
            client.post("/v1/events/publish", json={
                "agent_id": "order-agent",
                "project_slug": "proj",
                "memory_refs": [f"mem-{i}"],
            })

        resp = client.get("/v1/graph/agent/order-agent/trail")
        assert resp.status_code == 200
        trail = resp.json()["trail"]
        timestamps = [e["first_seen"] for e in trail]
        assert timestamps == sorted(timestamps)

    def test_trail_requires_auth_when_token_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_MODE", "local")
        monkeypatch.setenv("DEPTHFUSION_GRAPH_JSON", str(tmp_path / "graph.json"))
        monkeypatch.setenv("DEPTHFUSION_API_TOKEN", "trail-secret")
        monkeypatch.delenv("DEPTHFUSION_REDIS_URL", raising=False)

        import depthfusion.api.events as ev_mod
        ev_mod._event_store = None

        from depthfusion.api.rest import app
        client = TestClient(app, raise_server_exceptions=True)

        # No token → 401
        resp = client.get("/v1/graph/agent/any-agent/trail")
        assert resp.status_code == 401

        # Correct token → 200
        resp = client.get(
            "/v1/graph/agent/any-agent/trail",
            headers={"Authorization": "Bearer trail-secret"},
        )
        assert resp.status_code == 200

    def test_trail_three_agents_all_indexed(self, tmp_path, monkeypatch):
        """3 concurrent-style agents each publish; all three appear in their respective trails."""
        client = _fabric_client(tmp_path, monkeypatch)

        agents = ["alpha", "beta", "gamma"]
        for agent_id in agents:
            client.post("/v1/events/publish", json={
                "agent_id": agent_id,
                "project_slug": "shared-proj",
                "memory_refs": ["shared-mem"],
            })

        for agent_id in agents:
            resp = client.get(f"/v1/graph/agent/{agent_id}/trail")
            assert resp.status_code == 200
            assert resp.json()["count"] >= 1, f"{agent_id} has no trail"


# ---------------------------------------------------------------------------
# GET /v1/graph/memory/{entity_id}/observers  (S-144 / T-495 / T-496 / T-497)
# ---------------------------------------------------------------------------

class TestMemoryObservers:
    def test_observers_returns_404_for_unknown_entity(self, tmp_path, monkeypatch):
        client = _fabric_client(tmp_path, monkeypatch)
        resp = client.get("/v1/graph/memory/does-not-exist/observers")
        assert resp.status_code == 404

    def test_observers_returns_empty_list_when_no_edges(self, tmp_path, monkeypatch):
        """Memory entity exists but has no AGENT_RECEIVED edges yet."""
        client = _fabric_client(tmp_path, monkeypatch)

        # Publish an event so the memory entity is indexed
        pub = client.post("/v1/events/publish", json={
            "agent_id": "agent-a",
            "project_slug": "proj",
            "memory_refs": ["mem-abc"],
        })
        assert pub.status_code == 200
        event_id = pub.json()["event_id"]

        # Find the memory entity (it's the event entity — publish() returns event_id)
        # The event entity exists; observers only count AGENT_RECEIVED edges
        resp = client.get(f"/v1/graph/memory/{event_id}/observers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["observers"] == []

    def test_observers_counts_received_edges(self, tmp_path, monkeypatch):
        """After creating AGENT_RECEIVED edges, /observers returns them."""
        import asyncio
        import depthfusion.api.events as ev_mod

        monkeypatch.setenv("DEPTHFUSION_MODE", "local")
        monkeypatch.setenv("DEPTHFUSION_GRAPH_JSON", str(tmp_path / "graph.json"))
        monkeypatch.delenv("DEPTHFUSION_REDIS_URL", raising=False)
        monkeypatch.delenv("DEPTHFUSION_API_TOKEN", raising=False)
        ev_mod._event_store = None

        store = _setup_fabric_store(tmp_path)
        ev_mod._event_store = store

        from depthfusion.api.rest import app
        client = TestClient(app, raise_server_exceptions=True)

        # Publish content to create a MemoryEntity via publish_memory
        r = asyncio.run(store.publish_memory(
            content="shared knowledge", agent_id="agent-a", project_slug="proj",
        ))
        memory_id = r["memory_id"]

        # Manually add AGENT_RECEIVED edges (simulates SSE subscribers receiving the event)
        from depthfusion.graph.types import Edge
        for i, agent in enumerate(("agent-b", "agent-c")):
            store.graph.upsert_edge(Edge(
                edge_id=f"{memory_id}-recv-{agent}", source_id=memory_id, target_id=agent,
                relationship="AGENT_RECEIVED", weight=1.0, signals=["fabric"],
                metadata={"agent_id": agent, "timestamp": f"2026-01-01T00:0{i}:00+00:00"},
            ))

        resp = client.get(f"/v1/graph/memory/{memory_id}/observers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        agent_ids = {o["agent_id"] for o in data["observers"]}
        assert agent_ids == {"agent-b", "agent-c"}

    def test_observers_requires_auth_when_token_set(self, tmp_path, monkeypatch):
        import asyncio
        import depthfusion.api.events as ev_mod

        monkeypatch.setenv("DEPTHFUSION_MODE", "local")
        monkeypatch.setenv("DEPTHFUSION_GRAPH_JSON", str(tmp_path / "graph.json"))
        monkeypatch.setenv("DEPTHFUSION_API_TOKEN", "obs-secret")
        monkeypatch.delenv("DEPTHFUSION_REDIS_URL", raising=False)
        ev_mod._event_store = None

        store = _setup_fabric_store(tmp_path)
        ev_mod._event_store = store

        r = asyncio.run(store.publish_memory("content", "agent-x", "proj"))
        memory_id = r["memory_id"]

        from depthfusion.api.rest import app
        client = TestClient(app, raise_server_exceptions=True)

        # No token → 401
        resp = client.get(f"/v1/graph/memory/{memory_id}/observers")
        assert resp.status_code == 401

        # Correct token → 200
        resp = client.get(
            f"/v1/graph/memory/{memory_id}/observers",
            headers={"Authorization": "Bearer obs-secret"},
        )
        assert resp.status_code == 200

    def test_dedup_produces_one_memory_n_event_entities(self, tmp_path, monkeypatch):
        """T-497: 10 concurrent publishes of identical content → 1 MemoryEntity, 10 EventEntities."""
        import asyncio
        import depthfusion.api.events as ev_mod

        monkeypatch.setenv("DEPTHFUSION_MODE", "local")
        monkeypatch.setenv("DEPTHFUSION_GRAPH_JSON", str(tmp_path / "graph.json"))
        monkeypatch.delenv("DEPTHFUSION_REDIS_URL", raising=False)
        monkeypatch.delenv("DEPTHFUSION_API_TOKEN", raising=False)
        ev_mod._event_store = None

        store = _setup_fabric_store(tmp_path)
        ev_mod._event_store = store

        # Publish same content from 10 different agents
        N = 10
        results = [
            asyncio.run(store.publish_memory(
                content="identical content",
                agent_id=f"agent-{i}",
                project_slug="proj",
            ))
            for i in range(N)
        ]

        # All return the same memory_id
        memory_ids = {r["memory_id"] for r in results}
        assert len(memory_ids) == 1, "dedup should produce exactly 1 MemoryEntity"
        memory_id = next(iter(memory_ids))

        # 9 of 10 should be deduped (first one is new)
        deduped_count = sum(1 for r in results if r["deduped"])
        assert deduped_count == N - 1

        # Verify via graph: only 1 memory entity, N event entities
        all_entities = store.graph.all_entities()
        memory_entities = [e for e in all_entities if e.type == "memory"]
        event_entities = [e for e in all_entities if e.type == "event"]
        assert len(memory_entities) == 1
        assert len(event_entities) == N

        # Verify the /observers endpoint finds the memory entity
        from depthfusion.api.rest import app
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get(f"/v1/graph/memory/{memory_id}/observers")
        assert resp.status_code == 200
