# tests/test_graph/test_acl_inheritance.py
"""T-619: ACL inheritance — entities and edges inherit source document ACL.

Verifies:
1. DocumentEntityBuilder stamps acl_allow on every emitted entity.
2. The traverser ACL-trims results (existing behaviour, exercised here
   against entity metadata set by the builder).
3. Fail-open legacy entities (no acl_allow) remain visible to everyone.
"""
from __future__ import annotations

import pytest

from depthfusion.backends.null import NullBackend
from depthfusion.graph.builder import DocumentEntityBuilder
from depthfusion.graph.store import JSONGraphStore
from depthfusion.graph.traverser import traverse
from depthfusion.graph.types import Entity, Edge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def doc_acl():
    return ["acme-corp", "engineering"]


@pytest.fixture
def builder(doc_acl):
    return DocumentEntityBuilder(project="acme", haiku_backend=NullBackend())


@pytest.fixture
def extracted_entities(builder, doc_acl):
    return builder.extract(
        chunk_text="TierManager and RecallPipeline are core components.",
        source_file="docs/architecture.md",
        acl_allow=doc_acl,
    )


# ---------------------------------------------------------------------------
# T-619: ACL propagation into entity metadata
# ---------------------------------------------------------------------------

def test_entities_carry_acl_allow(extracted_entities, doc_acl):
    """Every extracted entity must have metadata['acl_allow'] == doc_acl."""
    assert len(extracted_entities) >= 1
    for entity in extracted_entities:
        assert "acl_allow" in entity.metadata, (
            f"Entity {entity.name!r} missing acl_allow"
        )
        assert entity.metadata["acl_allow"] == doc_acl, (
            f"Entity {entity.name!r}: expected {doc_acl}, "
            f"got {entity.metadata['acl_allow']}"
        )


def test_acl_allow_propagates_project_default_when_none():
    """When acl_allow=None, project slug is used as default ACL."""
    builder = DocumentEntityBuilder(project="myproject", haiku_backend=NullBackend())
    entities = builder.extract(
        chunk_text="TierManager is used here.",
        source_file="f.md",
        acl_allow=None,
    )
    for entity in entities:
        assert entity.metadata.get("acl_allow") == ["myproject"]


def test_acl_allow_is_a_list(extracted_entities, doc_acl):
    """acl_allow must be a list, not a tuple or set."""
    for entity in extracted_entities:
        assert isinstance(entity.metadata["acl_allow"], list)


def test_acl_allow_custom_principals():
    """Arbitrary principal IDs are preserved exactly."""
    acl = ["user-123", "group-admins", "service-account-bi"]
    builder = DocumentEntityBuilder(project="test", haiku_backend=NullBackend())
    entities = builder.extract(
        chunk_text="GraphStore indexes HaikuExtractor output.",
        source_file="test.md",
        acl_allow=acl,
    )
    for entity in entities:
        assert entity.metadata["acl_allow"] == acl


# ---------------------------------------------------------------------------
# T-619: Traversal is ACL-trimmed
# ---------------------------------------------------------------------------

class _MockPrincipal:
    """Minimal principal stub for traverser ACL checks."""
    def __init__(self, principal_id: str, groups: list[str] | None = None):
        self.principal_id = principal_id
        self.groups = groups or []


@pytest.fixture
def in_memory_store(tmp_path):
    path = tmp_path / "graph.json"
    return JSONGraphStore(path=path)


def _make_entity(name: str, etype: str, project: str, acl: list[str]) -> Entity:
    from depthfusion.graph.extractor import make_entity_id
    return Entity(
        entity_id=make_entity_id(name, etype, project),
        name=name,
        type=etype,
        project=project,
        source_files=["f.md"],
        confidence=1.0,
        first_seen="2026-01-01T00:00:00",
        metadata={"acl_allow": acl},
    )


def _make_edge(src: Entity, tgt: Entity, acl: list[str]) -> Edge:
    from depthfusion.graph.linker import make_edge_id
    return Edge(
        edge_id=make_edge_id(src.entity_id, tgt.entity_id, "CO_OCCURS"),
        source_id=src.entity_id,
        target_id=tgt.entity_id,
        relationship="CO_OCCURS",
        weight=1.0,
        signals=["co_occurrence"],
        metadata={"acl_allow": acl},
    )


