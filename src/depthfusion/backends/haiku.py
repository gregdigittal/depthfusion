"""HaikuBackend — Anthropic claude-haiku implementation of LLMBackend.

v0.5.0: first real backend to ship behind the provider-agnostic interface.
Replaces direct `anthropic.Anthropic(...)` instantiation at every LLM
call-site (reranker / extractor / linker / summariser / decision_extractor).

Two load-bearing behaviours per the Phase 3 acceptance criteria:
  1. **Explicit `api_key=`** — never relies on the Anthropic SDK's
     default `ANTHROPIC_API_KEY` environment-variable lookup. Reads
     `DEPTHFUSION_API_KEY` only. This is the C2 fix from Phase 1 §1.2
     that closes the bare-client bug in `graph/linker.py:L112`.
  2. **Typed error translation** — `anthropic.RateLimitError` (HTTP 429),
     `anthropic.APIStatusError` with 529 status (overload), and
     `anthropic.APITimeoutError` are translated to the Protocol's typed
     error classes so callers can drive fallback chains without inspecting
     vendor-specific exceptions (AC-01-4).

Other exceptions (JSON parse errors, unexpected SDK failures) are NOT
translated — they fall through to the method's safe-degenerate return
path so the caller's graceful-degradation contract is preserved.

Spec: docs/plans/v0.5/02-build-plan.md §2.2.1
Backlog: T-116, T-122 (tests).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from depthfusion.backends.base import (
    BackendOverloadError,
    BackendTimeoutError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

try:
    import anthropic
    _ANTHROPIC_IMPORTABLE = True
except ImportError:
    anthropic = None  # type: ignore[assignment]
    _ANTHROPIC_IMPORTABLE = False


_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_TIMEOUT_SECONDS = 30.0

# Reranker prompt — ports the pattern from `depthfusion.retrieval.reranker`
# so migrated call-sites produce equivalent output.
_RERANK_PROMPT = """\
You are a relevance ranker. Given a search query and a list of memory blocks, \
return a JSON array of indices (0-based) sorted from most to least relevant to the query.

Query: {query}

Blocks:
{docs_text}

