"""T-619 (E-55 / S-177): ACL inheritance propagation end-to-end.

These tests assert the two halves of the ACL-inheritance contract:

1. Inheritance on write — edges produced for document-derived entities carry
   ``metadata["acl_allow"]`` inherited from the source document, and an edge
   written without ``acl_allow`` still raises ``ValueError("acl_allow is
   required")`` via the store's ``_validate_graph_acl``.

2. Principal-trimmed traversal — ``traverse()`` with a Principal whose
   ``allowed_ids`` do not intersect a document edge's ``acl_allow`` excludes
   that edge (and the entity it reaches) from the result. Fail-closed: a
   restricted edge is never traversed even if the neighbour entity would
   otherwise be visible.

The flow under test wires builder/linker output through the ACL-stamping path
(``propagate_acl_from_entities``) so no document edge reaches the store without
an inherited ACL.
"""
from __future__ import annotations

import pytest

from depthfusion.graph.builder import DocumentEntityBuilder
from depthfusion.graph.extractor import make_entity_id
from depthfusion.graph.linker import (
    CoOccurrenceLinker,
    make_edge_id,
    propagate_acl_from_entities,
)
from depthfusion.graph.store import JSONGraphStore, _validate_graph_acl
from depthfusion.graph.traverser import traverse
from depthfusion.graph.types import Edge, Entity
from depthfusion.identity.models import Principal

# A chunk with two CamelCase classes so the regex extractor emits >= 2
# entities and the co-occurrence linker can produce a CO_OCCURS edge.
DOC_CHUNK = "The RecallPipeline calls the TierManager during fusion."
DOC_ACL = ["acme-corp", "engineering"]


def _build_document_subgraph(
    chunk: str, source_file: str, acl_allow: list[str]
) -> tuple[list[Entity], list[Edge]]:
    """Run the document builder + co-occurrence linker, then stamp edge ACLs.

    Mirrors the ingestion wiring: extract entities (each inherits the doc
    ACL), link them, then propagate each edge's ACL from its source entity so
    no edge reaches the store without an inherited acl_allow.
    """
    builder = DocumentEntityBuilder(project="acme")  # regex-only (no LLM key)
    entities = builder.extract(chunk, source_file=source_file, acl_allow=acl_allow)
    edges = CoOccurrenceLinker().link(entities)
    propagate_acl_from_entities(edges, entities)
    return entities, edges


# ---------------------------------------------------------------------------
# 1. Inheritance on write
# ---------------------------------------------------------------------------


def test_document_edges_inherit_source_acl() -> None:
    entities, edges = _build_document_subgraph(
        DOC_CHUNK, source_file="docs/arch.md", acl_allow=DOC_ACL
    )
    assert entities, "regex extractor should emit document entities"
    assert edges, "co-occurrence linker should emit at least one edge"
    for edge in edges:
        assert edge.metadata["acl_allow"] == DOC_ACL


def test_inherited_edge_acl_satisfies_store_validation(tmp_path) -> None:
    _, edges = _build_document_subgraph(
        DOC_CHUNK, source_file="docs/arch.md", acl_allow=DOC_ACL
    )
    store = JSONGraphStore(path=tmp_path / "g.json")
    # No ValueError — every document edge carries an inherited acl_allow.
    for edge in edges:
        store.upsert_edge(edge)
    assert store.edge_count() == len(edges)


def test_edge_without_acl_raises_value_error(tmp_path) -> None:
    """Writing a document edge without an ACL still fails closed (T-562)."""
    store = JSONGraphStore(path=tmp_path / "g.json")
    naked = Edge(
        edge_id="naked0000001",
        source_id="a",
        target_id="b",
        relationship="CO_OCCURS",
        weight=1.0,
        signals=["co_occurrence"],
        metadata={},  # no acl_allow
    )
    with pytest.raises(ValueError, match="acl_allow is required"):
        store.upsert_edge(naked)
    # And the bare validator agrees.
    with pytest.raises(ValueError, match="acl_allow is required"):
        _validate_graph_acl(naked.metadata.get("acl_allow"))


# ---------------------------------------------------------------------------
# 2. Principal-trimmed traversal
# ---------------------------------------------------------------------------


