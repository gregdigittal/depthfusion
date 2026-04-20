# tests/test_graph/test_temporal_session_linker.py
"""TemporalSessionLinker + PRECEDED_BY traversal tests — S-50 / T-155.

≥ 8 tests per S-50 AC-4. Covers:
  * tokenize_session_content normalisation
  * _vocabulary_overlap set arithmetic
  * TemporalSessionLinker gate logic (time + overlap)
  * Edge direction: later PRECEDED_BY earlier
  * Traverser time-window filter integration
"""
from __future__ import annotations

from datetime import datetime, timedelta

from depthfusion.graph.extractor import make_entity_id
from depthfusion.graph.linker import (
    SessionRecord,
    TemporalSessionLinker,
    _vocabulary_overlap,
    make_edge_id,
    tokenize_session_content,
)
from depthfusion.graph.store import JSONGraphStore
from depthfusion.graph.traverser import traverse
from depthfusion.graph.types import Edge, Entity

# ---------------------------------------------------------------------------
# tokenize_session_content
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_lowercases_and_returns_set(self):
        result = tokenize_session_content("Hello WORLD hello")
        assert result == {"hello", "world"}

    def test_filters_short_tokens(self):
        """Tokens of length < 3 are noise (a, is, to). Drop them."""
        result = tokenize_session_content("a is to the redis database")
        assert "redis" in result
        assert "database" in result
        # "the" is length 3 so IS included; but "is", "to", "a" are dropped
        assert "is" not in result
        assert "to" not in result
        assert "a" not in result

    def test_empty_content_returns_empty_set(self):
        assert tokenize_session_content("") == set()

    def test_ignores_pure_punctuation(self):
        result = tokenize_session_content("!!! ??? ...")
        assert result == set()

    def test_idempotent(self):
        content = "BM25 ranking inverted index"
        once = tokenize_session_content(content)
        twice = tokenize_session_content(" ".join(once))
        assert once == twice


# ---------------------------------------------------------------------------
# _vocabulary_overlap
# ---------------------------------------------------------------------------

class TestVocabularyOverlap:
    def test_returns_intersection_cardinality(self):
        assert _vocabulary_overlap({"a", "b", "c"}, {"b", "c", "d"}) == 2

    def test_empty_sets_return_zero(self):
        assert _vocabulary_overlap(set(), {"a"}) == 0
        assert _vocabulary_overlap({"a"}, set()) == 0
        assert _vocabulary_overlap(set(), set()) == 0

    def test_disjoint_sets_return_zero(self):
        assert _vocabulary_overlap({"a", "b"}, {"c", "d"}) == 0

    def test_identical_sets_return_cardinality(self):
        s = {"x", "y", "z"}
        assert _vocabulary_overlap(s, s) == 3


# ---------------------------------------------------------------------------
# TemporalSessionLinker.link — the core gate logic
# ---------------------------------------------------------------------------

_DEFAULT_VOCAB = {"alpha", "beta", "gamma", "delta", "epsilon", "zeta"}


def _rec(
    sid: str,
    *,
    ts: str,
    vocab: set[str] | None = None,
    project: str = "test",
) -> SessionRecord:
    # Use `is None` rather than truthiness so an explicit empty set() isn't
    # silently coerced to the default vocabulary.
    return SessionRecord(
        session_id=sid,
        timestamp=ts,
        vocabulary=_DEFAULT_VOCAB if vocab is None else vocab,
        project=project,
    )


