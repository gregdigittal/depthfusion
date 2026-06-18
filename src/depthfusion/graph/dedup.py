# src/depthfusion/graph/dedup.py
"""Entity deduplication: merge near-duplicate entity nodes by normalised name+type.

T-620: Cross-source entity linker with confidence-scored deduplication.

Design
------
*Near-duplicate* detection: two Entity nodes are candidates for merging when
their (normalised_name, type) pair is identical.  Normalisation strips
punctuation, lowercases, and collapses whitespace — so "RecallPipeline",
"recall_pipeline", and "Recall Pipeline" all normalise to "recallpipeline".

ACL semantics (fail-closed):
  The merged entity's acl_allow is the INTERSECTION of the two source
  entities' acl_allow lists.  An empty intersection produces an empty list
  (no-one may access the merged node), which is the safe fail-closed outcome.
  Never expand permissions on merge.

Merge confidence:
  ``merge_confidence`` is stored in ``merged_entity.metadata["merge_confidence"]``.
  For exact (normalised) name+type matches the confidence is 1.0.  Future
  heuristics (Levenshtein, embedding cosine) may produce lower values; the
  key is always present so callers can filter by threshold.

The deduplicator does NOT write to any store directly — it returns a list of
*canonical* entities and a list of *merged-away* entity_ids that the caller
should replace / remove from the store.

Usage
-----
::

    deduplicator = EntityDeduplicator()
    canonical, merged_away = deduplicator.deduplicate(entities)
    # canonical: list[Entity] — one per unique (norm_name, type)
    # merged_away: list[str]  — entity_ids that were absorbed
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from depthfusion.graph.types import Entity

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _normalise(name: str) -> str:
    """Lowercase + strip non-alphanumeric characters for fuzzy matching."""
    lower = name.lower()
    # NFKD normalisation covers accented characters
    decomposed = unicodedata.normalize("NFKD", lower)
    return _NON_ALNUM.sub("", decomposed)


def _acl_intersect(a: list[str], b: list[str]) -> list[str]:
    """Fail-closed ACL merge: return intersection, preserving order from *a*."""
    b_set = set(b)
    return [p for p in a if p in b_set]


@dataclass
class MergeRecord:
    """Records which entities were merged into which canonical entity."""
    canonical_id: str
    absorbed_ids: list[str] = field(default_factory=list)
    merge_confidence: float = 1.0


class EntityDeduplicator:
    """Merge near-duplicate Entity nodes by normalised name+type.

    Stateless — construct once, call ``deduplicate()`` multiple times.
    """

    def deduplicate(
        self, entities: list[Entity]
    ) -> tuple[list[Entity], list[str]]:
        """Merge near-duplicate entities and return canonical set.

        Parameters
        ----------
        entities:
            Input entities (may contain near-duplicates from different sources).

        Returns
        -------
        canonical: list[Entity]
            One entity per unique (normalised_name, type) pair.  Merged
            entities have ``metadata["merge_confidence"]`` set.
        merged_away: list[str]
            entity_ids of entities absorbed into a canonical entity.
            Callers should remove or remap these from the graph store.
        """
        # Map (norm_name, type) → canonical entity
        canonical_map: dict[tuple[str, str], Entity] = {}
        merged_away: list[str] = []

        for entity in entities:
            key = (_normalise(entity.name), entity.type)
            if key not in canonical_map:
                # First occurrence: becomes the canonical node
                canonical_map[key] = _clone_entity(entity)
            else:
                # Near-duplicate: merge into existing canonical
                canon = canonical_map[key]
                canon = _merge_into(canon, entity)
                canonical_map[key] = canon
                merged_away.append(entity.entity_id)

        return list(canonical_map.values()), merged_away


def _clone_entity(entity: Entity) -> Entity:
    """Return a shallow copy with a cloned metadata dict."""
    return Entity(
        entity_id=entity.entity_id,
        name=entity.name,
        type=entity.type,
        project=entity.project,
        source_files=list(entity.source_files),
        confidence=entity.confidence,
        first_seen=entity.first_seen,
        metadata=dict(entity.metadata),
    )


def _merge_into(canon: Entity, other: Entity) -> Entity:
    """Merge *other* into *canon* (in-place mutation of canon).

    Rules:
    - source_files: union, preserving canon order
    - confidence: max of the two
    - first_seen: earliest ISO-8601 timestamp
    - acl_allow: INTERSECTION (fail-closed)
    - merge_confidence: 1.0 for exact normalised-name matches
    """
    # Source files: union (preserve canon order, append new)
    existing = set(canon.source_files)
    for sf in other.source_files:
        if sf not in existing:
            canon.source_files.append(sf)
            existing.add(sf)

    # Confidence: take the higher value
    canon.confidence = max(canon.confidence, other.confidence)

    # first_seen: keep the earliest
    if other.first_seen < canon.first_seen:
        canon.first_seen = other.first_seen

    # ACL: fail-closed intersection
    canon_acl: list[str] = canon.metadata.get("acl_allow") or []
    other_acl: list[str] = other.metadata.get("acl_allow") or []
    if canon_acl or other_acl:
        canon.metadata["acl_allow"] = _acl_intersect(canon_acl, other_acl)

    # Record merge confidence (1.0 for exact normalised match)
    canon.metadata["merge_confidence"] = 1.0

    return canon


__all__ = ["EntityDeduplicator", "MergeRecord", "_normalise", "_acl_intersect"]
