"""Tests for HNSWStore — E-45 fused-recall vector index.

These tests do NOT require ``hnswlib`` to be installed:
- ``test_capability_disabled_when_env_false`` and ``test_init_without_hnswlib_*``
  exercise the no-dep paths.
- All other tests skip gracefully if hnswlib is unavailable.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from depthfusion.retrieval.hnsw_store import HNSWStore

_HNSWLIB_AVAILABLE = importlib.util.find_spec("hnswlib") is not None
_REQUIRES_HNSWLIB = pytest.mark.skipif(
    not _HNSWLIB_AVAILABLE, reason="hnswlib not installed"
)


def _patch_embedder(store: HNSWStore, vector: list[float] | None) -> None:
    """Force the store's embedder to return *vector* (or None) for every call."""

    class _StubBackend:
        def embed(self, texts):  # noqa: D401 — test stub
            if vector is None:
                return None
            return [vector for _ in texts]

    store._embedder = _StubBackend()  # type: ignore[attr-defined]
    store._embedder_failed = False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Environment / disabled-state tests (do not require hnswlib)
# ---------------------------------------------------------------------------


def test_capability_disabled_when_env_false(tmp_path: Path) -> None:
    """When DEPTHFUSION_HNSW_ENABLED is not set, _get_hnsw_store() returns None
    and the capability tool reports enabled=False."""
    from depthfusion.mcp import server as mcp_server

    with patch.dict(os.environ, {"DEPTHFUSION_HNSW_ENABLED": "false"}, clear=False):
        # Reset module-level singleton so the env change is honoured.
        mcp_server._HNSW_STORE = None
        mcp_server._HNSW_INIT_ATTEMPTED = False
        store = mcp_server._get_hnsw_store()
        assert store is None
        cap = json.loads(mcp_server._tool_hnsw_capability())
        assert cap["enabled"] is False
        assert cap["backend"] == "none"
        assert cap["entry_count"] == 0


def test_init_without_hnswlib_marks_store_not_ready(tmp_path: Path) -> None:
    """If hnswlib import fails, hnsw_ready stays False and methods degrade."""
    index_path = tmp_path / "hnsw.bin"

    with patch.object(HNSWStore, "_hnswlib_available", return_value=False):
        store = HNSWStore(index_path=index_path, model_name="test-model")
        assert store.hnsw_ready is False
        assert store.search("anything", k=5) == []
        assert store.upsert("id1", "content") is False
        # state() and capability() remain callable with sensible defaults.
        st = store.state()
        assert st["entry_count"] == 0
        cap = store.capability()
        assert cap["enabled"] is False
        assert cap["backend"] == "none"


# ---------------------------------------------------------------------------
# Tests requiring hnswlib
# ---------------------------------------------------------------------------


@_REQUIRES_HNSWLIB
def test_fresh_store_has_zero_entries(tmp_path: Path) -> None:
    index_path = tmp_path / "hnsw.bin"
    store = HNSWStore(index_path=index_path, model_name="test-model", dimension=4)
    assert store.hnsw_ready is True
    state = store.state()
    assert state["entry_count"] == 0
    assert state["schema_version"] == 1
    assert state["dimension"] == 4


@_REQUIRES_HNSWLIB
def test_upsert_returns_true_when_backend_available(tmp_path: Path) -> None:
    index_path = tmp_path / "hnsw.bin"
    store = HNSWStore(index_path=index_path, model_name="test-model", dimension=4)
    _patch_embedder(store, [1.0, 0.0, 0.0, 0.0])
    assert store.upsert("disc-1", "some content") is True
    assert store.state()["entry_count"] == 1


@_REQUIRES_HNSWLIB
def test_upsert_returns_false_when_embed_fails(tmp_path: Path) -> None:
    index_path = tmp_path / "hnsw.bin"
    store = HNSWStore(index_path=index_path, model_name="test-model", dimension=4)
    _patch_embedder(store, None)  # embedder returns None
    assert store.upsert("disc-1", "some content") is False
    assert store.state()["entry_count"] == 0


@_REQUIRES_HNSWLIB
def test_search_returns_empty_when_no_entries(tmp_path: Path) -> None:
    index_path = tmp_path / "hnsw.bin"
    store = HNSWStore(index_path=index_path, model_name="test-model", dimension=4)
    _patch_embedder(store, [1.0, 0.0, 0.0, 0.0])
    assert store.search("query", k=5) == []


