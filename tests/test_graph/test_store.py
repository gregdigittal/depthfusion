"""Tests for graph store backends."""
import pytest
from pathlib import Path

from depthfusion.graph.store import JSONGraphStore
from depthfusion.graph.types import Entity, Edge


@pytest.fixture
def json_store(tmp_path):
    return JSONGraphStore(path=tmp_path / "graph.json")


def test_upsert_and_get_entity(json_store, sample_entity):
    json_store.upsert_entity(sample_entity)
    result = json_store.get_entity(sample_entity.entity_id)
    assert result is not None
    assert result.name == "TierManager"


def test_get_missing_entity_returns_none(json_store):
    assert json_store.get_entity("nonexistent") is None


def test_upsert_entity_twice_updates(json_store, sample_entity):
    json_store.upsert_entity(sample_entity)
    updated = Entity(
        entity_id=sample_entity.entity_id,
        name=sample_entity.name,
        type=sample_entity.type,
        project=sample_entity.project,
        source_files=["memory/new.md"],
        confidence=0.95,
        first_seen=sample_entity.first_seen,
        metadata={},
    )
    json_store.upsert_entity(updated)
    result = json_store.get_entity(sample_entity.entity_id)
    assert result.confidence == 0.95


def test_upsert_and_get_edge(json_store, sample_entity, sample_entity_b, sample_edge):
    json_store.upsert_entity(sample_entity)
    json_store.upsert_entity(sample_entity_b)
    json_store.upsert_edge(sample_edge)
    edges = json_store.get_edges(sample_entity.entity_id)
    assert len(edges) == 1
    assert edges[0].relationship == "CO_OCCURS"


def test_all_entities_empty(json_store):
    assert json_store.all_entities() == []


def test_all_entities_returns_all(json_store, sample_entity, sample_entity_b):
    json_store.upsert_entity(sample_entity)
    json_store.upsert_entity(sample_entity_b)
    entities = json_store.all_entities()
    assert len(entities) == 2


def test_json_persists_to_disk(tmp_path, sample_entity):
    path = tmp_path / "graph.json"
    store1 = JSONGraphStore(path=path)
    store1.upsert_entity(sample_entity)
    store2 = JSONGraphStore(path=path)
    assert store2.get_entity(sample_entity.entity_id) is not None


def test_get_edges_by_source(json_store, sample_entity, sample_entity_b, sample_edge):
    json_store.upsert_entity(sample_entity)
    json_store.upsert_entity(sample_entity_b)
    json_store.upsert_edge(sample_edge)
    edges = json_store.get_edges(sample_entity.entity_id)
    assert any(e.relationship == "CO_OCCURS" for e in edges)


def test_node_count(json_store, sample_entity, sample_entity_b):
    json_store.upsert_entity(sample_entity)
    json_store.upsert_entity(sample_entity_b)
    assert json_store.node_count() == 2


def test_edge_count(json_store, sample_entity, sample_entity_b, sample_edge):
    json_store.upsert_entity(sample_entity)
    json_store.upsert_entity(sample_entity_b)
    json_store.upsert_edge(sample_edge)
    assert json_store.edge_count() == 1
