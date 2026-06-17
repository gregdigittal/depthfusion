# tests/test_graph/test_dedup.py
"""T-620: Entity deduplicator — near-duplicate merging with fail-closed ACL semantics."""
from __future__ import annotations

from depthfusion.graph.dedup import EntityDeduplicator, _acl_intersect, _normalise
from depthfusion.graph.types import Entity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entity(name: str, etype: str = "class", project: str = "p",
             acl: list[str] | None = None, first_seen: str = "2026-01-01T00:00:00",
             confidence: float = 1.0) -> Entity:
    from depthfusion.graph.extractor import make_entity_id
    return Entity(
        entity_id=make_entity_id(name, etype, project),
        name=name,
        type=etype,
        project=project,
        source_files=[f"{name}.md"],
        confidence=confidence,
        first_seen=first_seen,
        metadata={"acl_allow": list(acl)} if acl is not None else {},
    )


# ---------------------------------------------------------------------------
# _normalise helper
# ---------------------------------------------------------------------------

def test_normalise_lowercases():
    assert _normalise("TierManager") == "tiermanager"


def test_normalise_strips_underscores():
    assert _normalise("recall_pipeline") == "recallpipeline"


def test_normalise_strips_spaces():
    assert _normalise("Recall Pipeline") == "recallpipeline"


def test_normalise_camel_and_snake_match():
    assert _normalise("RecallPipeline") == _normalise("recall_pipeline")


def test_normalise_camel_and_spaced_match():
    assert _normalise("RecallPipeline") == _normalise("Recall Pipeline")


# ---------------------------------------------------------------------------
# _acl_intersect helper
# ---------------------------------------------------------------------------

def test_acl_intersect_common_principals():
    a = ["alice", "bob", "carol"]
    b = ["bob", "carol", "dave"]
    assert set(_acl_intersect(a, b)) == {"bob", "carol"}


def test_acl_intersect_no_overlap_returns_empty():
    assert _acl_intersect(["alice"], ["bob"]) == []


def test_acl_intersect_preserves_order_from_a():
    a = ["z", "a", "m"]
    b = ["m", "z"]
    result = _acl_intersect(a, b)
    assert result == ["z", "m"]  # order from a


def test_acl_intersect_empty_a():
    assert _acl_intersect([], ["alice"]) == []


def test_acl_intersect_empty_b():
    assert _acl_intersect(["alice"], []) == []


# ---------------------------------------------------------------------------
# EntityDeduplicator
# ---------------------------------------------------------------------------

