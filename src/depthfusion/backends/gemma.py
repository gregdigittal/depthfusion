"""GemmaBackend — vLLM-served Gemma implementation of LLMBackend.

v0.5.0: second real backend. Targets the vps-gpu deployment tier on the
Hetzner GEX44 (NVIDIA RTX 4000 SFF Ada, 20 GB VRAM) running Gemma 3 12B
quantized (AWQ by default) via vLLM's OpenAI-compatible server.

Transport: stdlib `urllib.request` posting JSON to vLLM's
`/v1/chat/completions` endpoint. Stdlib-only means zero new runtime
dependencies — the `vps-gpu` extra reserves its dep budget for `vllm`,
`sentence-transformers`, and `torch` (CUDA build).

Typed-error translation (AC-01-4, parity with HaikuBackend):
  HTTP 429 / 529 / 503  → RateLimitError / BackendOverloadError
  Socket / HTTP timeout → BackendTimeoutError
Other errors (JSON parse, unexpected shape) degrade to safe-default
returns per the Protocol contract.

Spec: docs/plans/v0.5/02-build-plan.md §2.4 TG-04
Backlog: T-132, T-135 (tests).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Optional

from depthfusion.backends.base import (
    BackendOverloadError,
    BackendTimeoutError,
    RateLimitError,
)

logger = logging.getLogger(__name__)


_DEFAULT_URL = "http://127.0.0.1:8000/v1"
_DEFAULT_MODEL = "google/gemma-3-12b-it-AWQ"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_CONCURRENT = 4


# Reranker prompt — same pattern as HaikuBackend so output shape matches
# across backends and the migration preserves v0.4.x semantics when the
# user flips DEPTHFUSION_MODE from vps-cpu to vps-gpu.
_RERANK_PROMPT = """\
You are a relevance ranker. Given a search query and a list of memory blocks, \
return a JSON array of indices (0-based) sorted from most to least relevant to the query.

Query: {query}

Blocks:
{docs_text}

