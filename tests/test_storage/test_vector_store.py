# tests/test_storage/test_vector_store.py
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.storage.vector_store import ChromaDBStore, is_chromadb_available


def test_is_chromadb_available_returns_bool():
    result = is_chromadb_available()
    assert isinstance(result, bool)


@pytest.mark.skipif(not is_chromadb_available(), reason="chromadb not installed")
def test_chromadb_store_add_and_query(tmp_path):
    store = ChromaDBStore(persist_dir=tmp_path / "vectors")
    store.add_document("doc1", "VPS server SSH configuration", {"source": "memory"})
    store.add_document("doc2", "cooking pasta recipe", {"source": "memory"})
    results = store.query("VPS server", top_k=1)
    assert len(results) == 1
    assert results[0]["chunk_id"] == "doc1"


@pytest.mark.skipif(not is_chromadb_available(), reason="chromadb not installed")
def test_chromadb_store_upsert_idempotent(tmp_path):
    store = ChromaDBStore(persist_dir=tmp_path / "vectors")
    store.add_document("doc1", "original content", {"source": "memory"})
    store.add_document("doc1", "updated content", {"source": "memory"})
    assert store.count() == 1  # upsert, not duplicate


def test_chromadb_store_unavailable_raises_import_error(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "chromadb", None)
    from importlib import reload

    import depthfusion.storage.vector_store as vs
    reload(vs)
    if not vs.is_chromadb_available():
        with pytest.raises(ImportError, match="chromadb"):
            vs.ChromaDBStore()
    monkeypatch.delitem(sys.modules, "chromadb")
    reload(vs)


# ---------------------------------------------------------------------------
# Helpers for mocking ChromaDB without requiring it to be installed
# ---------------------------------------------------------------------------

def _make_mock_store() -> ChromaDBStore:
    """Return a ChromaDBStore whose internal chromadb objects are fully mocked."""
    mock_collection = MagicMock()
    mock_client = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_collection

    with (
        patch("depthfusion.storage.vector_store._CHROMADB_AVAILABLE", True),
        patch("depthfusion.storage.vector_store.chromadb") as mock_chromadb,
    ):
        mock_chromadb.PersistentClient.return_value = mock_client
        # Path.mkdir called inside __init__ — patch it out
        with patch("pathlib.Path.mkdir"):
            store = ChromaDBStore.__new__(ChromaDBStore)
            store._client = mock_client
            store._collection = mock_collection

    return store


# ---------------------------------------------------------------------------
# S-89: embedding backend integration tests
# ---------------------------------------------------------------------------

class TestGetEmbedding:
    """Unit tests for ChromaDBStore._get_embedding."""

    def test_returns_embeddings_when_backend_healthy(self):
        store = _make_mock_store()
        fake_embeddings = [[0.1, 0.2, 0.3]]

        mock_backend = MagicMock()
        mock_backend.embed.return_value = fake_embeddings
        mock_get_backend = MagicMock(return_value=mock_backend)

        with patch("depthfusion.backends.get_backend", mock_get_backend):
            result = store._get_embedding(["hello world"])

        assert result == fake_embeddings
        mock_get_backend.assert_called_once_with("embedding")
        mock_backend.embed.assert_called_once_with(["hello world"])

    def test_returns_none_when_backend_returns_none(self):
        store = _make_mock_store()

        mock_backend = MagicMock()
        mock_backend.embed.return_value = None

        with patch("depthfusion.backends.get_backend", return_value=mock_backend):
            result = store._get_embedding(["hello world"])

        assert result is None

    def test_returns_none_when_backend_returns_empty_list(self):
        store = _make_mock_store()

        mock_backend = MagicMock()
        mock_backend.embed.return_value = []

        with patch("depthfusion.backends.get_backend", return_value=mock_backend):
            result = store._get_embedding(["hello world"])

        assert result is None

    def test_returns_none_and_logs_warning_on_exception(self, caplog):
        import logging
        store = _make_mock_store()

        mock_backend = MagicMock()
        mock_backend.embed.side_effect = RuntimeError("backend exploded")

        with patch("depthfusion.backends.get_backend", return_value=mock_backend):
            with caplog.at_level(logging.WARNING, logger="depthfusion.storage.vector_store"):
                result = store._get_embedding(["hello world"])

        assert result is None
        assert any("embedding backend unavailable" in r.message for r in caplog.records)