class TestEntityDeduplicator:
    """Tests for EntityDeduplicator.deduplicate()."""

    def test_single_entity_no_merge(self):
        dedup = EntityDeduplicator()
        e = _entity("TierManager", acl=["eng"])
        canonical, merged = dedup.deduplicate([e])
        assert len(canonical) == 1
        assert merged == []

    def test_two_distinct_entities_no_merge(self):
        dedup = EntityDeduplicator()
        a = _entity("TierManager", acl=["eng"])
        b = _entity("RecallPipeline", acl=["eng"])
        canonical, merged = dedup.deduplicate([a, b])
        assert len(canonical) == 2
        assert merged == []

    def test_exact_duplicate_merges(self):
        """Two entities with same normalised name+type are merged."""
        dedup = EntityDeduplicator()
        a = _entity("TierManager", acl=["eng"])
        # Same name, different casing → same normalised key
        from depthfusion.graph.extractor import make_entity_id
        b = Entity(
            entity_id=make_entity_id("tier_manager", "class", "p"),
            name="tier_manager",
            type="class",
            project="p",
            source_files=["b.md"],
            confidence=0.9,
            first_seen="2026-02-01T00:00:00",
            metadata={"acl_allow": ["eng"]},
        )
        canonical, merged = dedup.deduplicate([a, b])
        assert len(canonical) == 1
        assert len(merged) == 1

    def test_merge_absorbs_source_files(self):
        """Merged entity has union of source_files from both inputs."""
        dedup = EntityDeduplicator()
        from depthfusion.graph.extractor import make_entity_id
        a = Entity(entity_id=make_entity_id("TierManager", "class", "p"),
                   name="TierManager", type="class", project="p",
                   source_files=["a.md"], confidence=1.0,
                   first_seen="2026-01-01T00:00:00", metadata={"acl_allow": ["eng"]})
        b = Entity(entity_id=make_entity_id("tiermanager", "class", "p"),
                   name="tiermanager", type="class", project="p",
                   source_files=["b.md"], confidence=0.9,
                   first_seen="2026-02-01T00:00:00", metadata={"acl_allow": ["eng"]})
        canonical, _ = dedup.deduplicate([a, b])
        assert "a.md" in canonical[0].source_files
        assert "b.md" in canonical[0].source_files

    def test_merge_confidence_is_recorded(self):
        """Merged canonical entity carries metadata['merge_confidence']."""
        dedup = EntityDeduplicator()
        from depthfusion.graph.extractor import make_entity_id
        a = Entity(entity_id=make_entity_id("TierManager", "class", "p"),
                   name="TierManager", type="class", project="p",
                   source_files=["a.md"], confidence=1.0,
                   first_seen="2026-01-01T00:00:00", metadata={"acl_allow": ["eng"]})
        b = Entity(entity_id=make_entity_id("tiermanager", "class", "p"),
                   name="tiermanager", type="class", project="p",
                   source_files=["b.md"], confidence=0.9,
                   first_seen="2026-02-01T00:00:00", metadata={"acl_allow": ["eng"]})
        canonical, _ = dedup.deduplicate([a, b])
        assert "merge_confidence" in canonical[0].metadata
        assert canonical[0].metadata["merge_confidence"] == 1.0

    # ------------------------------------------------------------------
    # ACL fail-closed semantics
    # ------------------------------------------------------------------

    def test_acl_intersection_on_merge(self):
        """Merged entity acl_allow = intersection of the two (fail-closed)."""
        dedup = EntityDeduplicator()
        from depthfusion.graph.extractor import make_entity_id
        a = Entity(entity_id=make_entity_id("TierManager", "class", "p"),
                   name="TierManager", type="class", project="p",
                   source_files=["a.md"], confidence=1.0,
                   first_seen="2026-01-01T00:00:00",
                   metadata={"acl_allow": ["alice", "bob"]})
        b = Entity(entity_id=make_entity_id("tiermanager", "class", "p"),
                   name="tiermanager", type="class", project="p",
                   source_files=["b.md"], confidence=0.9,
                   first_seen="2026-02-01T00:00:00",
                   metadata={"acl_allow": ["bob", "carol"]})
        canonical, _ = dedup.deduplicate([a, b])
        assert canonical[0].metadata["acl_allow"] == ["bob"]

    def test_acl_empty_intersection_produces_empty(self):
        """Disjoint ACLs → empty acl_allow (fail-closed: no access on merge)."""
        dedup = EntityDeduplicator()
        from depthfusion.graph.extractor import make_entity_id
        a = Entity(entity_id=make_entity_id("TierManager", "class", "p"),
                   name="TierManager", type="class", project="p",
                   source_files=["a.md"], confidence=1.0,
                   first_seen="2026-01-01T00:00:00",
                   metadata={"acl_allow": ["alice"]})
        b = Entity(entity_id=make_entity_id("tiermanager", "class", "p"),
                   name="tiermanager", type="class", project="p",
                   source_files=["b.md"], confidence=0.9,
                   first_seen="2026-02-01T00:00:00",
                   metadata={"acl_allow": ["bob"]})
        canonical, _ = dedup.deduplicate([a, b])
        assert canonical[0].metadata["acl_allow"] == []

    def test_different_types_not_merged(self):
        """Same name but different type → NOT merged (class vs function)."""
        dedup = EntityDeduplicator()
        a = _entity("process", etype="class", acl=["eng"])
        b = _entity("process", etype="function", acl=["eng"])
        canonical, merged = dedup.deduplicate([a, b])
        assert len(canonical) == 2
        assert merged == []

    def test_confidence_max_on_merge(self):
        """Merged entity takes the maximum confidence."""
        dedup = EntityDeduplicator()
        from depthfusion.graph.extractor import make_entity_id
        a = Entity(entity_id=make_entity_id("TierManager", "class", "p"),
                   name="TierManager", type="class", project="p",
                   source_files=["a.md"], confidence=0.7,
                   first_seen="2026-01-01T00:00:00", metadata={"acl_allow": ["eng"]})
        b = Entity(entity_id=make_entity_id("tiermanager", "class", "p"),
                   name="tiermanager", type="class", project="p",
                   source_files=["b.md"], confidence=1.0,
                   first_seen="2026-02-01T00:00:00", metadata={"acl_allow": ["eng"]})
        canonical, _ = dedup.deduplicate([a, b])
        assert canonical[0].confidence == 1.0

    def test_first_seen_earliest_on_merge(self):
        """Merged entity carries the earliest first_seen timestamp."""
        dedup = EntityDeduplicator()
        from depthfusion.graph.extractor import make_entity_id
        a = Entity(entity_id=make_entity_id("TierManager", "class", "p"),
                   name="TierManager", type="class", project="p",
                   source_files=["a.md"], confidence=1.0,
                   first_seen="2026-03-01T00:00:00", metadata={"acl_allow": ["eng"]})
        b = Entity(entity_id=make_entity_id("tiermanager", "class", "p"),
                   name="tiermanager", type="class", project="p",
                   source_files=["b.md"], confidence=0.9,
                   first_seen="2026-01-01T00:00:00", metadata={"acl_allow": ["eng"]})
        canonical, _ = dedup.deduplicate([a, b])
        assert canonical[0].first_seen == "2026-01-01T00:00:00"

    def test_merged_away_ids_are_absorbed(self):
        """entity_ids of absorbed duplicates appear in merged_away list."""
        dedup = EntityDeduplicator()
        from depthfusion.graph.extractor import make_entity_id
        a = Entity(entity_id=make_entity_id("TierManager", "class", "p"),
                   name="TierManager", type="class", project="p",
                   source_files=["a.md"], confidence=1.0,
                   first_seen="2026-01-01T00:00:00", metadata={"acl_allow": ["eng"]})
        b = Entity(entity_id=make_entity_id("tiermanager", "class", "p"),
                   name="tiermanager", type="class", project="p",
                   source_files=["b.md"], confidence=0.9,
                   first_seen="2026-02-01T00:00:00", metadata={"acl_allow": ["eng"]})
        _, merged = dedup.deduplicate([a, b])
        assert b.entity_id in merged

    def test_empty_input(self):
        dedup = EntityDeduplicator()
        canonical, merged = dedup.deduplicate([])
        assert canonical == []
        assert merged == []

    def test_multiple_duplicates_single_canonical(self):
        """Three variants of the same entity → one canonical."""
        dedup = EntityDeduplicator()
        from depthfusion.graph.extractor import make_entity_id
        entities = [
            Entity(entity_id=make_entity_id(n, "class", "p"),
                   name=n, type="class", project="p",
                   source_files=[f"{n}.md"], confidence=1.0,
                   first_seen="2026-01-01T00:00:00",
                   metadata={"acl_allow": ["eng"]})
            for n in ["TierManager", "tier_manager", "Tier Manager"]
        ]
        canonical, merged = dedup.deduplicate(entities)
        assert len(canonical) == 1
        assert len(merged) == 2
