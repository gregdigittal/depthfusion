# src/depthfusion/graph/linker.py
"""Edge creation signals: co-occurrence, haiku-inferred, temporal proximity.

Edge kinds produced by this module:
  * CO_OCCURS       — CoOccurrenceLinker (entity-level, same block)
  * CO_WORKED_ON    — TemporalLinker (entity-level, across sessions)
  * PRECEDED_BY     — TemporalSessionLinker (session-level, v0.5 S-50 / CM-4)
  * CAUSES/FIXES/DEPENDS_ON/REPLACES/CONFLICTS_WITH — HaikuLinker (semantic)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Any

from depthfusion.graph.types import Edge, Entity

logger = logging.getLogger(__name__)

_VALID_RELATIONSHIPS = frozenset({
    "CO_OCCURS", "CAUSES", "FIXES", "DEPENDS_ON",
    "REPLACES", "CONFLICTS_WITH", "CO_WORKED_ON",
    "PRECEDED_BY",  # v0.5 S-50 / CM-4 — session-level temporal edge
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


def propagate_edge_acl(edge: Edge, acl_allow: list[str]) -> Edge:
    """T-619: stamp a source document's ``acl_allow`` onto an edge in place.

    Graph edges derived from a source document must inherit that document's
    ACL so the store's required-ACL rule (``_validate_graph_acl``) is
    satisfied and traversal honours visibility. Mutates and returns ``edge``
    for fluent use; the stored list is a fresh copy.

    Raises ``ValueError`` if ``acl_allow`` is empty — mirroring the store's
    invariant so the failure surfaces at link time, not write time.
    """
    if not acl_allow:
        raise ValueError("acl_allow is required")
    edge.metadata["acl_allow"] = list(acl_allow)
    return edge


def propagate_acl_from_entities(edges: list[Edge], entities: list[Entity]) -> list[Edge]:
    """T-619: give each edge the ACL of its source entity's document.

    Looks up the source entity for every edge and inherits its
    ``metadata["acl_allow"]``. Edges whose source entity is unknown or whose
    source entity carries no ACL are left untouched (the write path will
    reject them — explicit absence beats a silently-wrong ACL).
    """
    acl_by_entity = {
        e.entity_id: e.metadata.get("acl_allow")
        for e in entities
    }
    for edge in edges:
        acl = acl_by_entity.get(edge.source_id)
        if acl:
            edge.metadata["acl_allow"] = list(acl)
    return edges


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
                adapter_name="co_occurrence_linker",
                source_type="decision",
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
                        adapter_name="temporal_linker",
                        source_type="session",
                        metadata={"delta_hours": delta_hours},
                    ))
        return edges


# ---------------------------------------------------------------------------
# v0.5 S-50 / CM-4 — session-level temporal linker
# ---------------------------------------------------------------------------

# Minimal alphanumeric tokenizer for vocabulary-overlap comparison.
# Same shape as retrieval/bm25.tokenize() but without the stopword removal —
# vocabulary overlap is more discriminating when stopwords are filtered by
# the caller (or left in, depending on use case).
# Minimum match length is 3 (enforced by the `{2,}` quantifier on chars
# after the leading letter) so the caller doesn't need a post-filter.
_SESSION_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")


@dataclass
class SessionRecord:
    """A session's identity, timestamp, and vocabulary.

    `session_id` is any stable identifier the caller uses (e.g. file stem of
    the `.tmp` session state file). `timestamp` is ISO-8601. `vocabulary` is
    a set of tokens — typically extracted once with `tokenize_session_content()`
    and reused across pairwise comparisons to avoid O(n²) retokenization.
    """
    session_id: str
    timestamp: str                         # ISO-8601
    vocabulary: set[str]
    project: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


def tokenize_session_content(content: str) -> set[str]:
    """Produce a token set suitable for vocabulary-overlap comparison.

    Lowercased alphanumeric tokens of length ≥ 3 (filters most stopwords
    and noise). Idempotent — safe to call on already-tokenized content.
    """
    if not content:
        return set()
    # The regex guarantees length >= 3, so no post-filter is needed.
    return {t.lower() for t in _SESSION_TOKEN_RE.findall(content)}


def _vocabulary_overlap(a: set[str], b: set[str]) -> int:
    """Return |a ∩ b| — the cardinality of the shared token set."""
    if not a or not b:
        return 0
    # Intersect the smaller set against the larger for a minor perf win
    # on very uneven sessions.
    if len(a) > len(b):
        a, b = b, a
    return sum(1 for tok in a if tok in b)


class TemporalSessionLinker:
    """Create PRECEDED_BY edges between sessions close in time AND topic.

    Directionality: `session_B PRECEDED_BY session_A` — i.e. A came BEFORE B.
    The edge is emitted with B as source and A as target, so
    `traverse(B.entity_id, relationship_filter=["PRECEDED_BY"])` walks
    backward through time (natural for "what did we do recently").

    Dual gate:
      1. Time window: |t_B - t_A| ≤ `window_hours` (default 48h)
      2. Vocabulary overlap: |vocab_A ∩ vocab_B| ≥ `min_overlap` (default 5
         shared alphanumeric tokens — tunable for corpus size)

    If either gate fails the linker returns None. Callers pass already-
    tokenized SessionRecord instances; the linker does not read files.

    The edge metadata records `delta_hours` and `overlap` for downstream
    use — e.g. the traverser's `time_window_hours` filter reads
    `delta_hours`, and UI layers can show the overlap as provenance.
    """

    def __init__(
        self,
        window_hours: float = 48.0,
        min_overlap: int = 5,
    ) -> None:
        self._window_hours = float(window_hours)
        self._min_overlap = int(min_overlap)

    def link(
        self,
        session_a: SessionRecord,
        session_b: SessionRecord,
    ) -> Edge | None:
        """Return a PRECEDED_BY edge if both gates pass, else None.

        Order-independent: the linker figures out which session came first
        from the timestamps, so callers don't need to pre-sort.
        """
        if session_a.session_id == session_b.session_id:
            return None  # a session never precedes itself

        try:
            ts_a = datetime.fromisoformat(session_a.timestamp)
            ts_b = datetime.fromisoformat(session_b.timestamp)
        except ValueError:
            return None

        delta = ts_b - ts_a
        delta_hours = abs(delta.total_seconds()) / 3600.0
        if delta_hours > self._window_hours:
            return None

        overlap = _vocabulary_overlap(session_a.vocabulary, session_b.vocabulary)
        if overlap < self._min_overlap:
            return None

        # Normalise direction: later PRECEDED_BY earlier.
        # When timestamps are identical (realistic: batch imports, sub-second
        # creation), tie-break on session_id so link(a,b) and link(b,a)
        # produce the same edge_id — otherwise the store accumulates two
        # edges for a single pair of sessions on re-upsert.
        if ts_a < ts_b or (
            ts_a == ts_b and session_a.session_id <= session_b.session_id
        ):
            earlier, later = session_a, session_b
        else:
            earlier, later = session_b, session_a

        return Edge(
            edge_id=make_edge_id(later.session_id, earlier.session_id, "PRECEDED_BY"),
            source_id=later.session_id,
            target_id=earlier.session_id,
            relationship="PRECEDED_BY",
            weight=1.0,
            signals=["temporal", "vocabulary_overlap"],
            adapter_name="temporal_session_linker",
            source_type="session",
            metadata={
                "delta_hours": round(delta_hours, 3),
                "overlap": overlap,
                "earlier_session": earlier.session_id,
                "later_session": later.session_id,
            },
        )

    def link_all(self, sessions: list[SessionRecord]) -> list[Edge]:
        """Emit PRECEDED_BY edges for every qualifying pair in `sessions`.

        O(n²) in the number of sessions; callers are expected to window
        the input (e.g. last 30 sessions) before passing. Returns a flat
        list with duplicates deduplicated by `edge_id`.
        """
        seen_edges: set[str] = set()
        edges: list[Edge] = []
        for a, b in combinations(sessions, 2):
            edge = self.link(a, b)
            if edge is None:
                continue
            if edge.edge_id in seen_edges:
                continue
            seen_edges.add(edge.edge_id)
            edges.append(edge)
        return edges


def _normalize_entity_name(name: str) -> str:
    """Normalize an entity name for near-duplicate comparison.

    Lowercases, strips a trailing ``()`` (function marker), and collapses
    all non-alphanumeric runs to a single space, then trims. So
    "Acme Corp.", "ACME  Corp", and "acme corp" all normalize to "acme corp",
    while "rrf_fuse()" and "RRF Fuse" both become "rrf fuse".
    """
    lowered = name.strip().lower()
    if lowered.endswith("()"):
        lowered = lowered[:-2]
    collapsed = re.sub(r"[^a-z0-9]+", " ", lowered)
    return collapsed.strip()


def _name_similarity(a: str, b: str) -> float:
    """Token-set Jaccard similarity of two normalized names in [0.0, 1.0].

    Identical normalized names score 1.0. Pure token overlap is used (not edit
    distance) so word-order and punctuation differences don't matter:
    "Acme Corporation" vs "Corporation Acme" → 1.0.
    """
    na, nb = _normalize_entity_name(a), _normalize_entity_name(b)
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


class EntityDeduplicator:
    """T-620: merge near-duplicate entity nodes by normalized name + type.

    Two entities are considered duplicates when they share the same ``type``
    AND their normalized-name similarity is ``>= threshold`` (default 0.85).
    Entities below the threshold stay distinct. The merge is order-stable:
    the first-seen entity is the canonical survivor; later duplicates fold
    their ``source_files`` and ``acl_allow`` into it.

    Merge policy on collision:
      * survivor keeps the higher ``confidence``
      * ``source_files`` are unioned (order-preserving, all filenames preserved)
      * ``metadata["acl_allow"]`` is **intersected** (fail-closed — see note)
      * ``metadata["merge_confidence"]`` records the highest similarity score
        observed across all merges into this survivor

    ACL policy — fail-closed via intersection
    -----------------------------------------
    When two entities from different source documents are merged, we must
    decide what access the canonical node grants.  Two valid options:

      * UNION  — readable by any principal who could read *any* source doc.
                 Widens access: a principal who could only read doc-B now
                 sees content from doc-A too after the merge.

      * INTERSECTION — readable only by principals who could read *all*
                       source docs.  Never widens access beyond what every
                       contributor independently allowed.

    We choose **INTERSECTION** (fail-closed).  A merged node may cover content
    from multiple documents; exposing it to a principal who lacked access to
    even one of those documents would silently bypass that document's ACL.
    The security cost of intersection is that a merged node becomes invisible
    to principals who only have partial access — this is the correct trade-off
    for a graph that enforces document-level ACLs.

    Edge case: if intersection is empty (no principal can see all sources)
    ``acl_allow`` is set to ``[]``.  The store's ``_validate_graph_acl``
    invariant will then reject any attempt to write the node, which surfaces
    the conflict to the caller rather than silently creating an inaccessible
    but present node.
    """

    def __init__(self, threshold: float = 0.85) -> None:
        self._threshold = float(threshold)

    def deduplicate(self, entities: list[Entity]) -> list[Entity]:
        survivors: list[Entity] = []
        for candidate in entities:
            match, sim = self._find_match(candidate, survivors)
            if match is None:
                survivors.append(candidate)
            else:
                self._merge_into(match, candidate, sim)
        return survivors

    def _find_match(
        self, candidate: Entity, survivors: list[Entity]
    ) -> tuple[Entity | None, float]:
        """Return the first survivor that is a near-duplicate of *candidate*.

        Returns ``(entity, similarity)`` on a match, or ``(None, 0.0)`` when
        no survivor qualifies.
        """
        for survivor in survivors:
            if survivor.type != candidate.type:
                continue
            sim = _name_similarity(survivor.name, candidate.name)
            if sim >= self._threshold:
                return survivor, sim
        return None, 0.0

    @staticmethod
    def _merge_into(survivor: Entity, dup: Entity, sim: float) -> None:
        # Keep the stronger entity-level confidence.
        if dup.confidence > survivor.confidence:
            survivor.confidence = dup.confidence
        # Union source files (order-preserving).
        for sf in dup.source_files:
            if sf not in survivor.source_files:
                survivor.source_files.append(sf)
        # Intersect ACLs — fail-closed: never widen access beyond what every
        # contributing document independently allowed.  See class docstring.
        survivor_acl = set(survivor.metadata.get("acl_allow") or [])
        dup_acl = set(dup.metadata.get("acl_allow") or [])
        if survivor_acl and dup_acl:
            merged_acl = sorted(survivor_acl & dup_acl)
        elif survivor_acl:
            # dup had no ACL at all — treat as "no principals" → empty intersection
            merged_acl = []
        elif dup_acl:
            # survivor had no ACL at all — same
            merged_acl = []
        else:
            merged_acl = []
        survivor.metadata["acl_allow"] = merged_acl
        # Track the merge confidence (highest similarity seen across all merges).
        prev = float(survivor.metadata.get("merge_confidence", 0.0))
        survivor.metadata["merge_confidence"] = max(prev, sim)


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
            adapter_name="haiku_linker",
            source_type="decision",
            metadata={},
        )
