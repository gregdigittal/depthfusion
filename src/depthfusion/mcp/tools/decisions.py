"""depthfusion MCP tool implementations — decisions domain."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

from depthfusion.capture.event_hook import emit_if_high_importance
from depthfusion.core.types import ContextItem
from depthfusion.parsers import parse_conversation
from depthfusion.retrieval.bm25 import BM25 as _BM25
from depthfusion.retrieval.bm25 import tokenize as _tokenize_bm25
from depthfusion.router.bus import ContextBus, FileBus, InMemoryBus
try:
    from depthfusion.backends.openrouter import OpenRouterBackend
except Exception:  # pragma: no cover — optional module in older environments
    OpenRouterBackend = None  # type: ignore[assignment,misc]

logger = logging.getLogger("depthfusion.mcp.server")


def _tool_run_recursive(arguments: dict, config: Any) -> str:
    query = arguments.get("query", "")
    content = arguments.get("content", "")
    try:
        from depthfusion.recursive.client import RLMClient
        client = RLMClient(config=config)
        if not client.is_skillforge_configured() and not client.is_available():
            return json.dumps({"error": "neither SkillForge nor rlm configured", "result": None})
        result_text, traj = client.run(query=query, content=content)
        return json.dumps(
            {
                "result": result_text,
                "strategy": traj.strategy,
                "tokens": traj.total_tokens,
                "cost": traj.estimated_cost,
            }
        )
    except Exception as exc:
        return json.dumps({"error": str(exc), "result": None})

def _tool_record_decision(arguments: dict, config: Any) -> str:
    import uuid
    from datetime import datetime, timezone

    from depthfusion.core.memory import MemoryEvent, MemoryEventType
    from depthfusion.mcp.cognitive_tools import build_decision_memory
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    project_id = arguments.get("project_id", "")
    actor = arguments.get("actor", "unknown")
    m = build_decision_memory(
        project_id=project_id,
        decision=arguments.get("decision", ""),
        rationale=arguments.get("rationale", ""),
        actor=actor,
        rejected_options=arguments.get("rejected_options"),
        constraints=arguments.get("constraints"),
        impact_radius=arguments.get("impact_radius", "local"),
    )
    event = MemoryEvent(
        event_id=str(uuid.uuid4()),
        memory_id=m.id,
        event_type=MemoryEventType.CREATED,
        project_id=project_id,
        payload=m.to_dict(),
        actor=actor,
        timestamp=datetime.now(timezone.utc),
    )
    EventLog(config.event_log_path).append(event)
    MemoryStore(config.memory_store_path).upsert(m)
    return json.dumps({"memory_id": m.id, "type": "decision", "status": "recorded"})

def _tool_record_incident(arguments: dict, config: Any) -> str:
    import uuid
    from datetime import datetime, timezone

    from depthfusion.core.memory import MemoryEvent, MemoryEventType
    from depthfusion.mcp.cognitive_tools import build_incident_memory
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    project_id = arguments.get("project_id", "")
    actor = arguments.get("actor", "unknown")
    severity = arguments.get("severity", "medium")
    m = build_incident_memory(
        project_id=project_id,
        error=arguments.get("error", ""),
        fix=arguments.get("fix", ""),
        lesson=arguments.get("lesson", ""),
        actor=actor,
        severity=severity,
        recurrence_risk=float(arguments.get("recurrence_risk", 0.3)),
    )
    event = MemoryEvent(
        event_id=str(uuid.uuid4()),
        memory_id=m.id,
        event_type=MemoryEventType.CREATED,
        project_id=project_id,
        payload=m.to_dict(),
        actor=actor,
        timestamp=datetime.now(timezone.utc),
    )
    EventLog(config.event_log_path).append(event)
    MemoryStore(config.memory_store_path).upsert(m)
    return json.dumps({"memory_id": m.id, "type": "operational", "severity": severity})

def _tool_mark_superseded(arguments: dict, config: Any) -> str:
    import uuid
    from datetime import datetime, timezone

    from depthfusion.core.memory import MemoryEvent, MemoryEventType
    from depthfusion.core.memory_object import MemoryStatus
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    project_id = arguments.get("project_id", "")
    old_id = arguments.get("old_memory_id", "")
    new_id = arguments.get("new_memory_id", "")
    reason = arguments.get("reason", "")
    actor = arguments.get("actor", "unknown")

    store = MemoryStore(config.memory_store_path)
    log = EventLog(config.event_log_path)
    old = store.get(old_id)
    if not old:
        return json.dumps({"error": f"memory {old_id} not found"})
    old.status = MemoryStatus.SUPERSEDED
    old.extra["superseded_by"] = new_id
    old.extra["superseded_reason"] = reason
    event = MemoryEvent(
        event_id=str(uuid.uuid4()),
        memory_id=old_id,
        event_type=MemoryEventType.SUPERSEDED,
        project_id=project_id,
        payload={"new_id": new_id, "reason": reason, "extra": {"acl_allow": [project_id]}},
        actor=actor,
        timestamp=datetime.now(timezone.utc),
    )
    log.append(event)
    store.upsert(old)
    return json.dumps({"status": "superseded", "old_id": old_id, "new_id": new_id})

def _tool_report_outcome(arguments: dict, config: Any) -> str:
    import uuid
    from datetime import datetime, timezone

    from depthfusion.core.memory import MemoryEvent, MemoryEventType
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    project_id = arguments.get("project_id", "")
    memory_id = arguments.get("memory_id", "")
    outcome = arguments.get("outcome", "")
    success = bool(arguments.get("success", False))
    actor = arguments.get("actor", "unknown")

    store = MemoryStore(config.memory_store_path)
    log = EventLog(config.event_log_path)
    m = store.get(memory_id)
    if not m:
        return json.dumps({"error": f"memory {memory_id} not found"})
    outcomes = m.extra.get("outcomes", [])
    outcomes.append({
        "outcome": outcome,
        "success": success,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    m.extra["outcomes"] = outcomes
    if success:
        m.confidence.verification_count += 1
        m.confidence.score = min(1.0, m.confidence.score + 0.05)
    event = MemoryEvent(
        event_id=str(uuid.uuid4()),
        memory_id=memory_id,
        event_type=MemoryEventType.OUTCOME_RECORDED,
        project_id=project_id,
        payload={"outcome": outcome, "success": success, "extra": {"acl_allow": [project_id]}},
        actor=actor,
        timestamp=datetime.now(timezone.utc),
    )
    log.append(event)
    store.upsert(m)
    return json.dumps({"status": "recorded", "memory_id": memory_id, "success": success})

def _tool_get_cognitive_state(arguments: dict, config: Any) -> str:
    from depthfusion.storage.event_log import EventLog
    from depthfusion.storage.memory_store import MemoryStore

    project_id = arguments.get("project_id", "")
    store = MemoryStore(config.memory_store_path)
    log = EventLog(config.event_log_path)
    total = store.count(project_id or None)
    active = len(store.query(project_id=project_id or None, limit=1000))
    return json.dumps({
        "project_id": project_id,
        "total_memories": total,
        "active_memories": active,
        "total_events": log.count(),
        "feature_flags": {
            "cognitive_retrieval": getattr(config, "cognitive_retrieval", False),
            "contradiction_engine": getattr(config, "contradiction_engine", False),
            "decision_memory": getattr(config, "decision_memory", False),
            "operational_memory": getattr(config, "operational_memory", False),
            "autonomic": getattr(config, "autonomic", False),
        },
    })

def register_decisions() -> None:
    """Register decisions domain tools (stub for v2 tooling framework)."""
    pass
