"""HNSWStore — hnswlib-backed vector index for DepthFusion fused recall.

E-45 (S-130-ish): persisted HNSW vector index that lets the agent-ops bridge
fuse BM25 lexical recall with dense-vector semantic recall.

Design notes
============
- **Pure-Python wrapper.** Uses the upstream ``hnswlib`` Python package
  (not ``hnswlib-node``). If the package is unavailable, every public
  method degrades gracefully — the store reports ``hnsw_ready=False``
  and search/upsert return empty/false rather than raising.

- **Lazy embedding backend.** ``LocalEmbeddingBackend`` is imported at
  call time, not module-import time, to avoid pulling
  ``sentence_transformers`` (~200MB optional dep) into every MCP-server
  import.

- **Atomic on-disk persistence.** ``save()`` writes to ``<path>.tmp``
  files then ``os.replace()``-s them into place so a kill mid-write
  leaves the previous index intact. Three artefacts live alongside each
  other:

  ::

      hnsw.bin         # the hnswlib index itself
      hnsw.bin.labels.json  # discovery_id -> integer label map
      hnsw.bin.meta.json    # HNSWState (schema_version, embedding model, ...)

- **Auto-save cadence.** The store flushes every 100 successful upserts
  (``_AUTO_SAVE_INTERVAL``). On graceful shutdown the MCP server also
  calls ``save()`` explicitly — auto-save is best-effort, not
  exactly-once.

- **Label allocation.** New discoveries get ``label = current_entry_count``;
  existing ones reuse their label so the index stays single-vector-per-id.
  ``hnswlib.Index.mark_deleted()`` is not used — we simply overwrite on
  re-upsert.

- **State shapes** match the TypeScript contract used by the agent-ops
  bridge (see ``docs/ruflo-mod.md``):

  * ``HNSWState``   — sidecar ``.meta.json`` payload
  * ``HNSWCapability`` — startup ping reply (also returned by the
    ``depthfusion_hnsw_capability`` tool)

Spec: docs/ruflo-mod.md, E-45
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from depthfusion.identity.models import Principal

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_DEFAULT_DIMENSION = 384
_DEFAULT_MAX_ELEMENTS = 50_000
_AUTO_SAVE_INTERVAL = 100


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: bytes) -> None:
    """Write *payload* to *path* atomically using a tmp+os.replace dance."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "wb") as fh:
        fh.write(payload)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