@_REQUIRES_HNSWLIB
def test_search_returns_results_after_upsert(tmp_path: Path) -> None:
    index_path = tmp_path / "hnsw.bin"
    store = HNSWStore(index_path=index_path, model_name="test-model", dimension=4)
    # Three distinct unit vectors so cosine similarity discriminates them.
    vectors = {
        "disc-a": [1.0, 0.0, 0.0, 0.0],
        "disc-b": [0.0, 1.0, 0.0, 0.0],
        "disc-c": [0.0, 0.0, 1.0, 0.0],
    }

    class _MappedBackend:
        def __init__(self, mapping):
            self.mapping = mapping
            self.last = None

        def embed(self, texts):
            # The store passes one text at a time.
            return [self.mapping.get(texts[0], [0.0, 0.0, 0.0, 1.0])]

    mapping = {
        "content-a": vectors["disc-a"],
        "content-b": vectors["disc-b"],
        "content-c": vectors["disc-c"],
        "query-a": vectors["disc-a"],
    }
    store._embedder = _MappedBackend(mapping)  # type: ignore[attr-defined]
    store._embedder_failed = False  # type: ignore[attr-defined]

    assert store.upsert("disc-a", "content-a") is True
    assert store.upsert("disc-b", "content-b") is True
    assert store.upsert("disc-c", "content-c") is True

    results = store.search("query-a", k=3)
    assert len(results) == 3
    # disc-a should rank first (cosine sim 1.0 to query)
    assert results[0]["discovery_id"] == "disc-a"
    assert results[0]["score"] >= results[1]["score"]


@_REQUIRES_HNSWLIB
def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    index_path = tmp_path / "hnsw.bin"
    store = HNSWStore(index_path=index_path, model_name="test-model", dimension=4)
    _patch_embedder(store, [1.0, 0.0, 0.0, 0.0])
    assert store.upsert("disc-1", "content-1") is True
    assert store.upsert("disc-2", "content-2") is True
    store.save()

    assert index_path.exists()
    assert (tmp_path / "hnsw.bin.labels.json").exists()
    assert (tmp_path / "hnsw.bin.meta.json").exists()

    # Reload into a fresh store.
    store2 = HNSWStore(index_path=index_path, model_name="test-model", dimension=4)
    assert store2.hnsw_ready is True
    assert store2.state()["entry_count"] == 2


@_REQUIRES_HNSWLIB
def test_label_map_roundtrip(tmp_path: Path) -> None:
    index_path = tmp_path / "hnsw.bin"
    store = HNSWStore(index_path=index_path, model_name="test-model", dimension=4)
    _patch_embedder(store, [1.0, 0.0, 0.0, 0.0])
    store.upsert("disc-a", "content-a")
    store.upsert("disc-b", "content-b")
    original_labels = dict(store._label_map)  # type: ignore[attr-defined]
    store.save()

    store2 = HNSWStore(index_path=index_path, model_name="test-model", dimension=4)
    reloaded = store2._label_map  # type: ignore[attr-defined]
    assert reloaded == original_labels
    assert "disc-a" in reloaded
    assert "disc-b" in reloaded


@_REQUIRES_HNSWLIB
def test_upsert_is_idempotent_on_same_id(tmp_path: Path) -> None:
    """Re-upserting the same discovery_id must not bump entry_count."""
    index_path = tmp_path / "hnsw.bin"
    store = HNSWStore(index_path=index_path, model_name="test-model", dimension=4)
    _patch_embedder(store, [1.0, 0.0, 0.0, 0.0])
    assert store.upsert("disc-1", "first") is True
    assert store.upsert("disc-1", "second") is True
    assert store.state()["entry_count"] == 1


@_REQUIRES_HNSWLIB
def test_capability_reflects_ready_state(tmp_path: Path) -> None:
    index_path = tmp_path / "hnsw.bin"
    store = HNSWStore(index_path=index_path, model_name="my-model", dimension=4)
    cap = store.capability()
    assert cap["enabled"] is True
    assert cap["backend"] == "local"
    assert cap["model"] == "my-model"
    assert cap["dimension"] == 4
    assert cap["entry_count"] == 0
