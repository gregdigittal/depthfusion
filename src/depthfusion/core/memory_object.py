from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class MemoryType(str, Enum):
    DECISION = "decision"
    SEMANTIC = "semantic"
    OPERATIONAL = "operational"
    PROCEDURAL = "procedural"
    EPISODIC = "episodic"
    SOCIAL = "social"
    TEMPORAL = "temporal"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


@dataclass
class MemorySource:
    agent: str = "unknown"
    session_id: str = ""
    file_path: str = ""
    line_range: Optional[tuple[int, int]] = None

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "session_id": self.session_id,
            "file_path": self.file_path,
            "line_range": list(self.line_range) if self.line_range else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemorySource":
        lr = d.get("line_range")
        return cls(
            agent=d.get("agent", "unknown"),
            session_id=d.get("session_id", ""),
            file_path=d.get("file_path", ""),
            line_range=tuple(lr) if lr else None,
        )


@dataclass
class MemoryScope:
    project_id: str = ""
    file_patterns: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    regime: str = ""

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "file_patterns": self.file_patterns,
            "tags": self.tags,
            "regime": self.regime,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryScope":
        return cls(
            project_id=d.get("project_id", ""),
            file_patterns=d.get("file_patterns", []),
            tags=d.get("tags", []),
            regime=d.get("regime", ""),
        )


@dataclass
class MemoryValidity:
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_stale: bool = False
    stale_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "is_stale": self.is_stale,
            "stale_reason": self.stale_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryValidity":
        return cls(
            valid_from=datetime.fromisoformat(d["valid_from"]) if d.get("valid_from") else None,
            valid_until=datetime.fromisoformat(d["valid_until"]) if d.get("valid_until") else None,
            is_stale=d.get("is_stale", False),
            stale_reason=d.get("stale_reason", ""),
        )


@dataclass
class MemoryConfidence:
    score: float = 0.7
    verification_count: int = 0
    contradiction_count: int = 0
    last_verified_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "verification_count": self.verification_count,
            "contradiction_count": self.contradiction_count,
            "last_verified_at": self.last_verified_at.isoformat()
            if self.last_verified_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryConfidence":
        return cls(
            score=d.get("score", 0.7),
            verification_count=d.get("verification_count", 0),
            contradiction_count=d.get("contradiction_count", 0),
            last_verified_at=datetime.fromisoformat(d["last_verified_at"])
            if d.get("last_verified_at") else None,
        )


@dataclass
class MemoryObject:
    id: str
    project_id: str
    type: MemoryType
    content: str
    summary: str = ""
    status: MemoryStatus = MemoryStatus.ACTIVE
    pinned: bool = False
    source: MemorySource = field(default_factory=MemorySource)
    scope: MemoryScope = field(default_factory=MemoryScope)
    validity: MemoryValidity = field(default_factory=MemoryValidity)
    confidence: MemoryConfidence = field(default_factory=MemoryConfidence)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    extra: dict[str, Any] = field(default_factory=dict)
    event_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "type": self.type.value,
            "content": self.content,
            "summary": self.summary,
            "status": self.status.value,
            "pinned": self.pinned,
            "source": self.source.to_dict(),
            "scope": self.scope.to_dict(),
            "validity": self.validity.to_dict(),
            "confidence": self.confidence.to_dict(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "extra": self.extra,
            "event_version": self.event_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryObject":
        return cls(
            id=d["id"],
            project_id=d["project_id"],
            type=MemoryType(d["type"]),
            content=d["content"],
            summary=d.get("summary", ""),
            status=MemoryStatus(d.get("status", "active")),
            pinned=d.get("pinned", False),
            source=MemorySource.from_dict(d.get("source", {})),
            scope=MemoryScope.from_dict(d.get("scope", {})),
            validity=MemoryValidity.from_dict(d.get("validity", {})),
            confidence=MemoryConfidence.from_dict(d.get("confidence", {})),
            created_at=datetime.fromisoformat(d["created_at"])
            if "created_at" in d else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(d["updated_at"])
            if "updated_at" in d else datetime.now(timezone.utc),
            extra=d.get("extra", {}),
            event_version=d.get("event_version", 0),
        )
