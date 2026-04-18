# src/depthfusion/graph/linker.py
"""Edge creation signals: co-occurrence, haiku-inferred, temporal proximity."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from itertools import combinations
from typing import Any

from depthfusion.graph.types import Edge, Entity

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
    """Use Claude Haiku to infer semantic relationship type between two entities.

    v0.5.0 T-120: migrated to the provider-agnostic backend interface.
    Also closes the Phase 1 §1.2 C2 latent bug — the previous implementation
    called `anthropic.Anthropic()` with NO `api_key=` argument, falling back
    to the SDK's `ANTHROPIC_API_KEY` default lookup (a billing-isolation
    hazard). The new factory-resolved HaikuBackend always uses explicit
    `api_key=DEPTHFUSION_API_KEY`.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        backend: Any = None,
    ) -> None:
        self._model = model
        if backend is not None:
            self._backend = backend
            return
        # The v0.4.x HaikuLinker was available whenever any API key was set
        # (no DEPTHFUSION_HAIKU_ENABLED gate — unlike HaikuSummarizer/Extractor).
        # Preserve that: resolve via factory, which returns NullBackend when
        # no key is present.
        from depthfusion.backends.factory import get_backend
        self._backend = get_backend("linker")

    def is_available(self) -> bool:
        return self._backend.healthy() and self._backend.name != "null"

    def infer_relationship(
        self, entity_a: Entity, entity_b: Entity, context: str
    ) -> Edge | None:
        if not self.is_available():
            return None
        try:
            raw = self._backend.complete(
                _HAIKU_PROMPT.format(
                    name_a=entity_a.name, type_a=entity_a.type,
                    name_b=entity_b.name, type_b=entity_b.type,
                    context=context[:500],
                ),
                max_tokens=64,
            )
            if not raw:
                return None
            data: dict = json.loads(raw)
            rel = data.get("relationship", "")
        except Exception as exc:  # noqa: BLE001 — graceful-degradation contract
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
