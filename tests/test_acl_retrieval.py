"""T-571 / T-572: ACL retrieval tests.

Verifies that:
- MemoryStore.search() only returns records the principal can see.
- ChromaDBStore.query() (mocked) applies ACL metadata filter.
- HNSWStore.search() applies doc_mask / acl_cache filter.
- BM25.rank_with_mask() excludes unauthorized document indices.
- GraphStore traverse() excludes entities the principal cannot see.

Principal stub is a minimal dataclass that mirrors identity.models.Principal
so that these tests run without requiring the full identity stack.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Minimal Principal stub (no OIDC / token dependencies)
# ---------------------------------------------------------------------------

@dataclass
class _Principal:
    principal_id: str
    upn: str = ""
    display_name: str = ""
    groups: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# T-571 / T-572: MemoryStore.search() ACL filter
# ---------------------------------------------------------------------------

from depthfusion.core.memory_object import MemoryObject, MemoryType
from depthfusion.storage.memory_store import MemoryStore


def _make_memory(acl_allow: list[str]) -> MemoryObject:
    return MemoryObject(
        id=_uid(),
        project_id="proj",
        type=MemoryType.SEMANTIC,
        content="secret content " + _uid(),
        extra={"acl_allow": acl_allow},
    )


class TestMemoryStoreACLRetrieval:
    """MemoryStore.search() must honour principal ACL."""

    def setup_method(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._store = MemoryStore(Path(self._tmp.name) / "test.db")

    def teardown_method(self) -> None:
        self._tmp.cleanup()

    def test_authorized_principal_sees_own_record(self) -> None:
        p = _Principal(principal_id="alice")
        mem = _make_memory(acl_allow=["alice"])
        self._store.upsert(mem)

        results = self._store.search("secret content", principal=p)
        ids = [r.id for r in results]
        assert mem.id in ids

    def test_unauthorized_principal_cannot_see_record(self) -> None:
        p_bob = _Principal(principal_id="bob")
        mem = _make_memory(acl_allow=["alice"])
        self._store.upsert(mem)

        results = self._store.search("secret content", principal=p_bob)
        ids = [r.id for r in results]
        assert mem.id not in ids, "bob should NOT see alice's record"

    def test_group_member_sees_group_record(self) -> None:
        p = _Principal(principal_id="carol", groups=["team-alpha"])
        mem = _make_memory(acl_allow=["team-alpha"])
        self._store.upsert(mem)

        results = self._store.search("secret content", principal=p)
        ids = [r.id for r in results]
        assert mem.id in ids, "carol (member of team-alpha) should see record"

    def test_non_group_member_excluded(self) -> None:
        p = _Principal(principal_id="dave", groups=["team-beta"])
        mem = _make_memory(acl_allow=["team-alpha"])
        self._store.upsert(mem)

        results = self._store.search("secret content", principal=p)
        ids = [r.id for r in results]
        assert mem.id not in ids, "dave (team-beta) should NOT see team-alpha record"

    def test_none_principal_bypasses_acl(self) -> None:
        """Internal callers (principal=None) see all records."""
        mem = _make_memory(acl_allow=["alice"])
        self._store.upsert(mem)

        results = self._store.search("secret content", principal=None)
        ids = [r.id for r in results]
        assert mem.id in ids

    def test_mixed_acl_returns_only_authorized(self) -> None:
        p = _Principal(principal_id="alice")
        alice_mem = _make_memory(acl_allow=["alice"])
        bob_mem = _make_memory(acl_allow=["bob"])
        self._store.upsert(alice_mem)
        self._store.upsert(bob_mem)

        results = self._store.search("secret content", principal=p)
        ids = {r.id for r in results}
        assert alice_mem.id in ids
        assert bob_mem.id not in ids


# ---------------------------------------------------------------------------
# T-572: BM25 doc_mask filter
# ---------------------------------------------------------------------------

from depthfusion.retrieval.bm25 import BM25, tokenize


class TestBM25DocMask:
    """BM25.rank_with_mask() must exclude unauthorized document indices."""

    def _build_corpus(self) -> tuple[BM25, list[str]]:
        docs = [
            "authentication token security login",
            "database schema migration postgres",
            "frontend react component rendering",
            "secret admin password credentials",  # index 3 — restricted
        ]
        tokens = [tokenize(d) for d in docs]
        return BM25(tokens), docs

    def test_rank_with_mask_excludes_unauthorized_index(self) -> None:
        bm25, _ = self._build_corpus()
        query_terms = tokenize("admin secret credentials")

        # Full rank — index 3 should appear (highest relevance)
        all_results = bm25.rank_all(query_terms)
        all_indices = [idx for idx, _ in all_results if _ > 0]
        assert 3 in all_indices, "index 3 should rank in unconstrained results"

        # Now restrict to indices 0, 1, 2 (exclude index 3)
        allowed_mask = frozenset({0, 1, 2})
        masked_results = bm25.rank_with_mask(query_terms, allowed_mask)
        masked_indices = [idx for idx, _ in masked_results]
        assert 3 not in masked_indices, "index 3 should be excluded by doc_mask"

    def test_rank_with_mask_returns_correct_authorized_results(self) -> None:
        bm25, _ = self._build_corpus()
        query_terms = tokenize("database schema migration")

        allowed_mask = frozenset({1, 2})  # only database and frontend docs
        results = bm25.rank_with_mask(query_terms, allowed_mask)
        indices = [idx for idx, score in results if score > 0]
        assert 1 in indices, "database doc (index 1) should be in results"
        assert 0 not in indices
        assert 3 not in indices

    def test_rank_with_mask_empty_mask_returns_empty(self) -> None:
        bm25, _ = self._build_corpus()
        query_terms = tokenize("any query")
        results = bm25.rank_with_mask(query_terms, frozenset())
        assert results == []

    def test_rank_with_mask_full_mask_matches_rank_all(self) -> None:
        bm25, _ = self._build_corpus()
        query_terms = tokenize("authentication login")
        full_mask = frozenset(range(bm25.N))
        masked = bm25.rank_with_mask(query_terms, full_mask)
        all_r = bm25.rank_all(query_terms)
        # Both should have the same (idx, score) pairs, same order.
        assert masked == all_r


# ---------------------------------------------------------------------------
# T-572: HNSWStore.search() ACL filtering via acl_cache
# ---------------------------------------------------------------------------

from depthfusion.retrieval.hnsw_store import HNSWStore


class TestHNSWStoreACLFilter:
    """HNSWStore.search() must filter by principal when acl_cache is populated."""

    def _make_store(self, tmp: str) -> HNSWStore:
        return HNSWStore(
            index_path=Path(tmp) / "hnsw.bin",
            model_name="all-MiniLM-L6-v2",
        )

    def test_register_acl_populates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            store.register_acl("doc-1", ["alice"])
            assert hasattr(store, "_acl_cache")
            assert store._acl_cache["doc-1"] == ["alice"]

    def test_search_acl_filter_excluded_discovery_id(self) -> None:
        """When hnsw_ready=False the store returns []; with a mock knn_query
        we can verify the ACL filter logic independently of hnswlib/embedder."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            store.register_acl("doc-alice", ["alice"])
            store.register_acl("doc-bob", ["bob"])

            # Simulate hnsw_ready=True and a knn response returning both docs.

            store.hnsw_ready = True
            store._index = MagicMock()
            store._label_map = {"doc-alice": 0, "doc-bob": 1}
            store._next_label = 2
            store._dimension = 4
            # embed() will fail (no real backend), so patch it.
            store._embedder_failed = False

            fake_vector = [0.1, 0.2, 0.3, 0.4]
            knn_labels = [[0, 1]]
            knn_dists = [[0.1, 0.2]]
            store._index.knn_query.return_value = (
                knn_labels,
                knn_dists,
            )

            with patch.object(store, "embed", return_value=fake_vector):
                # Alice as principal — should only see doc-alice.
                p_alice = _Principal(principal_id="alice")
                results = store.search("some query", k=10, principal=p_alice)
                ids = [r["discovery_id"] for r in results]
                assert "doc-alice" in ids
                assert "doc-bob" not in ids, "bob's doc should not appear for alice"

                # Bob as principal — should only see doc-bob.
                p_bob = _Principal(principal_id="bob")
                results = store.search("some query", k=10, principal=p_bob)
                ids = [r["discovery_id"] for r in results]
                assert "doc-bob" in ids
                assert "doc-alice" not in ids

    def test_search_no_principal_returns_all(self) -> None:
        """principal=None bypasses ACL filtering."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            store.register_acl("doc-alice", ["alice"])
            store.register_acl("doc-bob", ["bob"])

            store.hnsw_ready = True
            store._index = MagicMock()
            store._label_map = {"doc-alice": 0, "doc-bob": 1}
            store._next_label = 2
            store._dimension = 4

            fake_vector = [0.1, 0.2, 0.3, 0.4]
            store._index.knn_query.return_value = ([[0, 1]], [[0.1, 0.2]])

            with patch.object(store, "embed", return_value=fake_vector):
                results = store.search("some query", k=10, principal=None)
                ids = [r["discovery_id"] for r in results]
                assert "doc-alice" in ids
                assert "doc-bob" in ids

    def test_search_group_membership_grants_access(self) -> None:
        """A principal whose group is in acl_allow should see the document."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            store.register_acl("doc-team", ["team-alpha"])

            store.hnsw_ready = True
            store._index = MagicMock()
            store._label_map = {"doc-team": 0}
            store._next_label = 1
            store._dimension = 4

            fake_vector = [0.1, 0.2, 0.3, 0.4]
            store._index.knn_query.return_value = ([[0]], [[0.1]])

            with patch.object(store, "embed", return_value=fake_vector):
                p = _Principal(principal_id="carol", groups=["team-alpha"])
                results = store.search("some query", k=10, principal=p)
                ids = [r["discovery_id"] for r in results]
                assert "doc-team" in ids

    def test_search_acl_resolver_callable(self) -> None:
        """acl_resolver kwarg overrides _acl_cache lookup."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            # No register_acl — resolver provided externally.
            acl_db = {"doc-x": ["alice"], "doc-y": ["bob"]}

            store.hnsw_ready = True
            store._index = MagicMock()
            store._label_map = {"doc-x": 0, "doc-y": 1}
            store._next_label = 2
            store._dimension = 4

            fake_vector = [0.1, 0.2, 0.3, 0.4]
            store._index.knn_query.return_value = ([[0, 1]], [[0.1, 0.2]])

            with patch.object(store, "embed", return_value=fake_vector):
                p = _Principal(principal_id="alice")
                results = store.search(
                    "query", k=10, principal=p, acl_resolver=acl_db.get
                )
                ids = [r["discovery_id"] for r in results]
                assert "doc-x" in ids
                assert "doc-y" not in ids


# ---------------------------------------------------------------------------
# T-571 / T-572: ChromaDBStore.query() ACL filter (mocked ChromaDB)
# ---------------------------------------------------------------------------

from depthfusion.storage.vector_store import ChromaDBStore


class TestChromaDBStoreACLFilter:
    """ChromaDBStore.query() must filter by principal ACL metadata."""

    def _make_chroma_result(
        self, ids: list[str], acl_values: list[Any]
    ) -> dict:
        """Build a mock ChromaDB query() result dict."""
        metadatas = [{"acl_allow": json.dumps(acl)} for acl in acl_values]
        distances = [[0.1 * (i + 1) for i in range(len(ids))]]
        documents = [["content-" + id_ for id_ in ids]]
        return {
            "ids": [ids],
            "distances": distances,
            "documents": documents,
            "metadatas": [metadatas],
        }

    def test_authorized_principal_sees_own_record(self) -> None:
        store = MagicMock(spec=ChromaDBStore)
        # Re-bind the real query() method to the mock instance.
        store.query = lambda *a, **kw: ChromaDBStore.query(store, *a, **kw)
        store._get_embedding = MagicMock(return_value=None)
        store.count = MagicMock(return_value=2)
        store._collection = MagicMock()
        store._collection.query.return_value = self._make_chroma_result(
            ["doc-alice", "doc-bob"],
            [["alice"], ["bob"]],
        )

        p = _Principal(principal_id="alice")
        results = store.query("test", top_k=10, principal=p)
        ids = [r["chunk_id"] for r in results]
        assert "doc-alice" in ids
        assert "doc-bob" not in ids

    def test_unauthorized_principal_excluded(self) -> None:
        store = MagicMock(spec=ChromaDBStore)
        store.query = lambda *a, **kw: ChromaDBStore.query(store, *a, **kw)
        store._get_embedding = MagicMock(return_value=None)
        store.count = MagicMock(return_value=1)
        store._collection = MagicMock()
        store._collection.query.return_value = self._make_chroma_result(
            ["doc-alice"],
            [["alice"]],
        )

        p = _Principal(principal_id="bob")
        results = store.query("test", top_k=10, principal=p)
        assert results == [], "bob should not see alice's doc"

    def test_none_principal_bypasses_filter(self) -> None:
        store = MagicMock(spec=ChromaDBStore)
        store.query = lambda *a, **kw: ChromaDBStore.query(store, *a, **kw)
        store._get_embedding = MagicMock(return_value=None)
        store.count = MagicMock(return_value=2)
        store._collection = MagicMock()
        store._collection.query.return_value = self._make_chroma_result(
            ["doc-alice", "doc-bob"],
            [["alice"], ["bob"]],
        )

        results = store.query("test", top_k=10, principal=None)
        ids = [r["chunk_id"] for r in results]
        assert "doc-alice" in ids
        assert "doc-bob" in ids

    def test_group_membership_grants_access(self) -> None:
        store = MagicMock(spec=ChromaDBStore)
        store.query = lambda *a, **kw: ChromaDBStore.query(store, *a, **kw)
        store._get_embedding = MagicMock(return_value=None)
        store.count = MagicMock(return_value=1)
        store._collection = MagicMock()
        store._collection.query.return_value = self._make_chroma_result(
            ["doc-team"],
            [["team-alpha"]],
        )

        p = _Principal(principal_id="carol", groups=["team-alpha"])
        results = store.query("test", top_k=10, principal=p)
        ids = [r["chunk_id"] for r in results]
        assert "doc-team" in ids

    def test_acl_allow_as_list_in_metadata(self) -> None:
        """acl_allow stored as a Python list (not JSON string) also works."""
        store = MagicMock(spec=ChromaDBStore)
        store.query = lambda *a, **kw: ChromaDBStore.query(store, *a, **kw)
        store._get_embedding = MagicMock(return_value=None)
        store.count = MagicMock(return_value=2)
        store._collection = MagicMock()
        # Inject list directly (not JSON-encoded) in metadatas
        store._collection.query.return_value = {
            "ids": [["doc-a", "doc-b"]],
            "distances": [[0.1, 0.2]],
            "documents": [["content-a", "content-b"]],
            "metadatas": [[{"acl_allow": ["alice"]}, {"acl_allow": ["bob"]}]],
        }

        p = _Principal(principal_id="alice")
        results = store.query("test", top_k=10, principal=p)
        ids = [r["chunk_id"] for r in results]
        assert "doc-a" in ids
        assert "doc-b" not in ids


# ---------------------------------------------------------------------------
# T-571: GraphStore traverse() ACL filter
# ---------------------------------------------------------------------------

from datetime import datetime, timezone

from depthfusion.graph.store import JSONGraphStore
from depthfusion.graph.traverser import traverse
from depthfusion.graph.types import Edge, Entity


def _make_entity(
    eid: str,
    name: str,
    acl_allow: list[str] | None = None,
) -> Entity:
    meta: dict = {}
    if acl_allow is not None:
        meta["acl_allow"] = acl_allow
    return Entity(
        entity_id=eid,
        name=name,
        type="concept",
        project="test",
        source_files=[],
        confidence=1.0,
        first_seen=datetime.now(timezone.utc).isoformat(),
        metadata=meta,
    )


def _make_edge(src: str, tgt: str, acl_allow: list[str] | None = None) -> Edge:
    meta: dict = {}
    if acl_allow is not None:
        meta["acl_allow"] = acl_allow
    return Edge(
        edge_id=_uid(),
        source_id=src,
        target_id=tgt,
        relationship="CO_OCCURS",
        weight=1.0,
        signals=[],
        metadata=meta,
    )


class TestGraphTraverseACL:
    """traverse() must exclude entities/edges the principal cannot see."""

    def setup_method(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._store = JSONGraphStore(path=Path(self._tmp.name) / "graph.json")

    def teardown_method(self) -> None:
        self._tmp.cleanup()

    def _upsert_entity_unsafe(self, entity: Entity) -> None:
        """Bypass ACL validation to seed test data with specific acl_allow."""
        from depthfusion.graph.store import _entity_to_dict
        self._store._data["entities"][entity.entity_id] = _entity_to_dict(entity)
        self._store._save()

    def _upsert_edge_unsafe(self, edge: Edge) -> None:
        from depthfusion.graph.store import _edge_to_dict
        self._store._data["edges"][edge.edge_id] = _edge_to_dict(edge)
        self._store._save()

    def test_origin_entity_visible_to_authorized_principal(self) -> None:
        origin = _make_entity("e1", "Origin", acl_allow=["alice"])
        self._upsert_entity_unsafe(origin)
        p = _Principal(principal_id="alice")
        result = traverse("e1", self._store, principal=p)
        assert result is not None
        assert result.origin_entity.entity_id == "e1"

    def test_origin_entity_invisible_to_unauthorized_principal(self) -> None:
        origin = _make_entity("e1", "Origin", acl_allow=["alice"])
        self._upsert_entity_unsafe(origin)
        p = _Principal(principal_id="bob")
        result = traverse("e1", self._store, principal=p)
        assert result is None, "bob should not traverse origin ACL-locked to alice"

    def test_neighbor_excluded_when_unauthorized(self) -> None:
        origin = _make_entity("e1", "Origin", acl_allow=["alice"])
        neighbor = _make_entity("e2", "Neighbor", acl_allow=["bob"])
        edge = _make_edge("e1", "e2", acl_allow=["alice"])

        self._upsert_entity_unsafe(origin)
        self._upsert_entity_unsafe(neighbor)
        self._upsert_edge_unsafe(edge)

        p = _Principal(principal_id="alice")
        result = traverse("e1", self._store, principal=p)
        assert result is not None
        neighbor_ids = [e.entity_id for e, _ in result.connected]
        assert "e2" not in neighbor_ids, "alice should NOT see bob's neighbor"

    def test_neighbor_included_when_authorized(self) -> None:
        origin = _make_entity("e1", "Origin", acl_allow=["alice"])
        neighbor = _make_entity("e2", "Neighbor", acl_allow=["alice"])
        edge = _make_edge("e1", "e2", acl_allow=["alice"])

        self._upsert_entity_unsafe(origin)
        self._upsert_entity_unsafe(neighbor)
        self._upsert_edge_unsafe(edge)

        p = _Principal(principal_id="alice")
        result = traverse("e1", self._store, principal=p)
        assert result is not None
        neighbor_ids = [e.entity_id for e, _ in result.connected]
        assert "e2" in neighbor_ids, "alice should see her own neighbor"

    def test_no_acl_on_entity_visible_to_all(self) -> None:
        """Legacy entities without acl_allow should be visible to everyone."""
        origin = _make_entity("e1", "Origin", acl_allow=None)
        neighbor = _make_entity("e2", "Neighbor", acl_allow=None)
        edge = _make_edge("e1", "e2")

        self._upsert_entity_unsafe(origin)
        self._upsert_entity_unsafe(neighbor)
        self._upsert_edge_unsafe(edge)

        p = _Principal(principal_id="carol")
        result = traverse("e1", self._store, principal=p)
        assert result is not None
        neighbor_ids = [e.entity_id for e, _ in result.connected]
        assert "e2" in neighbor_ids

    def test_group_member_sees_group_entity(self) -> None:
        origin = _make_entity("e1", "Origin", acl_allow=["team-alpha"])
        neighbor = _make_entity("e2", "Neighbor", acl_allow=["team-alpha"])
        edge = _make_edge("e1", "e2", acl_allow=["team-alpha"])

        self._upsert_entity_unsafe(origin)
        self._upsert_entity_unsafe(neighbor)
        self._upsert_edge_unsafe(edge)

        p = _Principal(principal_id="dave", groups=["team-alpha"])
        result = traverse("e1", self._store, principal=p)
        assert result is not None
        neighbor_ids = [e.entity_id for e, _ in result.connected]
        assert "e2" in neighbor_ids

    def test_none_principal_sees_all(self) -> None:
        """principal=None means internal/system caller — no ACL filter."""
        origin = _make_entity("e1", "Origin", acl_allow=["alice"])
        neighbor = _make_entity("e2", "Neighbor", acl_allow=["bob"])
        edge = _make_edge("e1", "e2", acl_allow=["alice"])

        self._upsert_entity_unsafe(origin)
        self._upsert_entity_unsafe(neighbor)
        self._upsert_edge_unsafe(edge)

        result = traverse("e1", self._store, principal=None)
        assert result is not None
        neighbor_ids = [e.entity_id for e, _ in result.connected]
        assert "e2" in neighbor_ids
