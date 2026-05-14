"""Integration tests for REST query endpoints — E-32 S-104."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))
    monkeypatch.delenv("DEPTHFUSION_QUERY_API_KEY", raising=False)
    from importlib import reload
    import depthfusion.api.rest as rest_module
    reload(rest_module)
    from fastapi.testclient import TestClient
    return TestClient(rest_module.app)


@pytest.fixture
def discoveries_dir(tmp_path) -> Path:
    d = tmp_path / "discoveries"
    d.mkdir()
    # File 1: with frontmatter
    (d / "2026-05-01-depthfusion-foo.md").write_text(
        "---\ndate: 2026-05-01\nproject: depthfusion\ntags: foo, bar\n---\n\n# Foo\n\nContent here.\n"
    )
    # File 2: different project
    (d / "2026-05-02-kitabu-baz.md").write_text(
        "---\ndate: 2026-05-02\nproject: kitabu\ntags: baz\n---\n\n# Baz\n"
    )
    # File 3: no frontmatter
    (d / "2026-05-03-notes.md").write_text("# Just notes\n\nNo frontmatter here.\n")
    return d


@pytest.fixture
def metrics_dir(tmp_path) -> Path:
    d = tmp_path / "metrics"
    d.mkdir()
    recall_file = d / "2026-05-13-recall.jsonl"
    events = [
        {
            "timestamp": "2026-05-13T03:21:11.851969+00:00",
            "event": "recall_query",
            "event_subtype": "ok",
            "mode": "vps",
            "result_count": 3,
            "total_latency_ms": 725.0,
            "config_version_id": "fd3690fc9494",
            "latency_ms_per_capability": {"reranker": 200.0, "embedding": 1.5},
            "backend_fallback_chain": {"reranker": ["haiku"]},
            "backend_used": {"reranker": "haiku"},
            "query_hash": "abc123",
        },
        {
            "timestamp": "2026-05-13T04:00:00.000000+00:00",
            "event": "recall_query",
            "event_subtype": "ok",
            "mode": "vps-gpu",
            "result_count": 5,
            "total_latency_ms": 400.0,
            "config_version_id": "d2e73ca63a91",
            "latency_ms_per_capability": {"reranker": 100.0, "embedding": 0.5},
            "backend_fallback_chain": {},
            "backend_used": {"reranker": "vllm"},
            "query_hash": "def456",
        },
    ]
    recall_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return d


# ---------------------------------------------------------------------------
# /query/discoveries
# ---------------------------------------------------------------------------

def test_discoveries_returns_all(client, monkeypatch, discoveries_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_DISCOVERIES_DIR", discoveries_dir)
    resp = client.get("/query/discoveries")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert data["total"] == 3
    assert data["next_cursor"] is None


def test_discoveries_filter_by_project(client, monkeypatch, discoveries_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_DISCOVERIES_DIR", discoveries_dir)
    resp = client.get("/query/discoveries?project=depthfusion")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["items"][0]["project"] == "depthfusion"


def test_discoveries_filter_by_tags(client, monkeypatch, discoveries_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_DISCOVERIES_DIR", discoveries_dir)
    resp = client.get("/query/discoveries?tags=foo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert "foo" in data["items"][0]["tags"]


def test_discoveries_date_range_filter(client, monkeypatch, discoveries_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_DISCOVERIES_DIR", discoveries_dir)
    resp = client.get("/query/discoveries?from=2026-05-02&to=2026-05-02")
    assert resp.status_code == 200
    data = resp.json()
    # Only the 2026-05-02 file matches
    assert data["count"] == 1
    assert data["items"][0]["date"] == "2026-05-02"


def test_discoveries_pagination(client, monkeypatch, discoveries_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_DISCOVERIES_DIR", discoveries_dir)
    # First page
    resp = client.get("/query/discoveries?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["next_cursor"] is not None
    # Second page
    resp2 = client.get(f"/query/discoveries?limit=2&cursor={data['next_cursor']}")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["count"] == 1
    assert data2["next_cursor"] is None


def test_discoveries_limit_enforced(client, monkeypatch, discoveries_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_DISCOVERIES_DIR", discoveries_dir)
    resp = client.get("/query/discoveries?limit=1001")
    assert resp.status_code == 422  # exceeds max 1000


# ---------------------------------------------------------------------------
# /query/sessions
# ---------------------------------------------------------------------------

def test_sessions_returns_all(client, monkeypatch, metrics_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_METRICS_DIR", metrics_dir)
    resp = client.get("/query/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["total"] == 2


def test_sessions_filter_by_agent_mode(client, monkeypatch, metrics_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_METRICS_DIR", metrics_dir)
    resp = client.get("/query/sessions?agent=vps")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["items"][0]["mode"] == "vps"


def test_sessions_date_range_filter(client, monkeypatch, metrics_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_METRICS_DIR", metrics_dir)
    resp = client.get("/query/sessions?from=2026-05-13T03:00:00Z&to=2026-05-13T03:30:00Z")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1  # only first event falls in window


def test_sessions_pagination(client, monkeypatch, metrics_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_METRICS_DIR", metrics_dir)
    resp = client.get("/query/sessions?limit=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["next_cursor"] is not None

    resp2 = client.get(f"/query/sessions?limit=1&cursor={data['next_cursor']}")
    data2 = resp2.json()
    assert data2["count"] == 1
    assert data2["next_cursor"] is None


# ---------------------------------------------------------------------------
# /query/aggregate
# ---------------------------------------------------------------------------

def test_aggregate_returns_stats(client, monkeypatch, metrics_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_METRICS_DIR", metrics_dir)
    resp = client.get("/query/aggregate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_events"] == 2
    assert "avg_latency_ms" in data
    assert data["avg_latency_ms"] is not None
    assert "modes" in data
    assert data["modes"]["vps"] == 1
    assert data["modes"]["vps-gpu"] == 1


def test_aggregate_empty_range(client, monkeypatch, metrics_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_METRICS_DIR", metrics_dir)
    resp = client.get("/query/aggregate?from=2020-01-01&to=2020-01-02")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_events"] == 0
    assert data["avg_latency_ms"] is None


# ---------------------------------------------------------------------------
# API key auth
# ---------------------------------------------------------------------------

def test_query_auth_enforced_when_key_set(tmp_path, monkeypatch, discoveries_dir):
    monkeypatch.setenv("DEPTHFUSION_QUERY_API_KEY", "test-key-abc")
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))
    from importlib import reload
    import depthfusion.api.rest as rest_module
    reload(rest_module)
    from fastapi.testclient import TestClient
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_DISCOVERIES_DIR", discoveries_dir)
    c = TestClient(rest_module.app)

    # No key → 401
    resp = c.get("/query/discoveries")
    assert resp.status_code == 401

    # Wrong key → 401
    resp = c.get("/query/discoveries", headers={"X-DepthFusion-Key": "wrong"})
    assert resp.status_code == 401

    # Correct key → 200
    resp = c.get("/query/discoveries", headers={"X-DepthFusion-Key": "test-key-abc"})
    assert resp.status_code == 200


def test_query_auth_not_enforced_without_key(client, monkeypatch, discoveries_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_DISCOVERIES_DIR", discoveries_dir)
    # No key set in env, no header needed
    resp = client.get("/query/discoveries")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Invalid date input
# ---------------------------------------------------------------------------

def test_invalid_date_returns_422(client):
    resp = client.get("/query/sessions?from=not-a-date")
    assert resp.status_code == 422


def test_corrupt_cursor_returns_422(client, monkeypatch, discoveries_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_DISCOVERIES_DIR", discoveries_dir)
    resp = client.get("/query/discoveries?cursor=notavalidcursor!!!")
    assert resp.status_code == 422


def test_corrupt_cursor_sessions_returns_422(client, monkeypatch, metrics_dir):
    from depthfusion.api import query as q
    monkeypatch.setattr(q, "_METRICS_DIR", metrics_dir)
    resp = client.get("/query/sessions?cursor=notavalidcursor!!!")
    assert resp.status_code == 422


def test_public_bind_without_query_key_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_API_PUBLIC", "1")
    monkeypatch.setenv("DEPTHFUSION_API_TOKEN", "some-token")
    monkeypatch.delenv("DEPTHFUSION_QUERY_API_KEY", raising=False)
    from depthfusion.api.rest import validate_public_bind_config
    with pytest.raises(ValueError, match="DEPTHFUSION_QUERY_API_KEY"):
        validate_public_bind_config()


def test_openapi_json_served(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert "paths" in spec
    assert "/query/discoveries" in spec["paths"]
    assert "/query/sessions" in spec["paths"]
    assert "/query/aggregate" in spec["paths"]