Return ONLY a JSON array of indices, e.g. [2, 0, 1]. No explanation."""


class GemmaBackend:
    """vLLM-served Gemma implementation of LLMBackend.

    Graceful degradation:
      - `healthy()` is construction-time only and returns True when URL +
        model are configured. Network reachability is NOT probed here
        (Protocol contract forbids network calls in healthy()).
      - Typed errors propagate from every method so callers drive fallback
        chains — the v0.5 AC-01-4 contract.
      - Parse errors / unexpected responses fall through to safe-degenerate
        return values.

    Thread-safety:
      - `urllib.request` connections are per-call (no shared state),
        so the backend is safe for concurrent reads without locks.
      - The `_max_concurrent` field is advisory for callers that want to
        bound vLLM load; the backend itself does not enforce it.
    """

    name = "gemma"

    def __init__(
        self,
        url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_concurrent: Optional[int] = None,
    ) -> None:
        raw_url = url if url is not None else os.environ.get(
            "DEPTHFUSION_GEMMA_URL", _DEFAULT_URL
        )
        self._url = raw_url.rstrip("/")
        self._model = model if model is not None else os.environ.get(
            "DEPTHFUSION_GEMMA_MODEL", _DEFAULT_MODEL
        )
        if timeout is not None:
            self._timeout = timeout
        else:
            try:
                self._timeout = float(os.environ.get(
                    "DEPTHFUSION_GEMMA_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS,
                ))
            except (ValueError, TypeError):
                self._timeout = _DEFAULT_TIMEOUT_SECONDS
        if max_concurrent is not None:
            self._max_concurrent = max_concurrent
        else:
            try:
                self._max_concurrent = int(os.environ.get(
                    "DEPTHFUSION_GEMMA_MAX_CONCURRENT", _DEFAULT_MAX_CONCURRENT,
                ))
            except (ValueError, TypeError):
                self._max_concurrent = _DEFAULT_MAX_CONCURRENT

    # ── Protocol methods ────────────────────────────────────────────

    def healthy(self) -> bool:
        """Construction-time readiness. Never makes a network call.

        Returns True when URL and model are non-empty. The factory's
        healthy-check-then-fallback uses this; live vLLM reachability is
        probed via the typed-error path on the first real call.
        """
        return bool(self._url) and bool(self._model)

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        system: str | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            data = self._post_chat(messages, max_tokens=max_tokens)
        except (RateLimitError, BackendOverloadError, BackendTimeoutError):
            raise
        except Exception as exc:
            logger.debug("GemmaBackend.complete unexpected error: %s", exc)
            return ""

        return self._extract_text(data)

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """vLLM supports an embedding endpoint, but GemmaBackend does not
        expose it — DepthFusion's vps-gpu mode routes the `embedding`
        capability to `LocalEmbeddingBackend` (sentence-transformers) per
        the factory's default-dispatch table. Return None to signal the
        capability boundary.
        """
        return None

    def rerank(
        self,
        query: str,
        docs: list[str],
        top_k: int,
    ) -> list[tuple[int, float]]:
        """Rerank via JSON-indices prompt. Same semantics as HaikuBackend
        (see haiku.py::rerank for full contract); prompt text is identical
        so cross-backend outputs are directly comparable.
        """
        if not docs:
            return []

        docs_text = "\n".join(f"[{i}] {d[:300]}" for i, d in enumerate(docs))
        prompt = _RERANK_PROMPT.format(query=query, docs_text=docs_text)

        try:
            raw = self.complete(prompt, max_tokens=128)
        except (RateLimitError, BackendOverloadError, BackendTimeoutError):
            raise

        if not raw:
            return [(i, 0.0) for i in range(min(len(docs), top_k))]

        try:
            indices = json.loads(raw.strip())
        except json.JSONDecodeError as exc:
            logger.debug("GemmaBackend.rerank JSON parse failed: %s", exc)
            return [(i, 0.0) for i in range(min(len(docs), top_k))]

        if not isinstance(indices, list):
            return [(i, 0.0) for i in range(min(len(docs), top_k))]

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
        """Schema-driven extraction. Tolerates ```json fences in response."""
        full_prompt = (
            f"{prompt}\n\n"
            f"Return a single JSON object matching this schema "
            f"(no surrounding text, no code fences): {json.dumps(schema)}"
        )
        try:
            raw = self.complete(full_prompt, max_tokens=1024)
        except (RateLimitError, BackendOverloadError, BackendTimeoutError):
            raise

        if not raw:
            return None

        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            if len(lines) >= 2:
                raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.debug("GemmaBackend.extract_structured JSON parse failed: %s", exc)
            return None

        return parsed if isinstance(parsed, dict) else None

    # ── HTTP transport ─────────────────────────────────────────────

    def _post_chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
    ) -> dict[str, Any]:
        """POST an OpenAI-compatible chat-completions request to vLLM.

        Translates HTTP / socket errors into the Protocol's typed errors.
        Any non-translatable exception re-raises as-is so the caller's
        broad try/except can safe-degenerate.
        """
        payload = json.dumps({
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self._url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 429 = rate-limit; 503/529 = overload (vLLM signals 503 for
            # "no capacity"; we treat both as the overload class).
            if exc.code == 429:
                raise RateLimitError(f"vLLM returned 429: {exc}") from exc
            if exc.code in (503, 529):
                raise BackendOverloadError(f"vLLM returned {exc.code}: {exc}") from exc
            raise
        except urllib.error.URLError as exc:
            # Connection refused / DNS failure / socket errors.
            # Timeouts come through as URLError(reason=TimeoutError(...)) or socket.timeout.
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise BackendTimeoutError(f"vLLM timeout: {exc}") from exc
            reason_str = str(exc.reason).lower() if exc.reason else str(exc).lower()
            if "timed out" in reason_str:
                raise BackendTimeoutError(f"vLLM timeout: {exc}") from exc
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise BackendTimeoutError(f"vLLM timeout: {exc}") from exc

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        """Extract the assistant text from an OpenAI-compatible response.

        Returns empty string on any unexpected shape — callers' contracts
        already accept empty as 'no output'.
        """
        try:
            choices = data.get("choices", [])
            if not choices:
                return ""
            message = choices[0].get("message", {})
            content = message.get("content", "")
            return content if isinstance(content, str) else ""
        except (AttributeError, TypeError, IndexError):
            return ""


__all__ = ["GemmaBackend"]
