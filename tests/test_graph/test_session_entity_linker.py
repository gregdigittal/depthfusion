"""Unit tests for session_entity_linker.py (S-212).

Tests use a minimal MagicMock GraphStore so no real storage is required.
The core invariants tested:

  1. get_sessions_from_events() extracts SessionRecords from event+memory entities.
  2. get_unlinked_sessions() filters to sessions with no existing PRECEDED_BY edges.
  3. link_and_upsert() dry_run returns correct counts without calling upsert.
  4. link_and_upsert() apply mode upserts entities and edges.
  5. Empty entity list returns gracefully (zero records, zero edges).
  6. Sessions without vocabulary (no memory entity match) are skipped.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from depthfusion.graph.extractor import make_entity_id
from depthfusion.graph.session_entity_linker import (
    get_sessions_from_events,
    get_unlinked_sessions,
    link_and_upsert,
)
from depthfusion.graph.types import Entity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _memory_entity(entity_id: str, name: str, project: str = "proj") -> Entity:
    return Entity(
        entity_id=entity_id,
        name=name,
        type="memory",
        project=project,
        source_files=[],
        confidence=1.0,
        first_seen="2026-01-01T00:00:00",
        metadata={},
    )


def _event_entity(
    entity_id: str,
    session_id: str,
    content_hash: str,
    agent_id: str = "agent-ops",
    project: str = "proj",
    first_seen: str = "2026-01-02T10:00:00",
) -> Entity:
    return Entity(
        entity_id=entity_id,
        name=f"event-{entity_id}",
        type="event",
        project=project,
        source_files=[],
        confidence=1.0,
        first_seen=first_seen,
        metadata={
            "session_id": session_id,
            "content_hash": content_hash,
            "agent_id": agent_id,
            "project_slug": project,
        },
    )


def _mock_store(entities: list[Entity], existing_edges: list | None = None) -> MagicMock:
    """Build a minimal GraphStore mock."""
    store = MagicMock()
    store.all_entities.return_value = entities
    store.get_edges.return_value = existing_edges or []
    store.upsert_entity.return_value = None
    store.upsert_edge.return_value = None
    return store


# ---------------------------------------------------------------------------
# Tests: get_sessions_from_events
# ---------------------------------------------------------------------------

class TestGetSessionsFromEvents:
    def test_empty_store_returns_empty(self) -> None:
        store = _mock_store([])
        result = get_sessions_from_events(store)
        assert result == []

    def test_single_session_one_event(self) -> None:
        mem = _memory_entity("hash001", "depthfusion recall pipeline refactor")
        evt = _event_entity("evt001", session_id="session-A", content_hash="hash001")
        store = _mock_store([mem, evt])

        records = get_sessions_from_events(store)
        assert len(records) == 1
        assert records[0].session_id == "session-A"
        assert records[0].project == "proj"
        # Vocabulary should contain tokens from the memory name + agent_id.
        assert "depthfusion" in records[0].vocabulary
        assert "recall" in records[0].vocabulary

    def test_multiple_events_same_session_merged(self) -> None:
        mem_a = _memory_entity("hash001", "token_alpha bravo charlie")
        mem_b = _memory_entity("hash002", "delta echo foxtrot")
        evt_a = _event_entity("evt001", session_id="S1", content_hash="hash001")
        evt_b = _event_entity("evt002", session_id="S1", content_hash="hash002")
        store = _mock_store([mem_a, mem_b, evt_a, evt_b])

        records = get_sessions_from_events(store)
        assert len(records) == 1
        vocab = records[0].vocabulary
        # All tokens from both memory entities should be merged.
        assert "token_alpha" in vocab or "alpha" in vocab  # tokenizer extracts "alpha"
        assert "bravo" in vocab
        assert "delta" in vocab
        assert "echo" in vocab

    def test_two_sessions_returned(self) -> None:
        mem = _memory_entity("hash001", "recall pipeline token_set")
        evt_a = _event_entity("evt001", session_id="S1", content_hash="hash001",
                              first_seen="2026-01-02T08:00:00")
        evt_b = _event_entity("evt002", session_id="S2", content_hash="hash001",
                              first_seen="2026-01-02T10:00:00")
        store = _mock_store([mem, evt_a, evt_b])

        records = get_sessions_from_events(store)
        session_ids = {r.session_id for r in records}
        assert session_ids == {"S1", "S2"}

    def test_event_with_no_memory_match_is_skipped(self) -> None:
        # Event points to a content_hash that doesn't correspond to any memory entity.
        evt = _event_entity("evt001", session_id="S1", content_hash="nonexistent",
                            agent_id="")
        store = _mock_store([evt])

        records = get_sessions_from_events(store)
        # No vocabulary can be built → session skipped.
        assert records == []

    def test_earliest_timestamp_wins(self) -> None:
        mem = _memory_entity("hash001", "token_alpha token_bravo")
        evt_early = _event_entity("evt001", session_id="S1", content_hash="hash001",
                                  first_seen="2026-01-01T06:00:00")
        evt_late = _event_entity("evt002", session_id="S1", content_hash="hash001",
                                 first_seen="2026-01-01T12:00:00")
        store = _mock_store([mem, evt_early, evt_late])

        records = get_sessions_from_events(store)
        assert len(records) == 1
        assert records[0].timestamp == "2026-01-01T06:00:00"


# ---------------------------------------------------------------------------
# Tests: get_unlinked_sessions
# ---------------------------------------------------------------------------

class TestGetUnlinkedSessions:
    def test_no_existing_edges_returns_all(self) -> None:
        mem = _memory_entity("hash001", "token_alpha token_bravo token_charlie")
        evt = _event_entity("evt001", session_id="S1", content_hash="hash001")
        store = _mock_store([mem, evt], existing_edges=[])

        unlinked = get_unlinked_sessions(store)
        assert len(unlinked) == 1
        assert unlinked[0].session_id == "S1"

    def test_session_with_existing_edge_filtered_out(self) -> None:
        mem = _memory_entity("hash001", "token_alpha token_bravo token_charlie")
        evt = _event_entity("evt001", session_id="S1", content_hash="hash001")
        # Simulate S1 already having a PRECEDED_BY edge.
        store = _mock_store([mem, evt], existing_edges=[MagicMock()])

        unlinked = get_unlinked_sessions(store)
        assert unlinked == []

    def test_mixed_sessions_partial_filter(self) -> None:
        mem = _memory_entity("hash001", "token_alpha token_bravo token_charlie")
        evt_a = _event_entity("evt001", session_id="S1", content_hash="hash001",
                              first_seen="2026-01-01T08:00:00")
        evt_b = _event_entity("evt002", session_id="S2", content_hash="hash001",
                              first_seen="2026-01-02T08:00:00")
        store = _mock_store([mem, evt_a, evt_b])

        # S1 is linked, S2 is not.
        entity_id_s1 = make_entity_id("S1", "session", "proj")

        def mock_get_edges(entity_id, relationship_filter=None):  # noqa: ANN001
            if entity_id == entity_id_s1:
                return [MagicMock()]  # has an edge
            return []

        store.get_edges.side_effect = mock_get_edges

        unlinked = get_unlinked_sessions(store)
        assert len(unlinked) == 1
        assert unlinked[0].session_id == "S2"


# ---------------------------------------------------------------------------
# Tests: link_and_upsert
# ---------------------------------------------------------------------------

class TestLinkAndUpsert:
    def _two_linkable_sessions(self) -> list:
        """Return two sessions that pass both time + vocab gates."""
        from depthfusion.graph.linker import SessionRecord

        tokens = {
            "recall", "pipeline", "depthfusion", "session", "linker",
            "temporal", "graph", "store", "entity", "event",
        }  # 10 tokens — well above default min_overlap=5

        return [
            SessionRecord(
                session_id="S1",
                timestamp="2026-01-01T08:00:00",
                vocabulary=tokens,
                project="proj",
            ),
            SessionRecord(
                session_id="S2",
                timestamp="2026-01-02T10:00:00",  # ~26h later — within 168h
                vocabulary=tokens,
                project="proj",
            ),
        ]

    def test_dry_run_returns_correct_counts_no_upserts(self) -> None:
        store = _mock_store([])
        sessions = self._two_linkable_sessions()

        result = link_and_upsert(sessions, store, dry_run=True)

        assert result["dry_run"] is True
        assert result["sessions"] == 2
        assert result["edges_added"] == 1  # one pair → one edge
        store.upsert_entity.assert_not_called()
        store.upsert_edge.assert_not_called()

    def test_apply_mode_calls_upserts(self) -> None:
        store = _mock_store([])
        sessions = self._two_linkable_sessions()

        result = link_and_upsert(sessions, store, dry_run=False)

        assert result["dry_run"] is False
        assert result["sessions"] == 2
        assert result["edges_added"] == 1
        # Two session entities must be upserted.
        assert store.upsert_entity.call_count == 2
        # One PRECEDED_BY edge must be upserted.
        assert store.upsert_edge.call_count == 1

    def test_empty_sessions_returns_zero(self) -> None:
        store = _mock_store([])
        result = link_and_upsert([], store, dry_run=False)

        assert result == {"sessions": 0, "edges_added": 0, "dry_run": False}
        store.upsert_entity.assert_not_called()
        store.upsert_edge.assert_not_called()

    def test_sessions_outside_window_no_edges(self) -> None:
        from depthfusion.graph.linker import SessionRecord

        tokens = {
            "recall", "pipeline", "depthfusion", "session", "linker",
            "temporal", "graph", "store", "entity", "event",
        }
        sessions = [
            SessionRecord("S1", "2026-01-01T00:00:00", tokens, "proj"),
            SessionRecord("S2", "2026-01-15T00:00:00", tokens, "proj"),  # 14 days apart
        ]
        store = _mock_store([])

        result = link_and_upsert(sessions, store, dry_run=True)

        # 14 days = 336h > 168h default window → no edge.
        assert result["edges_added"] == 0

    def test_sessions_insufficient_vocab_overlap_no_edges(self) -> None:
        from depthfusion.graph.linker import SessionRecord

        sessions = [
            SessionRecord("S1", "2026-01-01T08:00:00",
                          {"alpha", "bravo", "charlie"}, "proj"),  # 3 tokens
            SessionRecord("S2", "2026-01-01T10:00:00",
                          {"alpha", "bravo", "charlie"}, "proj"),  # same 3 → overlap=3 < 5
        ]
        store = _mock_store([])

        result = link_and_upsert(sessions, store, dry_run=True)

        # overlap(3) < min_overlap(5) → no edge.
        assert result["edges_added"] == 0

    def test_edge_entity_ids_are_graph_entity_ids(self) -> None:
        """Upserted edges must reference entity_ids, not raw session_ids."""
        store = _mock_store([])
        sessions = self._two_linkable_sessions()

        link_and_upsert(sessions, store, dry_run=False)

        upserted_edge = store.upsert_edge.call_args[0][0]
        s1_entity_id = make_entity_id("S1", "session", "proj")
        s2_entity_id = make_entity_id("S2", "session", "proj")
        # Either S1 or S2 can be source/target depending on which came first.
        assert upserted_edge.source_id in (s1_entity_id, s2_entity_id)
        assert upserted_edge.target_id in (s1_entity_id, s2_entity_id)
        assert upserted_edge.source_id != upserted_edge.target_id
        assert upserted_edge.relationship == "PRECEDED_BY"
