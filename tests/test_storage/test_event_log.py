import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from depthfusion.core.memory import MemoryEvent, MemoryEventType
from depthfusion.storage.event_log import EventLog


def make_event(event_id: str, memory_id: str = "mem-001") -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        memory_id=memory_id,
        event_type=MemoryEventType.CREATED,
        project_id="proj-test",
        payload={"content": f"content for {event_id}"},
        actor="test",
        timestamp=datetime.now(timezone.utc),
    )


def test_event_log_append_and_replay(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    e1 = make_event("evt-001")
    e2 = make_event("evt-002", "mem-002")
    assert log.append(e1) is True
    assert log.append(e2) is True
    events = list(log.replay())
    assert len(events) == 2
    assert events[0].event_id == "evt-001"
    assert events[1].event_id == "evt-002"


def test_event_log_idempotent(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    e = make_event("evt-001")
    assert log.append(e) is True
    assert log.append(e) is False
    assert log.count() == 1


def test_event_log_replay_by_project(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    e1 = MemoryEvent("evt-001", "m1", MemoryEventType.CREATED, "proj-a",
                     {}, "a", datetime.now(timezone.utc))
    e2 = MemoryEvent("evt-002", "m2", MemoryEventType.CREATED, "proj-b",
                     {}, "a", datetime.now(timezone.utc))
    log.append(e1)
    log.append(e2)
    proj_a = list(log.replay(project_id="proj-a"))
    assert len(proj_a) == 1
    assert proj_a[0].project_id == "proj-a"


def test_event_log_thread_safe(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    results = []

    def worker(i):
        e = make_event(f"evt-{i:04d}", f"mem-{i:04d}")
        results.append(log.append(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert log.count() == 50
    assert all(results)
