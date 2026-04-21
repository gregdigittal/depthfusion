"""FallbackChain — ordered `LLMBackend` fallback on typed errors.

v0.6.0-alpha (S-44 AC-3/AC-4): when a primary backend raises
`RateLimitError`, `BackendOverloadError`, or `BackendTimeoutError`,
transparently try the next backend in the chain. Emit a JSONL event
per transition so operators can see degradation without inspecting
per-capability exception logs.

Distinct from factory-level fallback (factory.py `_emit_fallback_event`):
  * factory fallback = backend was never usable (unhealthy at construction);
    actual resolves to NullBackend before the first call.
  * runtime fallback (here) = backend WAS healthy, responded normally in
    the past, but THIS call raised a typed error; we try the next link.

The two mechanisms are complementary — the factory decides initial
routing, FallbackChain handles mid-flight failures. They emit different
metrics (`backend.fallback` vs `backend.runtime_fallback`) so operators
can tell them apart in `backend_summary()` tables.

Semantics:
  * `name` = `"+".join(b.name for b in chain)` — e.g. "gemma+haiku+null".
  * `healthy()` = `any(b.healthy() for b in chain)`.
  * Per-capability methods iterate healthy backends in order:
      - If the backend returns without raising, that result is final.
        (Even None / empty — the per-backend decision to degrade is
        respected; we don't second-guess.)
      - If the backend raises one of the three typed fallback errors,
        emit a `backend.runtime_fallback` event and try the next link.
      - Any other exception is a bug in the primary backend and
        propagates to the caller unchanged.
      - If every link exhausts, raise `BackendExhaustedError(chain=names)`.

Thread-safety: the chain object is stateless (holds an immutable tuple).
Individual backends carry their own concurrency contracts per the
protocol.

Backlog: S-44 AC-3, AC-4 (v0.6.0-alpha scope).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from depthfusion.backends.base import (
    BackendExhaustedError,
    BackendOverloadError,
    BackendTimeoutError,
    LLMBackend,
    RateLimitError,
)

logger = logging.getLogger(__name__)


_FALLBACK_ERRORS = (RateLimitError, BackendOverloadError, BackendTimeoutError)


def _emit_runtime_fallback_event(
    from_backend: str,
    to_backend: str,
    capability: str,
    error_type: str,
) -> None:
    """Emit a JSONL event when the chain falls through from one link to the next.

    Gated on `DEPTHFUSION_BACKEND_FALLBACK_LOG` (default: enabled) — same
    env var that gates factory-level fallback events, so operators have
    one switch. Errors are swallowed; observability must never break
    serving.

    Distinct metric name (`backend.runtime_fallback`) from the factory's
    construction-time fallback so the two can be counted separately in
    aggregation.
    """
    raw = os.environ.get("DEPTHFUSION_BACKEND_FALLBACK_LOG", "true").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return
    try:
        from depthfusion.metrics.collector import MetricsCollector
        MetricsCollector().record(
            "backend.runtime_fallback",
            1.0,
            labels={
                "from": from_backend,
                "to": to_backend,
                "capability": capability,
                "error_type": error_type,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("_emit_runtime_fallback_event: could not write metrics: %s", exc)


class FallbackChain:
    """Ordered fallback over multiple `LLMBackend` implementations.

    Construct with an ordered list (primary first). The chain itself
    implements `LLMBackend`, so it's a drop-in replacement anywhere a
    single backend is expected.

    Example::

        chain = FallbackChain([
            GemmaBackend(),
            HaikuBackend(),
            NullBackend(),
        ])
        # chain.name == "gemma+haiku+null"
        text = chain.complete("hello", max_tokens=64)
        # On Gemma 503 -> Haiku. On Haiku 429 -> Null. On Null (always
        # healthy), returns safe defaults.
    """

    def __init__(self, backends: list[LLMBackend]) -> None:
        if not backends:
            raise ValueError("FallbackChain requires at least one backend")
        self._backends = tuple(backends)
        self.name = "+".join(b.name for b in self._backends)

    def healthy(self) -> bool:
        return any(b.healthy() for b in self._backends)

    def _try_chain(
        self,
        capability: str,
        attempt: Callable[[LLMBackend], Any],
    ) -> Any:
        """Walk the chain until one backend returns without a typed error.

        Unhealthy backends are skipped entirely (they don't count toward
        exhaustion — if they later become healthy, they rejoin the chain
        on the next call).
        """
        tried: list[str] = []
        for i, backend in enumerate(self._backends):
            if not backend.healthy():
                continue
            tried.append(backend.name)
            try:
                return attempt(backend)
            except _FALLBACK_ERRORS as err:
                next_name = self._next_healthy_name(i + 1)
                _emit_runtime_fallback_event(
                    from_backend=backend.name,
                    to_backend=next_name,
                    capability=capability,
                    error_type=type(err).__name__,
                )
                logger.info(
                    "FallbackChain[%s] %s raised %s; falling through to %s",
                    capability, backend.name, type(err).__name__, next_name,
                )
                continue
            # Any non-fallback exception propagates — it's a backend bug.

        raise BackendExhaustedError(
            chain=tried,
            message=(
                f"FallbackChain[{capability}] exhausted after trying {tried!r}; "
                f"no healthy backend remained or all raised typed errors"
            ),
        )

    def _next_healthy_name(self, start_idx: int) -> str:
        """Name of the next healthy backend after `start_idx`, or 'exhausted'."""
        for j in range(start_idx, len(self._backends)):
            if self._backends[j].healthy():
                return self._backends[j].name
        return "exhausted"

    # --- LLMBackend protocol methods ---

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        system: str | None = None,
    ) -> str:
        return self._try_chain(
            "complete",
            lambda b: b.complete(prompt, max_tokens=max_tokens, system=system),
        )

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return self._try_chain("embed", lambda b: b.embed(texts))

    def rerank(
        self,
        query: str,
        docs: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        return self._try_chain(
            "rerank",
            lambda b: b.rerank(query, docs, top_k),
        )

    def extract_structured(
        self,
        prompt: str,
        schema: dict,
    ) -> dict | None:
        return self._try_chain(
            "extract_structured",
            lambda b: b.extract_structured(prompt, schema),
        )


__all__ = ["FallbackChain"]
