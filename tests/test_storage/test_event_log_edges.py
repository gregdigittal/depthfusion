"""EventLog edge cases — covers lines 24-32, 55, 62, 69, 71-72 in event_log.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from depthfusion.core.memory import MemoryEvent, MemoryEventType
from depthfusion.storage.event_log import EventLog


def _make_event(event_id: str, project_id: str = "proj-test") -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        memory_id=f"mem-{event_id}",
        event_type=MemoryEventType.CREATED,
        project_id=project_id,
        payload={"content": "test", "extra": {"acl_allow": [project_id]}},
        actor="test",
        timestamp=datetime.now(timezone.utc),
    )


def test_load_seen_ids_from_existing_file(tmp_path):
    """Second EventLog instance loads seen IDs from the file — covers lines 24-32."""
    path = tmp_path / "events.jsonl"
    log1 = EventLog(path)
    e = _make_event("evt-existing-001")
    log1.append(e)

    # Create a second instance pointing at the same file — _load_seen_ids runs with content
    log2 = EventLog(path)
    assert "evt-existing-001" in log2._seen_ids
    # Appending duplicate should be rejected
    assert log2.append(e) is False


def test_replay_no_file_returns_empty(tmp_path):
    """replay() on a path that was never written yields nothing — covers line 55."""
    path = tmp_path / "does-not-exist.jsonl"
    log = EventLog(path)
    # Force the path to not exist (constructor creates parent, not file itself)
    assert not path.exists()
    assert list(log.replay()) == []


def test_replay_skips_empty_lines(tmp_path):
    """replay() silently skips empty lines in the file — covers line 62."""
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    e = _make_event("evt-real-001")
    log.append(e)

    # Inject blank lines directly into the file
    with open(path, "a") as f:
        f.write("\n\n")

    events = list(log.replay())
    assert len(events) == 1
    assert events[0].event_id == "evt-real-001"


def test_replay_since_filter(tmp_path):
    """replay(since=...) excludes events older than the cutoff — covers line 69."""
    path = tmp_path / "events.jsonl"
    log = EventLog(path)

    old_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
    new_ts = datetime.now(timezone.utc)

    old_event = MemoryEvent(
        event_id="evt-old",
        memory_id="mem-old",
        event_type=MemoryEventType.CREATED,
        project_id="proj-test",
        payload={"extra": {"acl_allow": ["proj-test"]}},
        actor="test",
        timestamp=old_ts,
    )
    new_event = MemoryEvent(
        event_id="evt-new",
        memory_id="mem-new",
        event_type=MemoryEventType.CREATED,
        project_id="proj-test",
        payload={"extra": {"acl_allow": ["proj-test"]}},
        actor="test",
        timestamp=new_ts,
    )
    log.append(old_event)
    log.append(new_event)

    cutoff = datetime(2023, 1, 1, tzinfo=timezone.utc)
    recent = list(log.replay(since=cutoff))
    assert len(recent) == 1
    assert recent[0].event_id == "evt-new"


def test_replay_skips_malformed_json(tmp_path):
    """replay() skips lines with invalid JSON — covers lines 71-72."""
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    e = _make_event("evt-good-001")
    log.append(e)

    # Inject malformed JSON
    with open(path, "a") as f:
        f.write("{ not valid json }\n")
        f.write('{"missing_event_id_key": "yes"}\n')

    events = list(log.replay())
    assert len(events) == 1
    assert events[0].event_id == "evt-good-001"
