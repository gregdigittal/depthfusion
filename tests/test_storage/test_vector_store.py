# tests/test_storage/test_vector_store.py
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
