"""Tests for the RLM HTTP sidecar (test_sidecar.py).

RLMClient is mocked throughout so the rlm package need not be installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Minimal stand-in for RecursiveTrajectory so we can test serialisation
# without importing the full depthfusion stack from inside the mock.
# ---------------------------------------------------------------------------
@dataclass
class _StubTrajectory:
    strategy: str = "flat"
    query: str = "q"
    sub_calls: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    quality_score: Optional[float] = None
    completed: bool = True
    error: Optional[str] = None
    steps: list = field(default_factory=list)


@pytest.fixture()
def client():
    """Return a TestClient with RLMClient fully mocked out."""
    mock_rlm = MagicMock()
    with patch("depthfusion.recursive.sidecar._client", mock_rlm):
        from depthfusion.recursive.sidecar import app
        yield TestClient(app), mock_rlm


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_available(client):
    tc, mock_rlm = client
    mock_rlm.is_available.return_value = True
    resp = tc.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"available": True, "status": "ok"}


def test_health_degraded(client):
    tc, mock_rlm = client
    mock_rlm.is_available.return_value = False
    resp = tc.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "status": "degraded"}


# ---------------------------------------------------------------------------
# /run — success
# ---------------------------------------------------------------------------

def test_run_success(client):
    tc, mock_rlm = client
    traj = _StubTrajectory(strategy="flat", query="test query", completed=True)
    mock_rlm.run.return_value = ("result text", traj)

    resp = tc.post("/run", json={"query": "test query", "content": "some content"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "result text"
    assert body["trajectory"]["strategy"] == "flat"
    assert body["trajectory"]["completed"] is True


# ---------------------------------------------------------------------------
# /run — validation errors
# ---------------------------------------------------------------------------

def test_run_missing_query(client):
    tc, _ = client
    resp = tc.post("/run", json={"content": "some content"})
    assert resp.status_code == 422


def test_run_missing_content(client):
    tc, _ = client
    resp = tc.post("/run", json={"query": "q"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /run — cost ceiling exceeded (ValueError → 422)
# ---------------------------------------------------------------------------

def test_run_cost_exceeded(client):
    tc, mock_rlm = client
    mock_rlm.run.side_effect = ValueError("cost exceeded")
    resp = tc.post("/run", json={"query": "q", "content": "lots of text"})
    assert resp.status_code == 422
    assert resp.json() == {"error": "cost exceeded"}


# ---------------------------------------------------------------------------
# /run — unexpected runtime error → 500
# ---------------------------------------------------------------------------

def test_run_runtime_error(client):
    tc, mock_rlm = client
    mock_rlm.run.side_effect = RuntimeError("unexpected")
    resp = tc.post("/run", json={"query": "q", "content": "c"})
    assert resp.status_code == 500
    assert "unexpected" in resp.json()["error"]


# ---------------------------------------------------------------------------
# /schema
# ---------------------------------------------------------------------------

def test_schema(client):
    tc, _ = client
    resp = tc.get("/schema")
    assert resp.status_code == 200
    body = resp.json()
    assert "endpoints" in body
    assert "GET /health" in body["endpoints"]
    assert "POST /run" in body["endpoints"]