class TestAddDocumentEmbeddingBackend:
    """Tests that add_document uses embeddings= when the backend is healthy."""

    def test_uses_embeddings_param_when_backend_returns_embeddings(self):
        store = _make_mock_store()
        fake_vec = [0.1, 0.2, 0.3]

        mock_backend = MagicMock()
        mock_backend.embed.return_value = [fake_vec]

        with patch("depthfusion.backends.get_backend", return_value=mock_backend):
            store.add_document("doc1", "some content", {"k": "v"})

        call_kwargs = store._collection.upsert.call_args.kwargs
        assert call_kwargs["embeddings"] == [fake_vec]
        assert call_kwargs["documents"] == ["some content"]
        assert call_kwargs["ids"] == ["doc1"]
        assert call_kwargs["metadatas"] == [{"k": "v"}]

    def test_falls_back_to_documents_when_backend_returns_none(self):
        store = _make_mock_store()

        mock_backend = MagicMock()
        mock_backend.embed.return_value = None

        with patch("depthfusion.backends.get_backend", return_value=mock_backend):
            store.add_document("doc1", "some content", {"k": "v"})

        call_kwargs = store._collection.upsert.call_args.kwargs
        # Must NOT have embeddings= key (or it's absent)
        assert "embeddings" not in call_kwargs
        assert call_kwargs["documents"] == ["some content"]

    def test_falls_back_gracefully_when_backend_raises(self):
        store = _make_mock_store()

        mock_backend = MagicMock()
        mock_backend.embed.side_effect = Exception("network error")

        with patch("depthfusion.backends.get_backend", return_value=mock_backend):
            store.add_document("doc1", "some content", {"k": "v"})

        call_kwargs = store._collection.upsert.call_args.kwargs
        assert "embeddings" not in call_kwargs
        assert call_kwargs["documents"] == ["some content"]


class TestQueryEmbeddingBackend:
    """Tests that query() uses query_embeddings= when the backend is healthy."""

    def _setup_query_results(self, store: ChromaDBStore) -> None:
        """Configure the mock collection to return a minimal valid query response."""
        store._collection.count.return_value = 1
        store._collection.query.return_value = {
            "ids": [["doc1"]],
            "distances": [[0.1]],
            "documents": [["content here"]],
            "metadatas": [[{"src": "test"}]],
        }

    def test_uses_query_embeddings_when_backend_returns_embeddings(self):
        store = _make_mock_store()
        self._setup_query_results(store)
        fake_vec = [0.4, 0.5, 0.6]

        mock_backend = MagicMock()
        mock_backend.embed.return_value = [fake_vec]

        with patch("depthfusion.backends.get_backend", return_value=mock_backend):
            results = store.query("find me something", top_k=5)

        call_kwargs = store._collection.query.call_args.kwargs
        assert call_kwargs["query_embeddings"] == [fake_vec]
        assert "query_texts" not in call_kwargs
        assert len(results) == 1
        assert results[0]["chunk_id"] == "doc1"

    def test_falls_back_to_query_texts_when_backend_returns_none(self):
        store = _make_mock_store()
        self._setup_query_results(store)

        mock_backend = MagicMock()
        mock_backend.embed.return_value = None

        with patch("depthfusion.backends.get_backend", return_value=mock_backend):
            results = store.query("find me something", top_k=5)

        call_kwargs = store._collection.query.call_args.kwargs
        assert call_kwargs["query_texts"] == ["find me something"]
        assert "query_embeddings" not in call_kwargs
        assert len(results) == 1
