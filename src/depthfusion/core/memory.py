from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class MemoryEventType(str, Enum):
    CREATED = "memory.created"
    VERIFIED = "memory.verified"
    CONTRADICTED = "memory.contradicted"
    SUPERSEDED = "memory.superseded"
    MERGED = "memory.merged"
    DECAYED = "memory.decayed"
    ARCHIVED = "memory.archived"
    USED = "memory.used"
    OUTCOME_RECORDED = "memory.outcome_recorded"


@dataclass(frozen=True)
class MemoryEvent:
    event_id: str
    memory_id: str
    event_type: MemoryEventType
    project_id: str
    payload: dict[str, Any]
    actor: str
    timestamp: datetime
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "memory_id": self.memory_id,
            "event_type": self.event_type.value,
            "project_id": self.project_id,
            "payload": copy.deepcopy(self.payload),
            "actor": self.actor,
            "timestamp": self.timestamp.isoformat(),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryEvent":
        return cls(
            event_id=d["event_id"],
            memory_id=d["memory_id"],
            event_type=MemoryEventType(d["event_type"]),
            project_id=d["project_id"],
            payload=d.get("payload", {}),
            actor=d.get("actor", "unknown"),
            timestamp=datetime.fromisoformat(d["timestamp"]),
            schema_version=d.get("schema_version", 1),
        )
