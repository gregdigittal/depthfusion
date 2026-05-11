from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from depthfusion.core.memory_object import MemoryObject

_NEGATION_RE = re.compile(
    r"\b(not|never|no|isn't|aren't|doesn't|don't|won't|cannot|can't)\b",
    re.IGNORECASE,
)


class ConflictSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConflictStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    AUTO_EMITTED = "auto_emitted"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


@dataclass
class Conflict:
    memory_a_id: str
    memory_b_id: str
    conflict_type: str
    description: str
    severity: ConflictSeverity
    status: ConflictStatus
    confidence_a: float
    confidence_b: float
    pinned_winner: Optional[str] = None


class ContradictionEngine:
    def __init__(self, auto_emit_threshold: float = 0.85) -> None:
        self._threshold = auto_emit_threshold

    def detect(self, a: MemoryObject, b: MemoryObject) -> list[Conflict]:
        conflicts: list[Conflict] = []
        if self._has_negation_conflict(a.content, b.content):
            min_conf = min(a.confidence.score, b.confidence.score)
            severity = ConflictSeverity.HIGH if min_conf > 0.8 else ConflictSeverity.MEDIUM
            status = (
                ConflictStatus.AUTO_EMITTED
                if min_conf >= self._threshold
                else ConflictStatus.PENDING_REVIEW
            )
            pinned_winner: Optional[str] = None
            if a.pinned:
                pinned_winner = a.id
            elif b.pinned:
                pinned_winner = b.id
            conflicts.append(
                Conflict(
                    memory_a_id=a.id,
                    memory_b_id=b.id,
                    conflict_type="negation",
                    description=(
                        f"Negation detected between '{a.content[:50]}'"
                        f" and '{b.content[:50]}'"
                    ),
                    severity=severity,
                    status=status,
                    confidence_a=a.confidence.score,
                    confidence_b=b.confidence.score,
                    pinned_winner=pinned_winner,
                )
            )
        return conflicts

    def _has_negation_conflict(self, text_a: str, text_b: str) -> bool:
        a_has_neg = bool(_NEGATION_RE.search(text_a))
        b_has_neg = bool(_NEGATION_RE.search(text_b))
        if a_has_neg == b_has_neg:
            return False
        a_stripped = _NEGATION_RE.sub("", text_a.lower()).strip()
        b_stripped = _NEGATION_RE.sub("", text_b.lower()).strip()
        return self._token_overlap(a_stripped, b_stripped) > 0.4

    @staticmethod
    def _token_overlap(a: str, b: str) -> float:
        ta = set(a.split())
        tb = set(b.split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / max(len(ta), len(tb))
