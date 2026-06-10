"""Coverage for depthfusion.mcp.tools.decisions — lines 29-218."""
from __future__ import annotations

import json
from types import SimpleNamespace

from depthfusion.mcp.tools.decisions import (
    _tool_get_cognitive_state,
    _tool_mark_superseded,
    _tool_record_decision,
    _tool_record_incident,
    _tool_report_outcome,
    _tool_run_recursive,
    register_decisions,
)


def _cfg(tmp_path):
    return SimpleNamespace(
        event_log_path=tmp_path / "events.jsonl",
        memory_store_path=tmp_path / "memories.db",
        cognitive_retrieval=False,
        contradiction_engine=False,
        decision_memory=True,
        operational_memory=True,
        autonomic=False,
    )


# ── _tool_run_recursive ────────────────────────────────────────────────────

def test_tool_run_recursive_returns_valid_json(tmp_path):
    """Covers lines 29-46: entry point + exception/unavailable branch."""
    result = _tool_run_recursive({}, _cfg(tmp_path))
    data = json.loads(result)
    # Either unavailable or exception — both return "error" + "result" keys
    assert "error" in data or "result" in data


# ── _tool_record_decision ──────────────────────────────────────────────────

def test_tool_record_decision_basic(tmp_path):
    """Covers lines 49-79: full happy path through EventLog + MemoryStore."""
    result = _tool_record_decision(
        {
            "project_id": "proj-v2",
            "decision": "Use SQLite for memory persistence",
            "rationale": "Simple deployment without external service deps",
            "actor": "architect",
            "impact_radius": "global",
            "rejected_options": ["PostgreSQL", "Redis"],
            "constraints": ["no external services"],
        },
        _cfg(tmp_path),
    )
    data = json.loads(result)
    assert data["type"] == "decision"
    assert data["status"] == "recorded"
    assert "memory_id" in data


def test_tool_record_decision_minimal_args(tmp_path):
    """Defaults fill in when optional args are absent."""
    result = _tool_record_decision(
        {"decision": "Pick X", "rationale": "It is better"},
        _cfg(tmp_path),
    )
    data = json.loads(result)
    assert data["status"] == "recorded"


# ── _tool_record_incident ─────────────────────────────────────────────────

def test_tool_record_incident_basic(tmp_path):
    """Covers lines 82-113: incident memory persisted to log + store."""
    result = _tool_record_incident(
        {
            "project_id": "proj-v2",
            "error": "KeyError in EventLog.append",
            "fix": "Added null-check before key access",
            "lesson": "Always validate to_dict round-trips",
            "actor": "dev",
            "severity": "high",
            "recurrence_risk": 0.5,
        },
        _cfg(tmp_path),
    )
    data = json.loads(result)
    assert data["type"] == "operational"
    assert data["severity"] == "high"
    assert "memory_id" in data


def test_tool_record_incident_default_severity(tmp_path):
    """Severity defaults to 'medium' when omitted."""
    result = _tool_record_incident(
        {"error": "Crash", "fix": "Fixed", "lesson": "Lesson"},
        _cfg(tmp_path),
    )
    data = json.loads(result)
    assert data["severity"] == "medium"


# ── _tool_mark_superseded ─────────────────────────────────────────────────

def test_tool_mark_superseded_not_found(tmp_path):
    """Covers lines 116-134: MemoryStore.get returns None → error JSON."""
    result = _tool_mark_superseded(
        {
            "project_id": "proj-v2",
            "old_memory_id": "does-not-exist",
            "new_memory_id": "new-id",
            "reason": "superseded",
            "actor": "dev",
        },
        _cfg(tmp_path),
    )
    data = json.loads(result)
    assert "error" in data
    assert "does-not-exist" in data["error"]


def test_tool_mark_superseded_success(tmp_path):
    """Covers lines 135-149: existing memory gets SUPERSEDED status."""
    cfg = _cfg(tmp_path)
    # Create a decision memory to supersede
    created = json.loads(
        _tool_record_decision(
            {"decision": "Use X", "rationale": "It works", "actor": "dev"},
            cfg,
        )
    )
    memory_id = created["memory_id"]

    result = _tool_mark_superseded(
        {
            "old_memory_id": memory_id,
            "new_memory_id": "new-approach-id",
            "reason": "Found a better way",
            "actor": "dev",
        },
        cfg,
    )
    data = json.loads(result)
    assert data["status"] == "superseded"
    assert data["old_id"] == memory_id


# ── _tool_report_outcome ──────────────────────────────────────────────────

def test_tool_report_outcome_not_found(tmp_path):
    """Covers lines 152-169: memory not found → error JSON."""
    result = _tool_report_outcome(
        {"memory_id": "ghost-id", "outcome": "worked", "success": True},
        _cfg(tmp_path),
    )
    data = json.loads(result)
    assert "error" in data


def test_tool_report_outcome_success(tmp_path):
    """Covers lines 169-191: outcome appended and confidence incremented."""
    cfg = _cfg(tmp_path)
    created = json.loads(
        _tool_record_decision(
            {"decision": "Try approach Y", "rationale": "Fast", "actor": "dev"},
            cfg,
        )
    )
    memory_id = created["memory_id"]

    result = _tool_report_outcome(
        {
            "memory_id": memory_id,
            "outcome": "It worked in production",
            "success": True,
            "actor": "dev",
        },
        cfg,
    )
    data = json.loads(result)
    assert data["status"] == "recorded"
    assert data["success"] is True
    assert data["memory_id"] == memory_id


def test_tool_report_outcome_failure(tmp_path):
    """success=False path — confidence not incremented."""
    cfg = _cfg(tmp_path)
    created = json.loads(
        _tool_record_decision(
            {"decision": "Try Z", "rationale": "Seems OK", "actor": "dev"},
            cfg,
        )
    )
    result = _tool_report_outcome(
        {
            "memory_id": created["memory_id"],
            "outcome": "It failed",
            "success": False,
        },
        cfg,
    )
    data = json.loads(result)
    assert data["success"] is False


# ── _tool_get_cognitive_state ────────────────────────────────────────────

def test_tool_get_cognitive_state_empty(tmp_path):
    """Covers lines 194-214: empty store returns zero counts."""
    result = _tool_get_cognitive_state({"project_id": "proj-v2"}, _cfg(tmp_path))
    data = json.loads(result)
    assert data["project_id"] == "proj-v2"
    assert data["total_memories"] == 0
    assert "feature_flags" in data
    assert data["feature_flags"]["decision_memory"] is True


def test_tool_get_cognitive_state_with_memories(tmp_path):
    """State reflects persisted memories."""
    cfg = _cfg(tmp_path)
    _tool_record_decision(
        {"project_id": "proj-a", "decision": "D1", "rationale": "R1", "actor": "a"},
        cfg,
    )
    _tool_record_incident(
        {"project_id": "proj-a", "error": "E", "fix": "F", "lesson": "L"},
        cfg,
    )
    result = _tool_get_cognitive_state({"project_id": "proj-a"}, cfg)
    data = json.loads(result)
    assert data["total_memories"] >= 2
    assert data["total_events"] >= 2


# ── register_decisions stub ───────────────────────────────────────────────

def test_register_decisions_callable():
    """Covers line 218: stub must not raise."""
    register_decisions()