def _seed_document_graph(tmp_path) -> JSONGraphStore:
    """Store two document entities joined by an ACL-stamped document edge."""
    entities, edges = _build_document_subgraph(
        DOC_CHUNK, source_file="docs/arch.md", acl_allow=DOC_ACL
    )
    store = JSONGraphStore(path=tmp_path / "g.json")
    for e in entities:
        store.upsert_entity(e)
    for edge in edges:
        store.upsert_edge(edge)
    return store


def test_allowed_principal_sees_document_edge(tmp_path) -> None:
    store = _seed_document_graph(tmp_path)
    origin = next(iter(store._data["edges"].values()))["source_id"]
    principal = Principal(principal_id="user-1", groups=["engineering"])
    result = traverse(origin, store, depth=1, principal=principal)
    assert result is not None
    assert result.connected, "allowed principal should reach the document neighbour"


def test_disallowed_principal_excludes_document_edge(tmp_path) -> None:
    """Fail-closed: a principal outside the edge ACL sees the edge trimmed.

    The origin entity is broadly visible (shared ACL), so traversal proceeds
    past the origin gate, but the document edge — restricted to DOC_ACL — is
    excluded for a principal whose allowed_ids don't intersect it.
    """
    entities, edges = _build_document_subgraph(
        DOC_CHUNK, source_file="docs/arch.md", acl_allow=DOC_ACL
    )
    store = JSONGraphStore(path=tmp_path / "g.json")
    origin = edges[0].source_id
    for e in entities:
        # Make the origin entity visible to everyone so we isolate the
        # edge-level trim from the entity-level (origin) trim.
        if e.entity_id == origin:
            e.metadata["acl_allow"] = ["acme-corp", "engineering", "marketing"]
        store.upsert_entity(e)
    for edge in edges:
        store.upsert_edge(edge)

    # Principal whose allowed_ids do NOT intersect the edge's DOC_ACL.
    outsider = Principal(principal_id="user-2", groups=["marketing"])
    result = traverse(origin, store, depth=1, principal=outsider)
    assert result is not None  # origin is visible to the outsider
    assert result.connected == []  # but the restricted document edge is trimmed


def test_edge_acl_trim_independent_of_entity_visibility(tmp_path) -> None:
    """An edge ACL stricter than the neighbour entity ACL still hides the edge.

    The neighbour entity is visible to the principal, but the connecting edge
    carries a document ACL the principal cannot satisfy — the edge (and thus
    the traversal hop) must be excluded.
    """
    store = JSONGraphStore(path=tmp_path / "g.json")
    a = Entity(
        entity_id=make_entity_id("A", "class", "p"), name="A", type="class",
        project="p", source_files=[], confidence=1.0,
        first_seen="2026-06-17T00:00:00", metadata={"acl_allow": ["team-x"]},
    )
    # Neighbour entity readable by the principal...
    b = Entity(
        entity_id=make_entity_id("B", "class", "p"), name="B", type="class",
        project="p", source_files=[], confidence=1.0,
        first_seen="2026-06-17T00:00:00", metadata={"acl_allow": ["team-x"]},
    )
    store.upsert_entity(a)
    store.upsert_entity(b)
    # ...but the document edge is restricted to a different ACL.
    store.upsert_edge(Edge(
        edge_id=make_edge_id(a.entity_id, b.entity_id, "CO_OCCURS"),
        source_id=a.entity_id, target_id=b.entity_id,
        relationship="CO_OCCURS", weight=1.0, signals=["co_occurrence"],
        metadata={"acl_allow": ["restricted-only"]},
    ))
    principal = Principal(principal_id="member", groups=["team-x"])
    result = traverse(a.entity_id, store, depth=1, principal=principal)
    assert result is not None
    assert result.connected == [], "restricted edge must be trimmed even when entity is visible"

    # Sanity: a principal inside the edge ACL does traverse it.
    insider = Principal(principal_id="member", groups=["team-x", "restricted-only"])
    insider_result = traverse(a.entity_id, store, depth=1, principal=insider)
    assert insider_result is not None
    assert {e.entity_id for e, _ in insider_result.connected} == {b.entity_id}
