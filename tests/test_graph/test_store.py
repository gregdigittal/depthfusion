"""Tests for graph store backends."""
import pytest

from depthfusion.graph.store import JSONGraphStore, SQLiteGraphStore, get_store
from depthfusion.graph.types import Entity


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


@pytest.fixture
def sqlite_store(tmp_path):
    return SQLiteGraphStore(path=tmp_path / "graph.db")


def test_sqlite_upsert_and_get_entity(sqlite_store, sample_entity):
    sqlite_store.upsert_entity(sample_entity)
    result = sqlite_store.get_entity(sample_entity.entity_id)
    assert result is not None
    assert result.name == "TierManager"


def test_sqlite_get_missing_entity_returns_none(sqlite_store):
    assert sqlite_store.get_entity("missing") is None


def test_sqlite_upsert_edge_and_get(sqlite_store, sample_entity, sample_entity_b, sample_edge):
    sqlite_store.upsert_entity(sample_entity)
    sqlite_store.upsert_entity(sample_entity_b)
    sqlite_store.upsert_edge(sample_edge)
    edges = sqlite_store.get_edges(sample_entity.entity_id)
    assert any(e.relationship == "CO_OCCURS" for e in edges)


def test_sqlite_all_entities(sqlite_store, sample_entity, sample_entity_b):
    sqlite_store.upsert_entity(sample_entity)
    sqlite_store.upsert_entity(sample_entity_b)
    assert len(sqlite_store.all_entities()) == 2


def test_sqlite_node_and_edge_count(sqlite_store, sample_entity, sample_entity_b, sample_edge):
    sqlite_store.upsert_entity(sample_entity)
    sqlite_store.upsert_entity(sample_entity_b)
    sqlite_store.upsert_edge(sample_edge)
    assert sqlite_store.node_count() == 2
    assert sqlite_store.edge_count() == 1


def test_sqlite_relationship_filter(sqlite_store, sample_entity, sample_entity_b, sample_edge):
    sqlite_store.upsert_entity(sample_entity)
    sqlite_store.upsert_entity(sample_entity_b)
    sqlite_store.upsert_edge(sample_edge)
    edges = sqlite_store.get_edges(
        sample_entity.entity_id, relationship_filter=["CO_OCCURS"]
    )
    assert len(edges) == 1
    edges_empty = sqlite_store.get_edges(
        sample_entity.entity_id, relationship_filter=["DEPENDS_ON"]
    )
    assert edges_empty == []


def test_get_store_returns_json_in_local_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    store = get_store(graph_json_path=tmp_path / "g.json")
    assert isinstance(store, JSONGraphStore)


def test_get_store_returns_sqlite_in_vps_tier1(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    store = get_store(
        graph_db_path=tmp_path / "g.db",
        corpus_size=10,
    )
    assert isinstance(store, SQLiteGraphStore)


# ---- Confidence threshold tests ----

def _low_confidence_entity(base: "Entity") -> "Entity":
    """Return a copy of base with confidence below the default 0.7 threshold."""
    from depthfusion.graph.types import Entity
    return Entity(
        entity_id=base.entity_id,
        name=base.name,
        type=base.type,
        project=base.project,
        source_files=base.source_files,
        confidence=0.5,
        first_seen=base.first_seen,
        metadata=base.metadata,
    )


def _entity_with_confidence(base: "Entity", confidence: float) -> "Entity":
    from depthfusion.graph.types import Entity
    return Entity(
        entity_id=base.entity_id,
        name=base.name,
        type=base.type,
        project=base.project,
        source_files=base.source_files,
        confidence=confidence,
        first_seen=base.first_seen,
        metadata=base.metadata,
    )


class TestJsonStoreConfidenceThreshold:
    def test_low_confidence_entity_not_stored(self, tmp_path, sample_entity, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_GRAPH_MIN_CONFIDENCE", raising=False)
        store = JSONGraphStore(path=tmp_path / "graph.json")
        low = _low_confidence_entity(sample_entity)
        store.upsert_entity(low)
        assert store.get_entity(low.entity_id) is None
        assert store.node_count() == 0

    def test_entity_at_threshold_is_stored(self, tmp_path, sample_entity, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_GRAPH_MIN_CONFIDENCE", raising=False)
        store = JSONGraphStore(path=tmp_path / "graph.json")
        at_threshold = _entity_with_confidence(sample_entity, 0.7)
        store.upsert_entity(at_threshold)
        assert store.get_entity(at_threshold.entity_id) is not None
        assert store.node_count() == 1

    def test_entity_above_threshold_is_stored(self, tmp_path, sample_entity, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_GRAPH_MIN_CONFIDENCE", raising=False)
        store = JSONGraphStore(path=tmp_path / "graph.json")
        high = _entity_with_confidence(sample_entity, 0.9)
        store.upsert_entity(high)
        assert store.get_entity(high.entity_id) is not None

    def test_env_var_lowers_threshold(self, tmp_path, sample_entity, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_GRAPH_MIN_CONFIDENCE", "0.5")
        store = JSONGraphStore(path=tmp_path / "graph.json")
        # confidence=0.5 is below default 0.7 but at custom threshold
        entity = _entity_with_confidence(sample_entity, 0.5)
        store.upsert_entity(entity)
        assert store.get_entity(entity.entity_id) is not None

    def test_invalid_env_var_falls_back_to_default(self, tmp_path, sample_entity, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_GRAPH_MIN_CONFIDENCE", "not-a-float")
        store = JSONGraphStore(path=tmp_path / "graph.json")
        # confidence=0.5 is below the fallback default of 0.7
        low = _low_confidence_entity(sample_entity)
        store.upsert_entity(low)
        assert store.get_entity(low.entity_id) is None


class TestSQLiteStoreConfidenceThreshold:
    def test_low_confidence_entity_not_stored(self, tmp_path, sample_entity, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_GRAPH_MIN_CONFIDENCE", raising=False)
        store = SQLiteGraphStore(path=tmp_path / "graph.db")
        low = _low_confidence_entity(sample_entity)
        store.upsert_entity(low)
        assert store.get_entity(low.entity_id) is None
        assert store.node_count() == 0

    def test_entity_at_threshold_is_stored(self, tmp_path, sample_entity, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_GRAPH_MIN_CONFIDENCE", raising=False)
        store = SQLiteGraphStore(path=tmp_path / "graph.db")
        at_threshold = _entity_with_confidence(sample_entity, 0.7)
        store.upsert_entity(at_threshold)
        assert store.get_entity(at_threshold.entity_id) is not None
        assert store.node_count() == 1

    def test_entity_above_threshold_is_stored(self, tmp_path, sample_entity, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_GRAPH_MIN_CONFIDENCE", raising=False)
        store = SQLiteGraphStore(path=tmp_path / "graph.db")
        high = _entity_with_confidence(sample_entity, 0.9)
        store.upsert_entity(high)
        assert store.get_entity(high.entity_id) is not None

    def test_env_var_lowers_threshold(self, tmp_path, sample_entity, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_GRAPH_MIN_CONFIDENCE", "0.5")
        store = SQLiteGraphStore(path=tmp_path / "graph.db")
        entity = _entity_with_confidence(sample_entity, 0.5)
        store.upsert_entity(entity)
        assert store.get_entity(entity.entity_id) is not None

    def test_invalid_env_var_falls_back_to_default(self, tmp_path, sample_entity, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_GRAPH_MIN_CONFIDENCE", "not-a-float")
        store = SQLiteGraphStore(path=tmp_path / "graph.db")
        low = _low_confidence_entity(sample_entity)
        store.upsert_entity(low)
        assert store.get_entity(low.entity_id) is None