def test_traversal_includes_entity_visible_to_principal(in_memory_store):
    """Authorised principal can traverse to ACL-matching entities."""
    acl = ["engineering"]
    origin = _make_entity("GraphStore", "class", "p", acl)
    neighbor = _make_entity("TierManager", "class", "p", acl)
    edge = _make_edge(origin, neighbor, acl)

    in_memory_store.upsert_entity(origin)
    in_memory_store.upsert_entity(neighbor)
    in_memory_store.upsert_edge(edge)

    principal = _MockPrincipal("u1", groups=["engineering"])
    result = traverse(origin.entity_id, in_memory_store, principal=principal)

    assert result is not None
    connected_ids = {e.entity_id for e, _ in result.connected}
    assert neighbor.entity_id in connected_ids


def test_traversal_excludes_entity_not_visible_to_principal(in_memory_store):
    """Principal without matching ACL cannot reach restricted neighbor."""
    origin = _make_entity("GraphStore", "class", "p", ["engineering"])
    restricted = _make_entity("SecretModule", "class", "p", ["admins-only"])
    edge = _make_edge(origin, restricted, ["admins-only"])

    in_memory_store.upsert_entity(origin)
    in_memory_store.upsert_entity(restricted)
    in_memory_store.upsert_edge(edge)

    principal = _MockPrincipal("u1", groups=["engineering"])
    result = traverse(origin.entity_id, in_memory_store, principal=principal)

    assert result is not None
    connected_ids = {e.entity_id for e, _ in result.connected}
    assert restricted.entity_id not in connected_ids


def test_traversal_origin_blocked_when_acl_mismatch(in_memory_store):
    """Principal without access to origin entity gets None from traverse()."""
    origin = _make_entity("RestrictedNode", "class", "p", ["admins-only"])
    in_memory_store.upsert_entity(origin)

    principal = _MockPrincipal("u1", groups=["engineering"])
    result = traverse(origin.entity_id, in_memory_store, principal=principal)
    assert result is None


def test_traversal_no_principal_returns_all_entities(in_memory_store):
    """Without a principal, all entities are returned (legacy behaviour)."""
    origin = _make_entity("GraphStore", "class", "p", ["private-group"])
    neighbor = _make_entity("TierManager", "class", "p", ["other-group"])
    edge = _make_edge(origin, neighbor, ["any"])

    in_memory_store.upsert_entity(origin)
    in_memory_store.upsert_entity(neighbor)
    in_memory_store.upsert_edge(edge)

    result = traverse(origin.entity_id, in_memory_store, principal=None)
    assert result is not None
    connected_ids = {e.entity_id for e, _ in result.connected}
    assert neighbor.entity_id in connected_ids


def test_legacy_entity_no_acl_visible_to_all():
    """Legacy entities with no acl_allow stamp are visible to everyone.

    The traverser's _graph_entity_allowed returns True when acl_allow is absent
    (None). This test exercises the traverser function directly, bypassing the
    store's write-side ACL validation (which correctly requires acl_allow on
    all new writes — T-562). The legacy-visibility rule is a read-time concern.
    """
    from depthfusion.graph.traverser import _graph_entity_allowed
    from depthfusion.graph.extractor import make_entity_id
    legacy = Entity(
        entity_id=make_entity_id("LegacyNode", "class", "p"),
        name="LegacyNode",
        type="class",
        project="p",
        source_files=["f.md"],
        confidence=1.0,
        first_seen="2026-01-01T00:00:00",
        metadata={},  # no acl_allow
    )
    allowed_ids = {"u1", "engineering"}
    # Entity with no acl_allow should be visible to any principal
    assert _graph_entity_allowed(legacy, allowed_ids) is True


def test_entity_with_empty_acl_not_visible():
    """Entity with explicit empty acl_allow is visible to no-one (fail-closed)."""
    from depthfusion.graph.traverser import _graph_entity_allowed
    from depthfusion.graph.extractor import make_entity_id
    entity = Entity(
        entity_id=make_entity_id("RestrictedNode", "class", "p"),
        name="RestrictedNode",
        type="class",
        project="p",
        source_files=["f.md"],
        confidence=1.0,
        first_seen="2026-01-01T00:00:00",
        metadata={"acl_allow": []},  # explicit empty → no access
    )
    allowed_ids = {"u1", "engineering"}
    assert _graph_entity_allowed(entity, allowed_ids) is False
