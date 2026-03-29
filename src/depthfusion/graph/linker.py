# src/depthfusion/graph/linker.py
"""Edge creation signals: co-occurrence, haiku-inferred, temporal proximity."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from itertools import combinations
from typing import Any

from depthfusion.graph.types import Entity, Edge

logger = logging.getLogger(__name__)

_VALID_RELATIONSHIPS = frozenset({
    "CO_OCCURS", "CAUSES", "FIXES", "DEPENDS_ON",
    "REPLACES", "CONFLICTS_WITH", "CO_WORKED_ON",
})

# Haiku may only produce semantic relationship types.
# CO_OCCURS and CO_WORKED_ON are structural signals owned by
# CoOccurrenceLinker and TemporalLinker — never Haiku-inferred.
_HAIKU_VALID_RELATIONSHIPS = frozenset({
    "CAUSES", "FIXES", "DEPENDS_ON", "REPLACES", "CONFLICTS_WITH",
})

_HAIKU_PROMPT = """\
Given two code entities and context, classify their relationship.
Return ONLY a JSON object: {{"relationship": "<type>"}}

Valid types: CAUSES, FIXES, DEPENDS_ON, REPLACES, CONFLICTS_WITH
Choose the strongest signal. If uncertain, omit (return {{}}).

Entity A: {name_a} ({type_a})
Entity B: {name_b} ({type_b})
Context: {context}"""


def make_edge_id(source_id: str, target_id: str, relationship: str) -> str:
    raw = f"{source_id}{target_id}{relationship}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


class CoOccurrenceLinker:
    """Create CO_OCCURS edges between all entity pairs in the same memory block."""

    def link(self, entities: list[Entity]) -> list[Edge]:
        edges: list[Edge] = []
        for a, b in combinations(entities, 2):
            edges.append(Edge(
                edge_id=make_edge_id(a.entity_id, b.entity_id, "CO_OCCURS"),
                source_id=a.entity_id,
                target_id=b.entity_id,
                relationship="CO_OCCURS",
                weight=1.0,
                signals=["co_occurrence"],
                metadata={},
            ))
        return edges


class TemporalLinker:
    """Create CO_WORKED_ON edges for entities that appear across sessions within N hours."""

    def __init__(self, window_hours: int = 48):
        self._window_hours = window_hours

    def link_across_sessions(
        self,
        session_a_entities: list[Entity],
        session_a_ts: str,
        session_b_entities: list[Entity],
        session_b_ts: str,
    ) -> list[Edge]:
        try:
            ts_a = datetime.fromisoformat(session_a_ts)
            ts_b = datetime.fromisoformat(session_b_ts)
        except ValueError:
            return []

        delta_hours = abs((ts_b - ts_a).total_seconds()) / 3600
        if delta_hours > self._window_hours:
            return []

        edges: list[Edge] = []
        for a in session_a_entities:
            for b in session_b_entities:
                if a.entity_id != b.entity_id:
                    edges.append(Edge(
                        edge_id=make_edge_id(a.entity_id, b.entity_id, "CO_WORKED_ON"),
                        source_id=a.entity_id,
                        target_id=b.entity_id,
                        relationship="CO_WORKED_ON",
                        weight=1.0,
                        signals=["temporal"],
                        metadata={"delta_hours": delta_hours},
                    ))
        return edges


class HaikuLinker:
    """Use Claude Haiku to infer semantic relationship type between two entities."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self._model = model
        self._client: Any = None
        try:
            import anthropic
            if os.environ.get("ANTHROPIC_API_KEY"):
                self._client = anthropic.Anthropic()
        except ImportError:
            pass

    def infer_relationship(
        self, entity_a: Entity, entity_b: Entity, context: str
    ) -> Edge | None:
        if not self._client:
            return None
        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=64,
                messages=[{
                    "role": "user",
                    "content": _HAIKU_PROMPT.format(
                        name_a=entity_a.name, type_a=entity_a.type,
                        name_b=entity_b.name, type_b=entity_b.type,
                        context=context[:500],
                    ),
                }],
            )
            raw = msg.content[0].text.strip()
            data: dict = json.loads(raw)
            rel = data.get("relationship", "")
        except Exception as exc:
            logger.debug("HaikuLinker failed: %s", exc)
            return None

        if rel not in _HAIKU_VALID_RELATIONSHIPS:
            return None

        return Edge(
            edge_id=make_edge_id(entity_a.entity_id, entity_b.entity_id, rel),
            source_id=entity_a.entity_id,
            target_id=entity_b.entity_id,
            relationship=rel,
            weight=1.0,
            signals=["haiku"],
            metadata={},
        )