Return ONLY a JSON array of indices, e.g. [2, 0, 1]. No explanation."""


class HaikuBackend:
    """Anthropic claude-haiku implementation of `LLMBackend`.

    Graceful degradation:
      - If `anthropic` SDK is not importable → `healthy() is False`
      - If `DEPTHFUSION_API_KEY` not set → `healthy() is False`
      - In both cases every method returns a safe degenerate result
        rather than raising.
      - If a method DOES raise `RateLimitError` / `BackendOverloadError`
        / `BackendTimeoutError`, that is intentional and callers MUST
        drive the fallback chain (the silent-swallow pattern from
        v0.4.x is fixed by construction).
    """

    name = "haiku"

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key: Optional[str] = None,
        timeout: Optional[float] = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._client: Any = None

        if not _ANTHROPIC_IMPORTABLE:
            return

        # C2 fix: DEPTHFUSION_API_KEY only — never ANTHROPIC_API_KEY.
        # Caller-supplied api_key takes precedence for explicit overrides
        # (e.g. testing with a mock client).
        resolved_key = api_key if api_key is not None else os.environ.get("DEPTHFUSION_API_KEY")
        if not resolved_key:
            return

        # Explicit api_key= to the SDK — does not rely on env default.
        self._client = anthropic.Anthropic(api_key=resolved_key, timeout=timeout)

    # ── Protocol methods ────────────────────────────────────────────

    def healthy(self) -> bool:
        """Cheap construction-time check. Never makes a network call."""
        return self._client is not None

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        system: str | None = None,
    ) -> str:
        if not self._client:
            return ""
        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            msg = self._client.messages.create(**kwargs)
            if not msg.content:
                return ""
            return msg.content[0].text
        except Exception as exc:
            self._raise_typed_or_reraise(exc)
            return ""  # unreachable; _raise_typed_or_reraise always raises

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Anthropic does not expose an embeddings endpoint — always returns None.

        Documented per the LLMBackend Protocol: `None` signals
        'capability unsupported'. For embeddings, use LocalEmbeddingBackend
        (T-118) on vps-gpu / vps-cpu.
        """
        return None

    def rerank(
        self,
        query: str,
        docs: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        """Rerank via a JSON-indices prompt. Returns `(idx, score)` pairs.

        Scoring: linear-decay by returned rank (rank 0 → 1.0, rank 1 → 0.95,
        rank 2 → 0.90, …). This is a stable proxy for comparability within
        a single call; absolute scores are not comparable across calls.

        On SDK-level typed errors (rate-limit / overload / timeout), propagates
        the typed error so the caller's fallback chain can react. On other
        failures (parse error, unexpected response shape) falls back to the
        safe-degenerate identity ordering.
        """
        if not self._client or not docs:
            return [(i, 0.0) for i in range(min(len(docs), top_k))]

        docs_text = "\n".join(f"[{i}] {d[:300]}" for i, d in enumerate(docs))
        prompt = _RERANK_PROMPT.format(query=query, docs_text=docs_text)

        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=128,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip() if msg.content else "[]"
            indices = json.loads(raw)
        except (RateLimitError, BackendOverloadError, BackendTimeoutError):
            raise
        except Exception as exc:
            try:
                self._raise_typed_or_reraise(exc)
            except (RateLimitError, BackendOverloadError, BackendTimeoutError):
                raise
            except Exception as inner:
                logger.debug("HaikuBackend.rerank fell back (error: %s)", inner)
                return [(i, 0.0) for i in range(min(len(docs), top_k))]

        if not isinstance(indices, list):
            logger.debug("HaikuBackend.rerank: response not a list: %r", indices)
            return [(i, 0.0) for i in range(min(len(docs), top_k))]

        # Convert rank → score, filter valid indices, dedup
        seen: set[int] = set()
        result: list[tuple[int, float]] = []
        for rank, idx in enumerate(indices):
            if isinstance(idx, int) and 0 <= idx < len(docs) and idx not in seen:
                score = max(0.0, 1.0 - rank * 0.05)
                result.append((idx, score))
                seen.add(idx)
                if len(result) >= top_k:
                    break
        return result

    def extract_structured(
        self,
        prompt: str,
        schema: dict,
    ) -> dict | None:
        """Extract a JSON object matching `schema` from a claude-haiku response.

        Tolerates code-fence wrappers (```json ... ```) which claude sometimes
        produces. Returns `None` on parse failure or schema mismatch rather
        than raising (the Protocol contract).
        """
        if not self._client:
            return None

        full_prompt = (
            f"{prompt}\n\n"
            f"Return a single JSON object matching this schema "
            f"(no surrounding text, no code fences): {json.dumps(schema)}"
        )

        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[{"role": "user", "content": full_prompt}],
            )
        except (RateLimitError, BackendOverloadError, BackendTimeoutError):
            raise
        except Exception as exc:
            try:
                self._raise_typed_or_reraise(exc)
            except (RateLimitError, BackendOverloadError, BackendTimeoutError):
                raise
            except Exception as inner:
                logger.debug("HaikuBackend.extract_structured fell back (error: %s)", inner)
                return None

        if not msg.content:
            return None

        raw = msg.content[0].text.strip()
        # Tolerate code-fence wrappers
        if raw.startswith("```"):
            lines = raw.split("\n")
            if len(lines) >= 2:
                # Strip the opening ```json (or ```) and the trailing ```
                raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.debug("HaikuBackend.extract_structured: JSON parse failed: %s", exc)
            return None

        return parsed if isinstance(parsed, dict) else None

    # ── Error translation ──────────────────────────────────────────

    def _raise_typed_or_reraise(self, exc: Exception) -> None:
        """Translate an anthropic SDK exception into a typed Protocol error.

        Raises:
            RateLimitError: on 429 (anthropic.RateLimitError or APIStatusError 429)
            BackendOverloadError: on 529 (anthropic.APIStatusError 529)
            BackendTimeoutError: on anthropic.APITimeoutError
            (original exception): for any other type — caller decides how to handle
        """
        if not _ANTHROPIC_IMPORTABLE:
            raise exc

        # Specific error classes first
        if isinstance(exc, anthropic.RateLimitError):
            raise RateLimitError(str(exc)) from exc
        if isinstance(exc, anthropic.APITimeoutError):
            raise BackendTimeoutError(str(exc)) from exc
        # Status-code-based dispatch (APIStatusError is the base for many)
        if isinstance(exc, anthropic.APIStatusError):
            status = getattr(exc, "status_code", None)
            if status == 429:
                raise RateLimitError(str(exc)) from exc
            if status == 529:
                raise BackendOverloadError(str(exc)) from exc

        # Not a translatable error — re-raise as-is
        raise exc


__all__ = ["HaikuBackend"]