class HNSWStore:
    """hnswlib-backed vector index with discovery-id keying.

    Public surface:
        embed(text)             -> list[float] | None
        upsert(discovery_id, c) -> bool
        search(query, k)        -> list[dict]
        save()                  -> None
        state()                 -> dict (HNSWState shape)
        capability()            -> dict (HNSWCapability shape)
    """

    def __init__(
        self,
        index_path: Path | str,
        model_name: str,
        dimension: int = _DEFAULT_DIMENSION,
        max_elements: int = _DEFAULT_MAX_ELEMENTS,
    ) -> None:
        self._index_path = Path(index_path).expanduser()
        self._labels_path = self._index_path.with_name(self._index_path.name + ".labels.json")
        self._meta_path = self._index_path.with_name(self._index_path.name + ".meta.json")
        self._model_name = model_name
        self._dimension = int(dimension)
        self._max_elements = int(max_elements)

        self._index: Any = None  # hnswlib.Index | None
        self._label_map: dict[str, int] = {}  # discovery_id -> label
        self._next_label: int = 0
        self._last_updated: str = _now_iso()
        self._upserts_since_save: int = 0
        self._lock = threading.Lock()

        # Lazy embedding backend — built on first embed() call.
        self._embedder: Any = None
        self._embedder_failed: bool = False

        self.hnsw_ready: bool = self._init_index()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _hnswlib_available(self) -> bool:
        try:
            return importlib.util.find_spec("hnswlib") is not None
        except (ImportError, ValueError):
            return False

    def _init_index(self) -> bool:
        """Construct or load the hnswlib index. Return True on success."""
        if not self._hnswlib_available():
            logger.info(
                "[hnsw] hnswlib not installed — HNSWStore inactive (degrade to BM25-only)."
            )
            return False

        try:
            import hnswlib  # type: ignore
        except Exception as exc:  # pragma: no cover — defensive
            logger.info("[hnsw] hnswlib import failed: %s", exc)
            return False

        try:
            self._index = hnswlib.Index(space="cosine", dim=self._dimension)
        except Exception as exc:
            logger.warning("[hnsw] failed to construct Index: %s", exc)
            self._index = None
            return False

        if self._index_path.exists() and self._labels_path.exists():
            # Try to load existing index + sidecars.
            try:
                self._index.load_index(str(self._index_path), max_elements=self._max_elements)
                self._index.set_ef(64)
                self._label_map = self._load_labels()
                self._next_label = (
                    max(self._label_map.values()) + 1 if self._label_map else 0
                )
                meta = self._load_meta()
                self._last_updated = meta.get("last_updated", _now_iso())
                logger.info(
                    "[hnsw] loaded existing index (entries=%d, dim=%d) from %s",
                    len(self._label_map),
                    self._dimension,
                    self._index_path,
                )
                return True
            except Exception as exc:
                logger.warning(
                    "[hnsw] failed to load index at %s (%s) — re-initialising fresh.",
                    self._index_path,
                    exc,
                )

        # Fresh init.
        try:
            self._index.init_index(
                max_elements=self._max_elements, ef_construction=200, M=16
            )
            self._index.set_ef(64)
            self._label_map = {}
            self._next_label = 0
            self._last_updated = _now_iso()
            logger.info(
                "[hnsw] initialised fresh index (dim=%d, max_elements=%d)",
                self._dimension,
                self._max_elements,
            )
            return True
        except Exception as exc:
            logger.warning("[hnsw] failed to init fresh index: %s", exc)
            self._index = None
            return False

    # ------------------------------------------------------------------
    # Sidecar I/O
    # ------------------------------------------------------------------

    def _load_labels(self) -> dict[str, int]:
        try:
            raw = json.loads(self._labels_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {}
            return {str(k): int(v) for k, v in raw.items()}
        except Exception as exc:
            logger.warning("[hnsw] failed to load labels: %s", exc)
            return {}

    def _load_meta(self) -> dict:
        try:
            raw = json.loads(self._meta_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception as exc:
            logger.debug("[hnsw] no meta sidecar yet (%s)", exc)
        return {}

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def embed(self, text: str) -> list[float] | None:
        """Embed *text* via LocalEmbeddingBackend; return None on failure."""
        if self._embedder_failed:
            return None
        if not text or not text.strip():
            return None
        if self._embedder is None:
            try:
                from depthfusion.backends.local_embedding import (
                    LocalEmbeddingBackend,
                )
                self._embedder = LocalEmbeddingBackend(model_name=self._model_name)
            except Exception as exc:
                logger.info("[hnsw] embedding backend init failed: %s", exc)
                self._embedder_failed = True
                return None
        try:
            vectors = self._embedder.embed([text])
        except Exception as exc:
            logger.warning("[hnsw] embed() raised: %s", exc)
            return None
        if not vectors:
            return None
        first = vectors[0]
        if first is None:
            return None
        return list(first)

    # ------------------------------------------------------------------
    # Upsert / Search
    # ------------------------------------------------------------------

    def upsert(self, discovery_id: str, content: str) -> bool:
        """Embed *content* and add/replace it in the index.

        Returns True on a successful add/replace; False on any failure.
        Never raises — callers may safely ignore the bool.
        """
        if not self.hnsw_ready or self._index is None:
            return False
        if not discovery_id:
            return False

        vector = self.embed(content)
        if vector is None:
            return False
        if len(vector) != self._dimension:
            logger.warning(
                "[hnsw] embedding dimension mismatch: got %d, expected %d",
                len(vector),
                self._dimension,
            )
            return False

        with self._lock:
            label = self._label_map.get(discovery_id)
            if label is None:
                label = self._next_label
                self._next_label += 1
                self._label_map[discovery_id] = label

            # Grow the index if we're hitting the cap.
            try:
                current_count = int(self._index.get_current_count())
                cap = int(getattr(self._index, "get_max_elements", lambda: self._max_elements)())
                if label >= cap:
                    new_cap = max(cap * 2, label + 1024)
                    try:
                        self._index.resize_index(new_cap)
                        self._max_elements = new_cap
                    except Exception as exc:
                        logger.warning("[hnsw] resize_index failed: %s", exc)
                        return False
            except Exception:
                current_count = len(self._label_map)

            try:
                self._index.add_items([vector], [label])
            except Exception as exc:
                logger.warning("[hnsw] add_items failed for %s: %s", discovery_id, exc)
                return False

            self._last_updated = _now_iso()
            self._upserts_since_save += 1

            should_save = self._upserts_since_save >= _AUTO_SAVE_INTERVAL

        if should_save:
            try:
                self.save()
            except Exception as exc:
                logger.warning("[hnsw] auto-save failed: %s", exc)
        # current_count is local-only, suppress unused warning.
        _ = current_count
        return True

    def search(
        self,
        query: str,
        k: int,
        *,
        principal: "Principal | None" = None,
        acl_resolver: "Any | None" = None,
    ) -> list[dict[str, Any]]:
        """Embed *query* and return top-*k* hits, filtered by principal ACL.

        T-571/T-572: when *principal* is supplied, only results whose
        discovery_id is in the allowed set (as determined by *acl_resolver*)
        are returned.  *acl_resolver* is an optional callable::

            acl_resolver(discovery_id: str) -> list[str] | None

        that returns the ``acl_allow`` list for a given discovery_id.  When
        *acl_resolver* is None but *principal* is set, the filter falls back
        to checking the ``_acl_cache`` attribute on the store itself (populated
        via :meth:`register_acl`).  When neither is available, no ACL filter
        is applied even when a principal is provided (degraded but non-crashing).

        Each hit is ``{"discovery_id": str, "score": float, "label": int}``
        where ``score`` is cosine similarity (1.0 = identical).  Returns
        an empty list on any failure.
        """
        if not self.hnsw_ready or self._index is None:
            return []
        if k <= 0:
            return []
        if not self._label_map:
            return []

        vector = self.embed(query)
        if vector is None:
            return []
        if len(vector) != self._dimension:
            return []

        # Build reverse lookup: label -> discovery_id.
        inverse: dict[int, str] = {label: did for did, label in self._label_map.items()}
        capped_k = min(k, len(self._label_map))
        try:
            labels_arr, distances_arr = self._index.knn_query([vector], k=capped_k)
        except Exception as exc:
            logger.debug("[hnsw] knn_query failed: %s", exc)
            return []

        # hnswlib returns numpy arrays shaped (1, k).
        try:
            row_labels = list(labels_arr[0])
            row_dists = list(distances_arr[0])
        except Exception:
            return []

        # Resolve the ACL filter function once, before the per-hit loop.
        allowed_ids: set[str] | None = None
        _acl_fn: Any = None
        if principal is not None:
            allowed_ids = {principal.principal_id}
            for g in (principal.groups or []):
                allowed_ids.add(g)
            if acl_resolver is not None:
                _acl_fn = acl_resolver
            elif hasattr(self, "_acl_cache"):
                # Fallback: use the in-memory ACL cache populated via register_acl().
                _cache: dict[str, list[str]] = self._acl_cache  # type: ignore[attr-defined]
                _acl_fn = _cache.get

        results: list[dict[str, Any]] = []
        for label_val, dist in zip(row_labels, row_dists):
            label_int = int(label_val)
            discovery_id = inverse.get(label_int)
            if discovery_id is None:
                continue
            # hnswlib cosine "distance" is 1 - cos_sim; convert back to a
            # similarity score in [-1, 1] (in practice [0, 1] for normalised
            # text embeddings).
            score = max(0.0, min(1.0, 1.0 - float(dist)))

            # T-572: ACL filter for HNSW results.
            if allowed_ids is not None and _acl_fn is not None:
                acl_allow = _acl_fn(discovery_id)
                if not acl_allow or not (set(acl_allow) & allowed_ids):
                    continue  # not authorized

            results.append(
                {"discovery_id": discovery_id, "score": score, "label": label_int}
            )
        return results

    def register_acl(self, discovery_id: str, acl_allow: list[str]) -> None:
        """Register ACL metadata for a discovery_id.

        T-572: callers that index documents into the HNSW store should also
        call register_acl() so that search() can apply ACL filtering.
        Stored in ``_acl_cache`` dict on the store instance.
        """
        if not hasattr(self, "_acl_cache"):
            self._acl_cache: dict[str, list[str]] = {}
        self._acl_cache[discovery_id] = list(acl_allow)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist index + labels + meta to disk atomically."""
        if not self.hnsw_ready or self._index is None:
            return
        with self._lock:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_index = self._index_path.with_suffix(self._index_path.suffix + ".tmp")
            try:
                self._index.save_index(str(tmp_index))
                os.replace(tmp_index, self._index_path)
            except Exception as exc:
                logger.warning("[hnsw] save_index failed: %s", exc)
                return

            try:
                _atomic_write(
                    self._labels_path,
                    json.dumps(self._label_map, sort_keys=True).encode("utf-8"),
                )
            except Exception as exc:
                logger.warning("[hnsw] labels write failed: %s", exc)

            try:
                _atomic_write(
                    self._meta_path,
                    json.dumps(self.state(), sort_keys=True).encode("utf-8"),
                )
            except Exception as exc:
                logger.warning("[hnsw] meta write failed: %s", exc)

            self._upserts_since_save = 0

    # ------------------------------------------------------------------
    # State / Capability shapes
    # ------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """HNSWState — used in the .meta.json sidecar."""
        return {
            "schema_version": _SCHEMA_VERSION,
            "index_path": str(self._index_path),
            "embedding_model": self._model_name,
            "dimension": self._dimension,
            "entry_count": len(self._label_map),
            "last_updated": self._last_updated,
        }

    def capability(self) -> dict[str, Any]:
        """HNSWCapability — returned to the agent-ops bridge at startup."""
        return {
            "enabled": bool(self.hnsw_ready),
            "backend": "local" if self.hnsw_ready else "none",
            "model": self._model_name,
            "dimension": self._dimension,
            "index_path": str(self._index_path),
            "entry_count": len(self._label_map),
        }


__all__ = ["HNSWStore"]
