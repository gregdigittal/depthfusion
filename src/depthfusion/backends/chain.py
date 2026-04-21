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


# Lazy-cached singleton so the emission hot path doesn't pay
# `Path.home() / mkdir(exist_ok=True)` cost on every fallback event.
# Under an overload wave every call emits — the cached instance turns
# per-event cost from "stat syscall + object ctor" into a dict lookup.
# Tests that need to redirect metrics dir (e.g. via monkeypatching HOME)
# can call `_reset_metrics_collector()` to clear the cache.
_cached_collector: Any = None


def _reset_metrics_collector() -> None:
    """Clear the cached MetricsCollector. Intended for test use only."""
    global _cached_collector
    _cached_collector = None


def _get_collector() -> Any:
    """Return the cached MetricsCollector or construct and cache one.

    Returns `None` if the import itself fails — observability must
    never break serving, so a missing metrics module is swallowed.
    """
    global _cached_collector
    if _cached_collector is not None:
        return _cached_collector
    try:
        from depthfusion.metrics.collector import MetricsCollector
        _cached_collector = MetricsCollector()
        return _cached_collector
    except Exception as exc:  # noqa: BLE001
        logger.debug("_get_collector: could not construct MetricsCollector: %s", exc)
        return None


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
    collector = _get_collector()
    if collector is None:
        return
    try:
        collector.record(
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

        Unhealthy backends are skipped. The "to" field in the emitted
        event is the NEXT-IN-CHAIN-BY-INDEX name, not a re-probed
        healthy name — this avoids a race where health can flip between
        the loop's health check and the event emission. Operators who
        need the actually-serving backend should correlate with the
        subsequent event (the "from" of the next fallback, or absence
        thereof for success).
        """
        tried: list[str] = []
        skipped_unhealthy: list[str] = []
        n = len(self._backends)
        for i, backend in enumerate(self._backends):
            if not backend.healthy():
                skipped_unhealthy.append(backend.name)
                continue
            tried.append(backend.name)
            try:
                return attempt(backend)
            except _FALLBACK_ERRORS as err:
                # "to" = next name by index (not by health) — see
                # docstring rationale. "exhausted" when this was the
                # last link, regardless of subsequent health state.
                to_name = self._backends[i + 1].name if i + 1 < n else "exhausted"
                _emit_runtime_fallback_event(
                    from_backend=backend.name,
                    to_backend=to_name,
                    capability=capability,
                    error_type=type(err).__name__,
                )
                logger.info(
                    "FallbackChain[%s] %s raised %s; falling through to %s",
                    capability, backend.name, type(err).__name__, to_name,
                )
                continue
            # Any non-fallback exception propagates — it's a backend bug.

        # Exhausted. `chain` carries the full backend list (always) so
        # operators see the structure even when nothing was tried (all
        # unhealthy). The message distinguishes tried-and-errored from
        # skipped-as-unhealthy for debugging clarity.
        all_names = [b.name for b in self._backends]
        detail_parts = []
        if tried:
            detail_parts.append(f"tried and errored: {tried!r}")
        if skipped_unhealthy:
            detail_parts.append(f"skipped as unhealthy: {skipped_unhealthy!r}")
        detail = "; ".join(detail_parts) if detail_parts else "empty chain"
        raise BackendExhaustedError(
            chain=all_names,
            message=(
                f"FallbackChain[{capability}] exhausted over {all_names!r} "
                f"({detail})"
            ),
        )

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
