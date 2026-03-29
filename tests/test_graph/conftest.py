"""Shared fixtures for graph tests."""
import pytest
from depthfusion.graph.types import Entity, Edge


@pytest.fixture
def sample_entity() -> Entity:
    return Entity(
        entity_id="abc123456789",
        name="TierManager",
        type="class",
        project="depthfusion",
        source_files=["memory/arch.md"],
        confidence=1.0,
        first_seen="2026-03-28T00:00:00",
        metadata={},
    )


@pytest.fixture
def sample_entity_b() -> Entity:
    return Entity(
        entity_id="def123456789",
        name="RecallPipeline",
        type="class",
        project="depthfusion",
        source_files=["memory/arch.md"],
        confidence=1.0,
        first_seen="2026-03-28T00:00:00",
        metadata={},
    )


@pytest.fixture
def sample_edge(sample_entity, sample_entity_b) -> Edge:
    return Edge(
        edge_id="edge00000001",
        source_id=sample_entity.entity_id,
        target_id=sample_entity_b.entity_id,
        relationship="CO_OCCURS",
        weight=1.0,
        signals=["co_occurrence"],
        metadata={},
    )
