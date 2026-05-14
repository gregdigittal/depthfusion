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
