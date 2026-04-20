"""LocalEmbeddingBackend — sentence-transformers wrapper for DepthFusion.

v0.5.0 T-118 / T-129: local (on-box) embedding backend used by the
`vps-gpu` install mode to power hybrid BM25 + vector retrieval.

Design notes
============
- **Embedding-only backend.** `complete`, `rerank`, and `extract_structured`
  return safe degenerate values (`""`, `[]`, `None`). The factory only
  ever routes this backend to the `embedding` capability — but implementing
  the full `LLMBackend` protocol keeps `isinstance`-style checks and the
  quality-ranked fallback chain uniform.

- **Lazy model load.** `sentence_transformers.SentenceTransformer(...)`
  takes ~2-3s to construct on first call and downloads weights on cold
  start. We defer construction until the first `embed()` call to keep
  factory resolution cheap. `healthy()` does NOT load the model — it only
  checks that the package is importable, per the `LLMBackend` contract
  (MUST be cheap, no network calls).

- **Graceful degradation.** If the import or model load fails at embed
  time, we return `None` (the "embeddings unsupported" sentinel defined
  in `base.py`). The factory will then fall back to `NullBackend` on the
  next call. We log at INFO level so the fallback is visible without
  spamming.

- **Thread-safe lazy init.** Concurrent `embed()` calls before the model
  is loaded are guarded by a `threading.Lock` so the ~2-3s construction
  cost (and any HF download) is paid exactly once per backend instance,
  even when multiple retrieval threads hit the backend simultaneously.
  The fast path (model already loaded) avoids the lock entirely via a
  double-checked read.

- **Default model.** `all-MiniLM-L6-v2` — 384-dim, ~80MB, CPU-friendly.
  Overridable via `DEPTHFUSION_EMBEDDING_MODEL` env var (same pattern as
  the Gemma backend's model override).

Spec: docs/plans/v0.5/02-build-plan.md §2.2.3
Backlog: T-118 (S-41), T-129 (S-43)
"""
from __future__ import annotations

import importlib.util
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "all-MiniLM-L6-v2"


class LocalEmbeddingBackend:
    """On-box sentence-transformers embedding backend.

    Implements the `LLMBackend` protocol; only `embed()` produces useful
    output. `healthy()` reflects package availability (construction-time
    readiness) — it does NOT probe the model itself.
    """

    name = "local_embedding"

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = (
            model_name
            or os.environ.get("DEPTHFUSION_EMBEDDING_MODEL", "").strip()
            or _DEFAULT_MODEL
        )
        self._model: Any = None  # lazy — set on first embed() call
        self._load_failed = False  # sticky: once failed, stay degraded
        self._load_lock = threading.Lock()  # guards model / _load_failed init

    # ------------------------------------------------------------------
    # LLMBackend protocol — embedding
    # ------------------------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Return embeddings for each input text, or None if unavailable.

        On first call, lazily constructs the `SentenceTransformer` model.
        Subsequent calls reuse the loaded model. Any failure (import
        missing, model download failure, runtime error) returns None so
        the caller can gracefully skip vector search.
        """
        if self._load_failed:
            return None
        if not texts:
            return []

        try:
            model = self._get_model()
        except Exception as exc:  # noqa: BLE001 — graceful-degradation contract
            with self._load_lock:
                self._load_failed = True
            logger.info(
                "LocalEmbeddingBackend: model load failed (%s); future embed() "
                "calls will return None until process restart.",
                exc,
            )
            return None

        if model is None:
            return None

        try:
            # encode() returns numpy array by default; convert to list[list[float]]
            vectors = model.encode(
                texts,
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=False,
            )
            return [list(map(float, v)) for v in vectors]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LocalEmbeddingBackend: encode() failed on %d texts: %s",
                len(texts), exc,
            )
            return None

    # ------------------------------------------------------------------
    # LLMBackend protocol — degenerate implementations
    # ------------------------------------------------------------------

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        system: str | None = None,
    ) -> str:
        """Embedding backend does not generate text — return empty string."""
        return ""

    def rerank(
        self,
        query: str,
        docs: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        """Embedding-based rerank could be implemented via cosine similarity,
        but that's the job of the retrieval pipeline (see T-130). Keep the
        backend surface minimal and return neutral scores, matching
        NullBackend's behaviour.
        """
        n = min(len(docs), top_k)
        return [(i, 0.0) for i in range(n)]

    def extract_structured(
        self,
        prompt: str,
        schema: dict,
    ) -> dict | None:
        return None

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def healthy(self) -> bool:
        """Return True if sentence_transformers is importable.

        Contract: MUST be cheap (no network, no model load). We use
        `importlib.util.find_spec` so we don't pay the multi-second
        import cost on every factory lookup.
        """
        if self._load_failed:
            return False
        try:
            return importlib.util.find_spec("sentence_transformers") is not None
        except (ImportError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_model(self) -> Any:
        """Return the loaded SentenceTransformer, lazily constructing it.

        Thread-safe: uses double-checked locking so the fast path (model
        already loaded) is lock-free, but concurrent first-callers share
        a single load attempt. Raises if sentence_transformers is not
        importable or the model cannot be loaded — callers (embed())
        translate the exception into the `None` protocol sentinel.
        """
        # Fast path: model already loaded — no lock needed.
        if self._model is not None:
            return self._model

        # Slow path: serialize the load so the ~2-3s cost is paid once.
        with self._load_lock:
            # Re-check under lock (another thread may have loaded first).
            if self._model is not None:
                return self._model
            from sentence_transformers import SentenceTransformer  # lazy import
            self._model = SentenceTransformer(self._model_name)
            return self._model


__all__ = ["LocalEmbeddingBackend"]