class TestTemporalSessionLinker:
    def test_links_close_sessions_with_shared_vocabulary(self):
        linker = TemporalSessionLinker(window_hours=48, min_overlap=3)
        a = _rec("sess-a", ts="2026-04-20T10:00:00", vocab={"redis", "cache", "auth", "token"})
        b = _rec("sess-b", ts="2026-04-20T14:00:00", vocab={"redis", "cache", "auth", "ttl"})
        edge = linker.link(a, b)
        assert edge is not None
        assert edge.relationship == "PRECEDED_BY"
        assert edge.metadata["overlap"] == 3
        assert edge.metadata["delta_hours"] == 4.0

    def test_rejects_pairs_outside_time_window(self):
        linker = TemporalSessionLinker(window_hours=24, min_overlap=2)
        a = _rec("a", ts="2026-04-18T10:00:00", vocab={"x", "y", "z"})
        b = _rec("b", ts="2026-04-20T10:00:00", vocab={"x", "y", "z"})  # 48h apart
        assert linker.link(a, b) is None

    def test_rejects_pairs_below_overlap_threshold(self):
        linker = TemporalSessionLinker(window_hours=48, min_overlap=5)
        a = _rec("a", ts="2026-04-20T10:00:00", vocab={"redis", "cache"})
        b = _rec("b", ts="2026-04-20T11:00:00", vocab={"redis", "cache"})
        # overlap=2 < min_overlap=5
        assert linker.link(a, b) is None

    def test_edge_direction_is_later_to_earlier(self):
        """B came AFTER A → edge `source=B, target=A, relationship=PRECEDED_BY`."""
        linker = TemporalSessionLinker(window_hours=48, min_overlap=2)
        earlier = _rec("early", ts="2026-04-20T10:00:00", vocab={"x", "y", "z"})
        later = _rec("late", ts="2026-04-20T15:00:00", vocab={"x", "y", "z"})

        # Pass in reverse order — linker normalises direction
        edge = linker.link(later, earlier)
        assert edge is not None
        assert edge.source_id == "late"
        assert edge.target_id == "early"
        assert edge.metadata["earlier_session"] == "early"
        assert edge.metadata["later_session"] == "late"

    def test_same_session_id_returns_none(self):
        """A session never precedes itself."""
        linker = TemporalSessionLinker()
        a = _rec("same", ts="2026-04-20T10:00:00")
        b = _rec("same", ts="2026-04-20T11:00:00")
        assert linker.link(a, b) is None

    def test_invalid_timestamp_returns_none(self):
        """Malformed ISO timestamps must not crash — return None."""
        linker = TemporalSessionLinker()
        a = _rec("a", ts="not-a-timestamp")
        b = _rec("b", ts="2026-04-20T10:00:00")
        assert linker.link(a, b) is None

    def test_empty_vocabularies_return_none(self):
        """Two empty-vocab sessions can't qualify even with time overlap."""
        linker = TemporalSessionLinker(window_hours=48, min_overlap=1)
        a = _rec("a", ts="2026-04-20T10:00:00", vocab=set())
        b = _rec("b", ts="2026-04-20T11:00:00", vocab=set())
        assert linker.link(a, b) is None

    def test_edge_id_stable_across_calls(self):
        """Re-running the linker on the same pair produces the same edge_id."""
        linker = TemporalSessionLinker(window_hours=48, min_overlap=2)
        a = _rec("a", ts="2026-04-20T10:00:00", vocab={"x", "y", "z"})
        b = _rec("b", ts="2026-04-20T14:00:00", vocab={"x", "y", "z"})
        e1 = linker.link(a, b)
        e2 = linker.link(a, b)
        assert e1 is not None and e2 is not None
        assert e1.edge_id == e2.edge_id

    def test_edge_id_stable_when_timestamps_are_equal(self):
        """Review-gate regression: when two sessions share a timestamp (batch
        import, sub-second creation), link(a,b) and link(b,a) must produce
        the SAME edge_id. Without the tie-break on session_id, the previous
        logic `ts_a <= ts_b` always kept arg-order, causing duplicate edges
        on upsert.
        """
        linker = TemporalSessionLinker(window_hours=1, min_overlap=2)
        a = _rec("sess-a", ts="2026-04-20T10:00:00", vocab={"x", "y", "z"})
        b = _rec("sess-b", ts="2026-04-20T10:00:00", vocab={"x", "y", "z"})
        e1 = linker.link(a, b)
        e2 = linker.link(b, a)
        assert e1 is not None and e2 is not None
        assert e1.edge_id == e2.edge_id
        # And direction is deterministic regardless of arg order
        assert e1.source_id == e2.source_id
        assert e1.target_id == e2.target_id


# ---------------------------------------------------------------------------
# link_all — pairwise over a list
# ---------------------------------------------------------------------------

class TestLinkAll:
    def test_produces_all_qualifying_pairs(self):
        linker = TemporalSessionLinker(window_hours=48, min_overlap=2)
        now = datetime(2026, 4, 20, 10, 0, 0)
        sessions = [
            SessionRecord(
                session_id=f"s{i}",
                timestamp=(now + timedelta(hours=i * 2)).isoformat(),
                vocabulary={"shared", "tokens", "here", f"unique{i}"},
                project="test",
            )
            for i in range(3)
        ]
        edges = linker.link_all(sessions)
        # C(3,2) = 3 pairs, all within 48h window with overlap=3
        assert len(edges) == 3
        assert all(e.relationship == "PRECEDED_BY" for e in edges)

    def test_deduplicates_by_edge_id(self):
        """If the same pair appears twice (shouldn't happen with combinations
        but defense in depth), dedup by edge_id.
        """
        linker = TemporalSessionLinker(window_hours=48, min_overlap=2)
        now = datetime(2026, 4, 20, 10, 0, 0)
        sessions = [
            SessionRecord("s0", now.isoformat(), {"a", "b", "c"}, "test"),
            SessionRecord("s1", (now + timedelta(hours=1)).isoformat(),
                          {"a", "b", "c"}, "test"),
        ]
        edges = linker.link_all(sessions)
        assert len(edges) == 1

    def test_empty_input_returns_empty(self):
        assert TemporalSessionLinker().link_all([]) == []

    def test_single_session_returns_empty(self):
        """Can't form pairs from a single session."""
        linker = TemporalSessionLinker()
        sessions = [_rec("only", ts="2026-04-20T10:00:00")]
        assert linker.link_all(sessions) == []


