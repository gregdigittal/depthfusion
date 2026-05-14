"""Integration tests for df_record_telemetry / df_query_telemetry — E-33 S-106."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# TelemetryStore unit tests
# ---------------------------------------------------------------------------

def test_record_and_query(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    eid = store.record(
        session_id="sess-1",
        tool_name="Read",
        agent="vps",
        project="depthfusion",
        story_id="S-106",
        sprint="2026-Q2-S1",
        duration_ms=42.5,
        tokens_in=100,
        tokens_out=200,
        cost_usd_estimate=0.0015,
    )
    assert isinstance(eid, int)
    rows = store.query(project="depthfusion")
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "sess-1"
    assert row["tool_name"] == "Read"
    assert row["agent"] == "vps"
    assert row["duration_ms"] == pytest.approx(42.5)
    assert row["tokens_in"] == 100
    assert row["cost_usd_estimate"] == pytest.approx(0.0015)


def test_query_filter_by_agent(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    store.record("sess-1", "Read", agent="vps", project="df")
    store.record("sess-2", "Write", agent="mac-mlx", project="df")
    rows = store.query(agent="vps")
    assert len(rows) == 1
    assert rows[0]["agent"] == "vps"


def test_query_filter_by_story(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    store.record("s1", "Bash", story_id="S-106")
    store.record("s2", "Edit", story_id="S-107")
    rows = store.query(story_id="S-106")
    assert len(rows) == 1
    assert rows[0]["story_id"] == "S-106"


def test_query_empty(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    rows = store.query(project="nonexistent")
    assert rows == []


def test_aggregate_totals(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    store.record("s1", "Read", project="df", duration_ms=100.0, tokens_in=50, tokens_out=100, cost_usd_estimate=0.001)
    store.record("s1", "Write", project="df", duration_ms=200.0, tokens_in=30, tokens_out=50, cost_usd_estimate=0.002)
    store.record("s2", "Bash", project="other", duration_ms=10.0)
    result = store.aggregate(project="df")
    assert result["row_count"] == 1
    row = result["rows"][0]
    assert row["event_count"] == 2
    assert row["total_duration_ms"] == pytest.approx(300.0)
    assert row["total_tokens_in"] == 80
    assert row["total_cost_usd"] == pytest.approx(0.003)


def test_aggregate_by_period(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    store.record("s1", "Read", recorded_at="2026-05-01T10:00:00Z", duration_ms=10.0)
    store.record("s1", "Read", recorded_at="2026-05-01T11:00:00Z", duration_ms=20.0)
    store.record("s1", "Read", recorded_at="2026-05-14T09:00:00Z", duration_ms=30.0)
    result = store.aggregate(period="day")
    assert result["row_count"] == 2
    day_counts = {r["period"]: r["event_count"] for r in result["rows"]}
    assert day_counts["2026-05-01"] == 2
    assert day_counts["2026-05-14"] == 1


def test_aggregate_date_range(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    store.record("s1", "Read", recorded_at="2026-05-01T00:00:00Z")
    store.record("s1", "Read", recorded_at="2026-05-14T00:00:00Z")
    result = store.aggregate(from_dt="2026-05-10T00:00:00Z")
    assert result["rows"][0]["event_count"] == 1


# ---------------------------------------------------------------------------
# MCP tool integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def mcp_config(tmp_path):
    """Minimal config stub that routes telemetry to a temp DB."""
    class _Config:
        telemetry_store_path = tmp_path / "tel.db"

    return _Config()


def test_mcp_record_telemetry(mcp_config):
    import importlib
    server = importlib.import_module("depthfusion.mcp.server")
    result = json.loads(
        server._tool_record_telemetry(
            {
                "session_id": "sess-abc",
                "tool_name": "Bash",
                "agent": "vps",
                "project": "depthfusion",
                "duration_ms": 55.0,
                "tokens_in": 200,
                "tokens_out": 400,
                "cost_usd_estimate": 0.003,
            },
            mcp_config,
        )
    )
    assert result["ok"] is True
    assert isinstance(result["event_id"], int)


def test_mcp_query_telemetry(mcp_config):
    import importlib
    server = importlib.import_module("depthfusion.mcp.server")

    server._tool_record_telemetry(
        {"session_id": "s1", "tool_name": "Read", "project": "df", "duration_ms": 10.0, "cost_usd_estimate": 0.001},
        mcp_config,
    )
    server._tool_record_telemetry(
        {"session_id": "s1", "tool_name": "Write", "project": "df", "duration_ms": 20.0, "cost_usd_estimate": 0.002},
        mcp_config,
    )

    result = json.loads(
        server._tool_query_telemetry({"project": "df"}, mcp_config)
    )
    assert result["row_count"] == 1
    assert result["rows"][0]["event_count"] == 2
    assert result["rows"][0]["total_cost_usd"] == pytest.approx(0.003)


def test_mcp_query_telemetry_by_period(mcp_config):
    import importlib
    server = importlib.import_module("depthfusion.mcp.server")

    server._tool_record_telemetry(
        {"session_id": "s1", "tool_name": "Bash", "recorded_at": "2026-05-01T00:00:00Z"},
        mcp_config,
    )
    server._tool_record_telemetry(
        {"session_id": "s1", "tool_name": "Bash", "recorded_at": "2026-05-14T00:00:00Z"},
        mcp_config,
    )

    result = json.loads(
        server._tool_query_telemetry({"period": "day"}, mcp_config)
    )
    assert result["row_count"] == 2


def test_mcp_record_telemetry_missing_required(mcp_config):
    """session_id and tool_name are required — without them the store gets empty strings, not crashes."""
    import importlib
    server = importlib.import_module("depthfusion.mcp.server")
    result = json.loads(
        server._tool_record_telemetry({}, mcp_config)
    )
    # Should still succeed (empty strings are stored) — the schema validation
    # is done by the MCP layer, not the handler itself.
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# S-107 additions: session_type, offset pagination, compute_think_times,
# aggregate session_type filter, REST telemetry endpoints
# ---------------------------------------------------------------------------

def test_query_filter_by_session_type(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    store.record("s1", "Read", session_type="human", project="df")
    store.record("s2", "Write", session_type="agent", project="df")
    human = store.query(session_type="human")
    assert len(human) == 1
    assert human[0]["session_type"] == "human"
    agent = store.query(session_type="agent")
    assert len(agent) == 1
    assert agent[0]["session_type"] == "agent"


def test_query_offset_pagination(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    for i in range(5):
        store.record(f"s{i}", "Read", recorded_at=f"2026-05-0{i+1}T00:00:00Z")
    first_page = store.query(limit=2, offset=0)
    second_page = store.query(limit=2, offset=2)
    assert len(first_page) == 2
    assert len(second_page) == 2
    # Pages should not overlap
    first_ids = {r["id"] for r in first_page}
    second_ids = {r["id"] for r in second_page}
    assert first_ids.isdisjoint(second_ids)


def test_aggregate_filter_by_session_type(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    store.record("s1", "Read", session_type="human", cost_usd_estimate=0.01)
    store.record("s2", "Write", session_type="agent", cost_usd_estimate=0.02)
    result = store.aggregate(session_type="human")
    assert result["rows"][0]["event_count"] == 1
    assert result["rows"][0]["total_cost_usd"] == pytest.approx(0.01)


def test_compute_think_times_basic(tmp_path):
    from depthfusion.storage.telemetry_store import compute_think_times

    events = [
        {"session_id": "s1", "recorded_at": "2026-05-01T10:00:00Z", "duration_ms": 1000.0},
        {"session_id": "s1", "recorded_at": "2026-05-01T10:00:02Z", "duration_ms": 500.0},
        {"session_id": "s1", "recorded_at": "2026-05-01T10:00:03.5Z", "duration_ms": 200.0},
    ]
    result = compute_think_times(events)
    by_ts = {r["recorded_at"]: r for r in result}
    assert by_ts["2026-05-01T10:00:00Z"]["think_time_ms"] is None
    # gap = 2000ms start - (0ms start + 1000ms dur) = 1000ms
    assert by_ts["2026-05-01T10:00:02Z"]["think_time_ms"] == pytest.approx(1000.0, abs=1.0)
    # gap = 3500ms - (2000ms + 500ms) = 1000ms
    assert by_ts["2026-05-01T10:00:03.5Z"]["think_time_ms"] == pytest.approx(1000.0, abs=1.0)


def test_compute_think_times_multi_session():
    from depthfusion.storage.telemetry_store import compute_think_times

    events = [
        {"session_id": "s1", "recorded_at": "2026-05-01T10:00:00Z", "duration_ms": 500.0},
        {"session_id": "s2", "recorded_at": "2026-05-01T10:00:01Z", "duration_ms": 200.0},
        {"session_id": "s1", "recorded_at": "2026-05-01T10:00:02Z", "duration_ms": 100.0},
    ]
    result = compute_think_times(events)
    s1_events = sorted([r for r in result if r["session_id"] == "s1"], key=lambda e: e["recorded_at"])
    assert s1_events[0]["think_time_ms"] is None
    assert s1_events[1]["think_time_ms"] is not None
    s2_events = [r for r in result if r["session_id"] == "s2"]
    assert s2_events[0]["think_time_ms"] is None


def test_mcp_record_telemetry_session_type(mcp_config):
    import importlib
    server = importlib.import_module("depthfusion.mcp.server")
    result = json.loads(
        server._tool_record_telemetry(
            {"session_id": "s-human", "tool_name": "Read", "session_type": "human"},
            mcp_config,
        )
    )
    assert result["ok"] is True
    from depthfusion.storage.telemetry_store import TelemetryStore
    store = TelemetryStore(mcp_config.telemetry_store_path)
    rows = store.query(session_type="human")
    assert len(rows) == 1


def test_mcp_query_telemetry_session_type_filter(mcp_config):
    import importlib
    server = importlib.import_module("depthfusion.mcp.server")
    server._tool_record_telemetry(
        {"session_id": "s1", "tool_name": "Read", "session_type": "human", "cost_usd_estimate": 0.005},
        mcp_config,
    )
    server._tool_record_telemetry(
        {"session_id": "s2", "tool_name": "Write", "session_type": "agent", "cost_usd_estimate": 0.010},
        mcp_config,
    )
    result = json.loads(
        server._tool_query_telemetry({"session_type": "human"}, mcp_config)
    )
    assert result["rows"][0]["event_count"] == 1
    assert result["rows"][0]["total_cost_usd"] == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# REST endpoint tests for /query/telemetry and /query/telemetry/aggregate
# ---------------------------------------------------------------------------

@pytest.fixture
def telemetry_client(tmp_path, monkeypatch):
    """Test client with telemetry store routed to a temp DB."""
    from fastapi.testclient import TestClient
    monkeypatch.setenv("DEPTHFUSION_TELEMETRY_DB", str(tmp_path / "tel.db"))
    monkeypatch.delenv("DEPTHFUSION_API_PUBLIC", raising=False)
    monkeypatch.delenv("DEPTHFUSION_QUERY_API_KEY", raising=False)
    from depthfusion.api.rest import app
    return TestClient(app)


def test_rest_get_telemetry_empty(telemetry_client):
    resp = telemetry_client.get("/query/telemetry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows"] == []
    assert data["row_count"] == 0
    assert data["next_cursor"] is None


def test_rest_get_telemetry_with_data(telemetry_client, tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_TELEMETRY_DB", str(tmp_path / "tel.db"))
    from depthfusion.storage.telemetry_store import TelemetryStore
    store = TelemetryStore(tmp_path / "tel.db")
    store.record("s1", "Read", session_type="human", project="df", duration_ms=100.0)
    store.record("s2", "Write", session_type="agent", project="df", duration_ms=200.0)

    resp = telemetry_client.get("/query/telemetry", params={"session_type": "human"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["row_count"] == 1
    assert data["rows"][0]["session_type"] == "human"


def test_rest_get_telemetry_pagination(telemetry_client, tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_TELEMETRY_DB", str(tmp_path / "tel.db"))
    from depthfusion.storage.telemetry_store import TelemetryStore
    store = TelemetryStore(tmp_path / "tel.db")
    for i in range(5):
        store.record(f"s{i}", "Bash", recorded_at=f"2026-05-0{i+1}T00:00:00Z")

    resp = telemetry_client.get("/query/telemetry", params={"limit": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert data["row_count"] == 2
    assert data["next_cursor"] is not None

    resp2 = telemetry_client.get("/query/telemetry", params={"limit": 2, "cursor": data["next_cursor"]})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["row_count"] == 2
    first_ids = {r["id"] for r in data["rows"]}
    second_ids = {r["id"] for r in data2["rows"]}
    assert first_ids.isdisjoint(second_ids)


def test_rest_get_telemetry_invalid_cursor(telemetry_client):
    resp = telemetry_client.get("/query/telemetry", params={"cursor": "not-valid-base64!!!"})
    assert resp.status_code == 422


def test_rest_get_telemetry_include_think_time(telemetry_client, tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_TELEMETRY_DB", str(tmp_path / "tel.db"))
    from depthfusion.storage.telemetry_store import TelemetryStore
    store = TelemetryStore(tmp_path / "tel.db")
    store.record("s1", "Read", recorded_at="2026-05-01T10:00:00Z", duration_ms=500.0)
    store.record("s1", "Write", recorded_at="2026-05-01T10:00:01Z", duration_ms=100.0)

    resp = telemetry_client.get("/query/telemetry", params={"include_think_time": "true"})
    assert resp.status_code == 200
    data = resp.json()
    think_times = [r.get("think_time_ms") for r in data["rows"]]
    assert None in think_times  # first event in session has no think time


def test_rest_telemetry_aggregate(telemetry_client, tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_TELEMETRY_DB", str(tmp_path / "tel.db"))
    from depthfusion.storage.telemetry_store import TelemetryStore
    store = TelemetryStore(tmp_path / "tel.db")
    store.record("s1", "Read", project="df", cost_usd_estimate=0.001)
    store.record("s2", "Write", project="df", cost_usd_estimate=0.002)
    store.record("s3", "Bash", project="other", cost_usd_estimate=0.005)

    resp = telemetry_client.get("/query/telemetry/aggregate", params={"project": "df"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows"][0]["event_count"] == 2
    assert data["rows"][0]["total_cost_usd"] == pytest.approx(0.003)


def test_rest_telemetry_aggregate_by_period(telemetry_client, tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_TELEMETRY_DB", str(tmp_path / "tel.db"))
    from depthfusion.storage.telemetry_store import TelemetryStore
    store = TelemetryStore(tmp_path / "tel.db")
    store.record("s1", "Read", recorded_at="2026-05-01T00:00:00Z")
    store.record("s1", "Read", recorded_at="2026-05-14T00:00:00Z")

    resp = telemetry_client.get("/query/telemetry/aggregate", params={"period": "day"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["row_count"] == 2


def test_rest_telemetry_aggregate_invalid_period(telemetry_client):
    resp = telemetry_client.get("/query/telemetry/aggregate", params={"period": "quarter"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# S-109: candidate_skills table + df_surface_skill_candidates MCP tool
# ---------------------------------------------------------------------------

def test_get_recurring_patterns_empty(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    assert store.get_recurring_patterns(threshold=3) == []


def test_get_recurring_patterns_threshold(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    for i in range(4):
        store.record(f"sess-{i}", "Read")
    for i in range(2):
        store.record(f"sess-b{i}", "Write")

    patterns = store.get_recurring_patterns(threshold=3)
    names = [p["tool_name"] for p in patterns]
    assert "Read" in names
    assert "Write" not in names


def test_add_candidate_dedup(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    row1 = store.add_candidate("tool:Read", "Auto-use: Read", "desc")
    row2 = store.add_candidate("tool:Read", "Auto-use: Read", "desc")
    assert row1 is not None
    assert row2 is None  # duplicate returns None

    candidates = store.get_candidates()
    assert len(candidates) == 1
    assert candidates[0]["status"] == "pending"


def test_update_candidate_skillforge_id(tmp_path):
    from depthfusion.storage.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path / "tel.db")
    store.add_candidate("tool:Bash", "Auto-use: Bash", "")
    store.update_candidate_skillforge_id("tool:Bash", "sf-42")
    candidates = store.get_candidates()
    assert candidates[0]["skillforge_id"] == "sf-42"


def test_mcp_surface_skill_candidates_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_TELEMETRY_DB", str(tmp_path / "tel.db"))
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _dispatch_tool
    from depthfusion.storage.telemetry_store import TelemetryStore

    cfg = DepthFusionConfig()
    store = TelemetryStore(tmp_path / "tel.db")
    for i in range(3):
        store.record(f"s{i}", "Bash")

    result = json.loads(
        _dispatch_tool("depthfusion_surface_skill_candidates", {"threshold": 3, "dry_run": True}, cfg)
    )
    assert result["candidates_found"] == 1
    assert result["candidates_drafted"] == 1
    assert result["dry_run"] is True
    assert result["items"][0]["pattern_key"] == "tool:Bash"
    assert result["items"][0]["skillforge_id"] is None


def test_mcp_surface_skill_candidates_below_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_TELEMETRY_DB", str(tmp_path / "tel.db"))
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _dispatch_tool
    from depthfusion.storage.telemetry_store import TelemetryStore

    cfg = DepthFusionConfig()
    store = TelemetryStore(tmp_path / "tel.db")
    for i in range(2):
        store.record(f"s{i}", "Glob")

    result = json.loads(
        _dispatch_tool("depthfusion_surface_skill_candidates", {"threshold": 3, "dry_run": True}, cfg)
    )
    assert result["candidates_found"] == 0
    assert result["candidates_drafted"] == 0


def test_mcp_surface_skill_candidates_no_duplicate_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_TELEMETRY_DB", str(tmp_path / "tel.db"))
    from depthfusion.core.config import DepthFusionConfig
    from depthfusion.mcp.server import _dispatch_tool
    from depthfusion.storage.telemetry_store import TelemetryStore

    cfg = DepthFusionConfig()
    store = TelemetryStore(tmp_path / "tel.db")
    for i in range(3):
        store.record(f"s{i}", "Read")

    r1 = json.loads(
        _dispatch_tool("depthfusion_surface_skill_candidates", {"threshold": 3, "dry_run": True}, cfg)
    )
    assert r1["candidates_drafted"] == 1
    assert r1["already_tracked"] == 0

    r2 = json.loads(
        _dispatch_tool("depthfusion_surface_skill_candidates", {"threshold": 3, "dry_run": True}, cfg)
    )
    assert r2["candidates_drafted"] == 0
    assert r2["already_tracked"] == 1


def test_skillforge_client_no_url(monkeypatch):
    """When DEPTHFUSION_SKILLFORGE_URL is unset, post_skill_draft returns None without error."""
    monkeypatch.delenv("DEPTHFUSION_SKILLFORGE_URL", raising=False)
    from depthfusion.mcp.skillforge_client import post_skill_draft

    result = post_skill_draft("Test", "desc", "tool:Test", 5)
    assert result is None
