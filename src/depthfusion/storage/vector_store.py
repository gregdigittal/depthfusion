"""ChromaDB vector store wrapper for DepthFusion Tier 2."""
from __future__ import annotations

import json as _json_mod
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from depthfusion.identity.models import Principal, cast

logger = logging.getLogger(__name__)

_PERSIST_DIR = Path.home() / ".claude" / ".depthfusion_vectors"

# Evaluated once at import time — process restart required to pick up env changes.
_ADMISSION_DROP_THRESHOLD: float = float(
    os.environ.get("DEPTHFUSION_ADMISSION_THRESHOLD", "0.10")
)

try:
    import chromadb
    _CHROMADB_AVAILABLE = True
except (ImportError, Exception):
    _CHROMADB_AVAILABLE = False


def is_chromadb_available() -> bool:
    return _CHROMADB_AVAILABLE


def _validate_vector_acl(metadata: dict) -> None:
    """T-562: reject vector writes where acl_allow is missing or empty.

    In ChromaDB, acl_allow is stored as metadata["acl_allow"] (JSON-serialized list
    or plain list). Raises ValueError("acl_allow is required") if absent or empty.
    """
    import json as _json
    raw = metadata.get("acl_allow")
    if raw is None:
        raise ValueError("acl_allow is required")
    # acl_allow may be a JSON string (e.g. '["greg"]') or already a list.
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
        except (ValueError, TypeError):
            parsed = [raw] if raw.strip() else []
        if not parsed:
            raise ValueError("acl_allow is required")
    elif isinstance(raw, list):
        if not raw:
            raise ValueError("acl_allow is required")
    else:
        raise ValueError("acl_allow is required")


def _admission_score(content: str) -> float:
    """Cheap pre-indexing quality gate score in [0.0, 1.0].

    v2: boilerplate_penalty × lexical_richness_penalty. A short session
    envelope (bp=0.2) with low vocabulary diversity (lr≈0.5) yields a
    combined score of ~0.10, right at the default threshold.

    Returns 1.0 for normal, content-rich blocks; lower values for
    envelopes and/or repetitive content.
    """
    from depthfusion.retrieval.hybrid import (  # lazy — avoids circular deps
        boilerplate_penalty,
        lexical_richness_penalty,
    )
    return boilerplate_penalty(content) * lexical_richness_penalty(content)


class ChromaDBStore:
    """Persistent ChromaDB vector store. Tier 2 only."""

    def __init__(self, persist_dir: Optional[Path] = None):
        if not _CHROMADB_AVAILABLE:
            raise ImportError(
                "chromadb not installed. Run: pip install 'depthfusion[vps-gpu]'"
            )
        dir_ = persist_dir or _PERSIST_DIR
        dir_.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(dir_))
        self._collection = self._client.get_or_create_collection(
            name="memory_corpus",
            metadata={"hnsw:space": "cosine"},
        )

    def _get_embedding(self, texts: list[str]) -> list[list[float]] | None:
        """Return embeddings from DepthFusion's embedding backend, or None on failure.

        Uses a lazy import of ``get_backend`` to avoid circular dependencies at
        module load time.  Returns ``None`` when the backend is unavailable,
        returns an empty result, or raises any exception.
        """
        try:
            from depthfusion.backends import get_backend  # lazy — avoids circular deps
            backend = get_backend("embedding")
            embeddings = backend.embed(texts)
            if embeddings:
                return embeddings
            return None
        except Exception:
            logger.warning(
                "DepthFusion embedding backend unavailable; falling back to Chroma auto-embed",
                exc_info=True,
            )
            return None

    def add_document(self, doc_id: str, content: str, metadata: dict) -> None:
        """Add or update a document (upsert).

        Uses the DepthFusion embedding backend when healthy; falls back to
        Chroma's built-in auto-embedding when the backend is unavailable.
        """
        # T-562: enforce ACL stamp before any write.
        _validate_vector_acl(metadata)
        _score = _admission_score(content)
        if _score < _ADMISSION_DROP_THRESHOLD:
            logger.debug(
                "Skipping indexing of %s — admission score %.3f < threshold %.3f",
                doc_id,
                _score,
                _ADMISSION_DROP_THRESHOLD,
            )
            return
        embedding = self._get_embedding([content])
        if embedding is not None:
            self._collection.upsert(
                ids=[doc_id],
                embeddings=cast(Any, [embedding[0]]),
                documents=[content],
                metadatas=[metadata],
            )
        else:
            try:
                _nonempty = int(self.count()) > 0
            except (TypeError, ValueError):
                _nonempty = False
            if _nonempty:
                logger.warning(
                    "Embedding backend unavailable during add_document — falling back to "
                    "Chroma auto-embedding on a non-empty collection; embedding space "
                    "mismatch is possible if the backend was healthy during prior indexing."
                )
            self._collection.upsert(
                ids=[doc_id],
                documents=[content],
                metadatas=[metadata],
            )

    def query(
        self,
        query_text: str,
        top_k: int = 20,
        *,
        principal: "Optional[Principal]" = None,
    ) -> list[dict]:
        """Return top_k most similar documents, filtered by principal ACL.

        T-571/T-572: every retrieval call carries the requesting Principal.
        When principal is not None, only documents whose acl_allow metadata
        includes principal.principal_id or any of principal.groups are
        returned.  When principal is None, no ACL filter is applied (internal
        / system callers only).

        Uses the DepthFusion embedding backend for the query vector when
        healthy; falls back to Chroma's built-in auto-embedding otherwise.
        """
        n = min(top_k, self.count())
        if n == 0:
            return []
        embedding = self._get_embedding([query_text])
        if embedding is not None:
            results = self._collection.query(
                query_embeddings=cast(Any, [embedding[0]]), n_results=n
            )
        else:
            logger.warning(
                "Embedding backend unavailable during query — falling back to Chroma "
                "auto-embedding; results may be inconsistent if documents were indexed "
                "with a different embedding backend."
            )
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

        # Build principal allowed set for ACL filtering.
        allowed_ids: Optional[set[str]] = None
        if principal is not None:
            allowed_ids = {principal.principal_id}
            for g in (principal.groups or []):
                allowed_ids.add(g)

        output = []
        for i, doc_id in enumerate(ids[0]):
            dist = distances[0][i] if distances and distances[0] else 0.0
            content = documents[0][i] if documents and documents[0] else ""
            metadata = metadatas[0][i] if metadatas and metadatas[0] else {}

            # T-572: ACL filter — check acl_allow in document metadata.
            if allowed_ids is not None:
                acl_raw = metadata.get("acl_allow")
                # acl_allow is stored as JSON string or list in ChromaDB.
                if isinstance(acl_raw, str):
                    try:
                        acl_list: list[str] = _json_mod.loads(acl_raw)
                    except (ValueError, TypeError):
                        acl_list = [acl_raw] if acl_raw.strip() else []
                elif isinstance(acl_raw, list):
                    acl_list = acl_raw
                else:
                    acl_list = []
                if not (set(acl_list) & allowed_ids):
                    continue  # not authorized — skip this document

            output.append({
                "chunk_id": doc_id,
                "content": content,
                "metadata": metadata,
                "score": max(0.0, 1.0 - dist),  # cosine distance → similarity
            })
        return output

    def count(self) -> int:
        return self._collection.count()