# ---------------------------------------------------------------------------
# Traverser integration — time-window filter (T-154)
# ---------------------------------------------------------------------------

class TestTraverseWithTimeWindow:
    def _seed_graph(self, tmp_path):
        """Create a graph with 3 session entities and PRECEDED_BY edges."""
        store = JSONGraphStore(path=tmp_path / "graph.json")
        now_iso = "2026-04-20T10:00:00"
        for i, sid in enumerate(["sess-a", "sess-b", "sess-c"]):
            store.upsert_entity(Entity(
                entity_id=make_entity_id(sid, "session", "test"),
                name=sid, type="session", project="test",
                source_files=[], confidence=1.0,
                first_seen=now_iso, metadata={},
            ))
        a = make_entity_id("sess-a", "session", "test")
        b = make_entity_id("sess-b", "session", "test")
        c = make_entity_id("sess-c", "session", "test")
        # b PRECEDED_BY a, delta=4h
        store.upsert_edge(Edge(
            edge_id=make_edge_id(b, a, "PRECEDED_BY"),
            source_id=b, target_id=a, relationship="PRECEDED_BY",
            weight=1.0, signals=["temporal", "vocabulary_overlap"],
            metadata={"delta_hours": 4.0, "overlap": 3},
        ))
        # c PRECEDED_BY a, delta=72h (outside 48h window)
        store.upsert_edge(Edge(
            edge_id=make_edge_id(c, a, "PRECEDED_BY"),
            source_id=c, target_id=a, relationship="PRECEDED_BY",
            weight=1.0, signals=["temporal"],
            metadata={"delta_hours": 72.0, "overlap": 5},
        ))
        return store, a, b, c

    def test_time_window_filters_out_stale_edges(self, tmp_path):
        """time_window_hours=48 must exclude the 72h edge."""
        store, a, b, c = self._seed_graph(tmp_path)
        # Traverse from c — it's only connected via a 72h edge
        result = traverse(c, store, time_window_hours=48.0)
        assert result is not None
        assert len(result.connected) == 0  # 72h edge filtered out

    def test_time_window_includes_edges_within_limit(self, tmp_path):
        store, a, b, c = self._seed_graph(tmp_path)
        result = traverse(b, store, time_window_hours=48.0)
        assert result is not None
        assert len(result.connected) == 1
        neighbor, edge = result.connected[0]
        assert neighbor.name == "sess-a"
        assert edge.metadata["delta_hours"] == 4.0

    def test_no_time_window_includes_all_edges(self, tmp_path):
        """Back-compat: time_window_hours=None (default) preserves old behaviour."""
        store, a, b, c = self._seed_graph(tmp_path)
        result = traverse(c, store)  # no time filter
        assert result is not None
        assert len(result.connected) == 1

    def test_non_temporal_edges_are_not_filtered(self, tmp_path):
        """CO_OCCURS and Haiku-inferred edges have no delta_hours. A time
        filter must NOT drop them — it applies only to edges that DO carry
        a delta_hours in metadata.
        """
        store = JSONGraphStore(path=tmp_path / "g2.json")
        a = make_entity_id("classA", "class", "test")
        b = make_entity_id("classB", "class", "test")
        for eid, name in [(a, "classA"), (b, "classB")]:
            store.upsert_entity(Entity(
                entity_id=eid, name=name, type="class", project="test",
                source_files=[], confidence=1.0,
                first_seen="2026-04-20T10:00:00", metadata={},
            ))
        # CO_OCCURS edge with no delta_hours in metadata
        store.upsert_edge(Edge(
            edge_id=make_edge_id(a, b, "CO_OCCURS"),
            source_id=a, target_id=b, relationship="CO_OCCURS",
            weight=1.0, signals=["co_occurrence"], metadata={},
        ))
        result = traverse(a, store, time_window_hours=1.0)
        assert result is not None
        # CO_OCCURS edge must survive — no delta_hours in its metadata
        assert len(result.connected) == 1

    def test_relationship_filter_restricts_to_preceded_by(self, tmp_path):
        """Edge-kind filtering works end-to-end for the new edge type."""
        store, a, b, c = self._seed_graph(tmp_path)
        result = traverse(b, store, relationship_filter=["PRECEDED_BY"])
        assert result is not None
        assert all(e.relationship == "PRECEDED_BY" for _, e in result.connected)
