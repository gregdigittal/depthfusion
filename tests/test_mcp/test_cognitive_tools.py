import pytest

from depthfusion.mcp.cognitive_tools import build_decision_memory, build_incident_memory
from depthfusion.core.memory_object import MemoryType


def test_decision_memory_extra_schema():
    m = build_decision_memory(
        project_id="proj",
        decision="Use SQLite for memory store",
        rationale="Simple deployment, WAL mode for concurrency",
        rejected_options=["PostgreSQL", "Redis"],
        constraints=["must work without external services"],
        impact_radius="local",
        actor="architect",
    )
    assert m.type == MemoryType.DECISION
    assert m.extra["decision"] == "Use SQLite for memory store"
    assert "PostgreSQL" in m.extra["rejected_options"]
    assert m.extra["impact_radius"] == "local"


def test_incident_memory_extra_schema():
    m = build_incident_memory(
        project_id="proj",
        error="KeyError: 'event_id' in EventLog.append",
        fix="Added missing event_id field to MemoryEvent.to_dict()",
        lesson="Always validate to_dict() round-trip in tests",
        severity="medium",
        recurrence_risk=0.2,
        actor="dev",
    )
    assert m.type == MemoryType.OPERATIONAL
    assert m.extra["error"] == "KeyError: 'event_id' in EventLog.append"
    assert m.extra["severity"] == "medium"
    assert 0.0 <= m.extra["recurrence_risk"] <= 1.0


def test_decision_memory_requires_rationale():
    with pytest.raises(ValueError, match="rationale"):
        build_decision_memory(
            project_id="proj",
            decision="Use Redis",
            rationale="",
            actor="dev",
        )


def test_incident_memory_clamps_recurrence_risk():
    m = build_incident_memory(
        project_id="proj",
        error="Some error",
        fix="Some fix",
        lesson="Some lesson",
        actor="dev",
        recurrence_risk=2.5,
    )
    assert m.extra["recurrence_risk"] == 1.0

    m2 = build_incident_memory(
        project_id="proj",
        error="Some error",
        fix="Some fix",
        lesson="Some lesson",
        actor="dev",
        recurrence_risk=-0.5,
    )
    assert m2.extra["recurrence_risk"] == 0.0
