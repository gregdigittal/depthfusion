"""ChromaDB vector store wrapper for DepthFusion Tier 2."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PERSIST_DIR = Path.home() / ".claude" / ".depthfusion_vectors"

try:
    import chromadb
    _CHROMADB_AVAILABLE = True
except (ImportError, Exception):
    _CHROMADB_AVAILABLE = False


def is_chromadb_available() -> bool:
    return _CHROMADB_AVAILABLE


class ChromaDBStore:
    """Persistent ChromaDB vector store. Tier 2 only."""

    def __init__(self, persist_dir: Optional[Path] = None):
        if not _CHROMADB_AVAILABLE:
            raise ImportError(
                "chromadb not installed. Run: pip install 'depthfusion[vps-tier2]'"
            )
        dir_ = persist_dir or _PERSIST_DIR
        dir_.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(dir_))
        self._collection = self._client.get_or_create_collection(
            name="memory_corpus",
            metadata={"hnsw:space": "cosine"},
        )

    def add_document(self, doc_id: str, content: str, metadata: dict) -> None:
        """Add or update a document (upsert)."""
        self._collection.upsert(
            ids=[doc_id],
            documents=[content],
            metadatas=[metadata],
        )

    def query(self, query_text: str, top_k: int = 20) -> list[dict]:
        """Return top_k most similar documents."""
        n = min(top_k, self.count())
        if n == 0:
            return []
        results = self._collection.query(query_texts=[query_text], n_results=n)
        # The Chroma return types for distances/documents/metadatas are
        # `list[...] | None` — narrow once so mypy + runtime both treat
        # the subscripted access below as safe.
        ids = results.get("ids") or []
        distances = results.get("distances") or []
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        if not ids or not ids[0]:
            return []
        output = []
        for i, doc_id in enumerate(ids[0]):
            dist = distances[0][i] if distances and distances[0] else 0.0
            content = documents[0][i] if documents and documents[0] else ""
            metadata = metadatas[0][i] if metadatas and metadatas[0] else {}
            output.append({
                "chunk_id": doc_id,
                "content": content,
                "metadata": metadata,
                "score": max(0.0, 1.0 - dist),  # cosine distance → similarity
            })
        return output

    def count(self) -> int:
        return self._collection.count()
