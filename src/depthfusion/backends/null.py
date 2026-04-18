"""NullBackend — passthrough / no-op backend.

Used as the terminal fallback in every capability chain. Always healthy;
every method returns a safe degenerate result. Chosen by the factory
whenever no credential is available, no vendor SDK is installed, or the
user explicitly requests a no-op backend via env var.

Spec: docs/plans/v0.5/02-build-plan.md §2.2.1
Backlog: T-117
"""
from __future__ import annotations

from depthfusion.backends.base import LLMBackend


class NullBackend:
    """No-op LLMBackend. All methods return safe degenerate results.

    Explicitly implements every LLMBackend method even when the degenerate
    result is trivial, so it satisfies `isinstance(x, LLMBackend)` via
    the protocol's `runtime_checkable` decorator.
    """

    name = "null"

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        system: str | None = None,
    ) -> str:
        return ""

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None

    def rerank(
        self,
        query: str,
        docs: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        # Preserve original order; uniform 0.0 score — the caller's
        # own tie-breaker (e.g. source weighting) decides final rank.
        return [(i, 0.0) for i in range(min(len(docs), top_k))]

    def extract_structured(
        self,
        prompt: str,
        schema: dict,
    ) -> dict | None:
        return None

    def healthy(self) -> bool:
        return True


# Module-load-time protocol check — fails early if the class drifts from
# the protocol (e.g. a method is renamed). Unused at runtime.
def _protocol_sanity() -> LLMBackend:  # pragma: no cover
    return NullBackend()


__all__ = ["NullBackend"]
