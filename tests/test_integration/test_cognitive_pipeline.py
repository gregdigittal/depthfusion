"""End-to-end cognitive pipeline integration tests — Task 12 / E-31 / S-102."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest


@pytest.fixture
def cognitive_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_COGNITIVE_RETRIEVAL", "1")
    monkeypatch.setenv("DEPTHFUSION_DECISION_MEMORY", "1")
    monkeypatch.setenv("DEPTHFUSION_OPERATIONAL_MEMORY", "1")
    monkeypatch.setenv("DEPTHFUSION_CONTRADICTION_ENGINE", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    return tmp_path


def test_full_decision_lifecycle(cognitive_env, tmp_path):
    """Record decision → report outcome → verify event count is 2."""
    from depthfusion.core.memory import MemoryEvent, MemoryEventType
    from depthfusion.mcp.cognitive_tools import build_decision_memory
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    log = EventLog(tmp_path / "events.jsonl")
    store = MemoryStore(tmp_path / "memories.db")

    decision = build_decision_memory(
        project_id="proj-integration",
        decision="Use SQLite for MemoryStore",
        rationale="Simple deployment, WAL mode for concurrency",
        rejected_options=["Postgres", "Redis"],
        actor="integration-test",
    )
    event = MemoryEvent(
        str(uuid.uuid4()), decision.id, MemoryEventType.CREATED,
        "proj-integration", decision.to_dict(), "test",
        datetime.now(timezone.utc),
    )
    log.append(event)
    store.upsert(decision)

    retrieved = store.get(decision.id)
    assert retrieved is not None
    assert retrieved.extra["decision"] == "Use SQLite for MemoryStore"
    assert "Postgres" in retrieved.extra["rejected_options"]

    retrieved.confidence.verification_count += 1
    retrieved.confidence.score = min(1.0, retrieved.confidence.score + 0.05)
    outcome_event = MemoryEvent(
        str(uuid.uuid4()), decision.id, MemoryEventType.OUTCOME_RECORDED,
        "proj-integration",
        {"outcome": "Works well in prod", "success": True,
         "extra": {"acl_allow": ["proj-integration"]}},
        "test", datetime.now(timezone.utc),
    )
    log.append(outcome_event)
    store.upsert(retrieved)

    events = list(log.replay(project_id="proj-integration"))
    assert len(events) == 2
    assert events[0].event_type == MemoryEventType.CREATED
    assert events[1].event_type == MemoryEventType.OUTCOME_RECORDED


def test_incident_lifecycle(cognitive_env, tmp_path):
    """Record incident → verify stored extra fields."""
    from depthfusion.mcp.cognitive_tools import build_incident_memory
    from depthfusion.storage.memory_store import MemoryStore

    store = MemoryStore(tmp_path / "memories.db")
    incident = build_incident_memory(
        project_id="proj-integration",
        error="KeyError in handler",
        fix="Added missing key validation",
        lesson="Always validate dict keys at boundaries",
        severity="high",
        recurrence_risk=0.6,
        actor="integration-test",
    )
    store.upsert(incident)
    retrieved = store.get(incident.id)
    assert retrieved is not None
    assert retrieved.extra["severity"] == "high"
    assert 0.0 <= retrieved.extra["recurrence_risk"] <= 1.0


def test_contradiction_detected_and_queued(cognitive_env):
    """Low-confidence contradiction is PENDING_REVIEW, not auto-emitted."""
    from depthfusion.cognitive.contradiction import ConflictStatus, ContradictionEngine
    from depthfusion.core.memory_object import MemoryObject, MemoryType

    engine = ContradictionEngine(auto_emit_threshold=0.85)

    m1 = MemoryObject(
        id="m1", project_id="proj-integration",
        type=MemoryType.SEMANTIC, content="Redis is used for caching",
    )
    m1.confidence.score = 0.5
    m2 = MemoryObject(
        id="m2", project_id="proj-integration",
        type=MemoryType.SEMANTIC, content="Redis is not used for caching",
    )
    m2.confidence.score = 0.5

    conflicts = engine.detect(m1, m2)
    assert any(c.status == ConflictStatus.PENDING_REVIEW for c in conflicts)
    assert not any(c.status == ConflictStatus.AUTO_EMITTED for c in conflicts)


def test_contradiction_high_confidence_auto_emits(cognitive_env):
    """High-confidence contradiction is AUTO_EMITTED."""
    from depthfusion.cognitive.contradiction import ConflictStatus, ContradictionEngine
    from depthfusion.core.memory_object import MemoryObject, MemoryType

    engine = ContradictionEngine(auto_emit_threshold=0.85)

    m1 = MemoryObject(
        id="m3", project_id="proj-integration",
        type=MemoryType.SEMANTIC, content="Redis is used for caching",
    )
    m1.confidence.score = 0.95
    m2 = MemoryObject(
        id="m4", project_id="proj-integration",
        type=MemoryType.SEMANTIC, content="Redis is not used for caching",
    )
    m2.confidence.score = 0.95

    conflicts = engine.detect(m1, m2)
    assert any(c.status == ConflictStatus.AUTO_EMITTED for c in conflicts)


def test_event_log_replay_filters_by_project(cognitive_env, tmp_path):
    """Events from different projects do not bleed into each other."""
    from depthfusion.core.memory import MemoryEvent, MemoryEventType
    from depthfusion.storage.event_log import EventLog

    log = EventLog(tmp_path / "events.jsonl")
    for proj in ("proj-a", "proj-b"):
        e = MemoryEvent(
            str(uuid.uuid4()), str(uuid.uuid4()), MemoryEventType.CREATED,
            proj, {"extra": {"acl_allow": [proj]}}, "test", datetime.now(timezone.utc),
        )
        log.append(e)

    proj_a_events = list(log.replay(project_id="proj-a"))
    assert len(proj_a_events) == 1
    assert proj_a_events[0].project_id == "proj-a"
