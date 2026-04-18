"""LLM backend protocol — pluggable interface for DepthFusion LLM call-sites.

v0.5.0 refactor: replaces direct `anthropic.Anthropic(...)` instantiation
with a provider-agnostic interface. All six capabilities (reranker /
extractor / summariser / linker / decision_extractor / embedding) route
through `depthfusion.backends.factory.get_backend`.

Implementations: NullBackend (always), HaikuBackend (v0.5 T-116),
GemmaBackend (v0.5 T-132), LocalEmbeddingBackend (v0.5 T-118).

Typed errors let callers drive fallback chains without inspecting
vendor-specific exception types. Backend implementations translate
their native errors into these four classes.

Spec: docs/plans/v0.5/02-build-plan.md §2.2.1
Backlog: T-115 (AC-01-1)
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class RateLimitError(Exception):
    """Raised when the backend reports a rate limit (HTTP 429 or equivalent).

    Callers should trigger the fallback chain rather than retry-in-place;
    the factory will pick the next backend in the quality-ranked chain.
    """


class BackendOverloadError(Exception):
    """Raised when the backend reports capacity overload (HTTP 529 or equivalent).

    Semantically distinct from rate-limit: the backend is healthy but
    temporarily saturated. Fallback-chain behaviour is identical in v0.5;
    a future release may add backend-specific retry timing.
    """


class BackendTimeoutError(Exception):
    """Raised when the backend exceeds its configured timeout without returning.

    Timeouts are configured per backend (see HaikuBackend / GemmaBackend).
    """


class BackendExhaustedError(Exception):
    """Raised when every backend in a fallback chain has been tried and failed.

    Carries a `chain` attribute (list of backend names attempted, in order)
    so callers can surface the failure path in error messages and audit logs.
    """

    def __init__(self, chain: list[str], message: str | None = None) -> None:
        self.chain = chain
        msg = message or (
            f"Backend fallback chain exhausted: tried {chain!r} in order, all failed."
        )
        super().__init__(msg)


@runtime_checkable
class LLMBackend(Protocol):
    """Pluggable LLM backend covering every DepthFusion LLM call-site.

    Not every implementation supports every method. Backends that cannot
    satisfy a capability return a safe degenerate result (empty string,
    `None`, empty list) rather than raising `NotImplementedError` — this
    keeps the factory's per-capability dispatch simple and lets a single
    backend serve multiple capabilities with graceful fallback.

    Contract:
      - `name` is a short ASCII identifier (e.g. "haiku", "gemma", "null").
      - `healthy()` MUST be cheap (no network calls); it reflects construction-
        time readiness (credentials present, SDK importable) not live-probe state.
      - `complete / embed / rerank / extract_structured` MAY raise
        `RateLimitError`, `BackendOverloadError`, or `BackendTimeoutError`.
        Other exceptions are caller-visible bugs.
      - Methods MUST be thread-safe for read-only use. Stateful backends
        (e.g. connection-pooling) document their own concurrency contract.
    """

    name: str

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        system: str | None = None,
    ) -> str:
        """Return a text completion for `prompt`.

        Backends that do not support completion return an empty string.
        """
        ...

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Return embeddings for each input text.

        Returns `None` when the backend does not support embeddings (e.g.
        HaikuBackend returns `None`; LocalEmbeddingBackend returns vectors).
        Length of result MUST equal `len(texts)` when non-`None`.
        """
        ...

    def rerank(
        self,
        query: str,
        docs: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        """Return `(original_index, score)` pairs sorted by descending
        relevance to `query`.

        Length of result is `min(len(docs), top_k)`. Scores are backend-
        specific; only the ordering is guaranteed comparable.
        """
        ...

    def extract_structured(
        self,
        prompt: str,
        schema: dict,
    ) -> dict | None:
        """Return a parsed object matching `schema`, or `None` on failure.

        Backends without structured-extraction support return `None`.
        Schema validation is the backend's responsibility.
        """
        ...

    def healthy(self) -> bool:
        """Return `True` if the backend is ready to serve calls.

        Checked at construction time (by the factory) and before traversing
        into the next fallback-chain element. MUST NOT make network calls.
        """
        ...
