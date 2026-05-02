# src/depthfusion/graph/traverser.py
"""Graph traversal, query expansion, and score boosting."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from depthfusion.graph.types import Edge, Entity, TraversalResult

if TYPE_CHECKING:
    from depthfusion.graph.store import ChromaGraphStore, JSONGraphStore, SQLiteGraphStore

# Threshold: entities below this are excluded from query expansion
_CONFIDENCE_THRESHOLD = 0.70
# Max boost per block, applied additively
_MAX_BOOST = 0.30
# Boost per unit of edge weight
_BOOST_PER_WEIGHT_UNIT = 0.10

logger = logging.getLogger(__name__)


def traverse(
    entity_id: str,
    store: "JSONGraphStore | SQLiteGraphStore | ChromaGraphStore",
    depth: int = 1,
    relationship_filter: list[str] | None = None,
    time_window_hours: float | None = None,
) -> TraversalResult | None:
    """Walk the graph from entity_id up to `depth` hops.

    Returns TraversalResult with all reachable (entity, edge) pairs,
    or None if the origin entity is not found.

    Args:
        entity_id: origin entity for the traversal.
        store: graph backend (JSON or SQLite).
        depth: maximum hops (default 1).
        relationship_filter: if provided, only edges whose `relationship`
            is in this list are traversed. Passed through to
            `store.get_edges()` for push-down filtering where supported.
        time_window_hours: v0.5 S-50 — if provided, only traverse edges
            whose `metadata["delta_hours"]` is at most this value. Edges
            without `delta_hours` in metadata are INCLUDED (back-compat:
            non-temporal edges like CO_OCCURS or Haiku-inferred semantic
            edges don't carry a time delta and should not be filtered out).
    """
    origin = store.get_entity(entity_id)
    if origin is None:
        return None

    visited: set[str] = {entity_id}
    connected: list[tuple[Entity, Edge]] = []

    frontier: set[str] = {entity_id}
    for _ in range(depth):
        next_frontier: set[str] = set()
        for fid in frontier:
            edges = store.get_edges(fid, relationship_filter=relationship_filter)
            for edge in edges:
                # v0.5 T-154: time-bucketed traversal. Skip temporal edges
                # that exceed the window; keep non-temporal edges (which
                # have no delta_hours in metadata) regardless.
                if time_window_hours is not None:
                    delta = edge.metadata.get("delta_hours")
                    if delta is not None and float(delta) > time_window_hours:
                        continue
                neighbor_id = (
                    edge.target_id if edge.source_id == fid else edge.source_id
                )
                if neighbor_id not in visited:
                    neighbor = store.get_entity(neighbor_id)
                    if neighbor:
                        connected.append((neighbor, edge))
                        next_frontier.add(neighbor_id)
                        visited.add(neighbor_id)
        frontier = next_frontier

    return TraversalResult(
        origin_entity=origin,
        connected=connected,
        source_memories=[],
        depth=depth,
    )


def expand_query(query: str, store: "JSONGraphStore | SQLiteGraphStore | ChromaGraphStore") -> str:
    """Expand a query string with entity-linked terms from the graph.

    1. Find entities whose name appears in the query (case-insensitive word match).
    2. For each found entity, look up its neighbors in the graph.
    3. Add neighbor entity names as extra query terms.

    Original terms are always preserved. Returns expanded query string.
    Skips entities with confidence < 0.70.
    """
    all_entities = store.all_entities()
    query_entities: list[Entity] = []

    for entity in all_entities:
        if entity.confidence < _CONFIDENCE_THRESHOLD:
            continue
        # Word-boundary match (case-insensitive) — clean the name for function types
        clean_name = entity.name.rstrip("()")
        pattern = r"\b" + re.escape(clean_name) + r"\b"
        if re.search(pattern, query, re.IGNORECASE):
            query_entities.append(entity)

    if not query_entities:
        return query

    extra_terms: list[str] = []
    for entity in query_entities:
        result = traverse(entity.entity_id, store, depth=1)
        if result:
            for neighbor, _ in result.connected:
                if neighbor.confidence >= _CONFIDENCE_THRESHOLD:
                    # Add the clean name (without trailing "()")
                    term = neighbor.name.rstrip("()")
                    if term.lower() not in query.lower():
                        extra_terms.append(term)

    if not extra_terms:
        return query

    return query + " " + " ".join(extra_terms)


def boost_scores(
    blocks: list[dict],
    top_result_entity_id: str,
    store: "JSONGraphStore | SQLiteGraphStore | ChromaGraphStore",
) -> list[dict]:
    """Boost block scores if they mention entities linked to the top-1 result.

    Boost = min(edge_weight × 0.10, 0.30), additive, per block.
    Returns new list with boosted scores; original dicts are not mutated.
    """
    result = traverse(top_result_entity_id, store, depth=1)
    if not result:
        return blocks

    # Map entity names → edge weight for linked neighbors
    linked: dict[str, float] = {}
    for neighbor, edge in result.connected:
        clean = neighbor.name.rstrip("()")
        linked[clean.lower()] = edge.weight

    boosted: list[dict] = []
    for block in blocks:
        content_lower = block.get("content", "").lower()
        boost = 0.0
        for name_lower, weight in linked.items():
            if re.search(r"\b" + re.escape(name_lower) + r"\b", content_lower):
                boost += weight * _BOOST_PER_WEIGHT_UNIT
        boost = min(boost, _MAX_BOOST)
        boosted.append({**block, "score": block["score"] + boost})
    return boosted
