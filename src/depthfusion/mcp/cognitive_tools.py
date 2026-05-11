from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from depthfusion.core.memory_object import (
    MemoryConfidence,
    MemoryObject,
    MemorySource,
    MemoryStatus,
    MemoryType,
)


def build_decision_memory(
    project_id: str,
    decision: str,
    rationale: str,
    actor: str,
    rejected_options: Optional[list[str]] = None,
    constraints: Optional[list[str]] = None,
    impact_radius: str = "local",
) -> MemoryObject:
    if not rationale.strip():
        raise ValueError("rationale must not be empty for decision memories")
    now = datetime.now(timezone.utc)
    extra = {
        "decision": decision,
        "rationale": rationale,
        "rejected_options": rejected_options or [],
        "constraints": constraints or [],
        "impact_radius": impact_radius,
    }
    return MemoryObject(
        id=str(uuid.uuid4()),
        project_id=project_id,
        type=MemoryType.DECISION,
        content=f"DECISION: {decision}\nRATIONALE: {rationale}",
        summary=decision[:120],
        status=MemoryStatus.ACTIVE,
        source=MemorySource(agent=actor),
        extra=extra,
        created_at=now,
        updated_at=now,
    )


def build_incident_memory(
    project_id: str,
    error: str,
    fix: str,
    lesson: str,
    actor: str,
    severity: str = "medium",
    recurrence_risk: float = 0.3,
) -> MemoryObject:
    now = datetime.now(timezone.utc)
    recurrence_risk = max(0.0, min(1.0, recurrence_risk))
    extra = {
        "error": error,
        "fix": fix,
        "lesson": lesson,
        "severity": severity,
        "recurrence_risk": recurrence_risk,
    }
    return MemoryObject(
        id=str(uuid.uuid4()),
        project_id=project_id,
        type=MemoryType.OPERATIONAL,
        content=f"ERROR: {error}\nFIX: {fix}\nLESSON: {lesson}",
        summary=f"[{severity.upper()}] {error[:80]}",
        status=MemoryStatus.ACTIVE,
        source=MemorySource(agent=actor),
        extra=extra,
        created_at=now,
        updated_at=now,
    )
