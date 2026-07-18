"""DistillationClient — local-LLM-first text completion with Haiku fallback.

E-68 S-228.

Backend resolution:
  auto   — probe local_llm_url with a lightweight ping; use local if reachable,
            else fall back to Haiku (requires DEPTHFUSION_API_KEY).
  local  — always use local_llm_url; raise RuntimeError if URL is empty.
  haiku  — always use Haiku backend (reuses HaikuBackend).

Follows the guarded-import + graceful-degradation style of backends/haiku.py:
  - ``httpx`` is imported lazily behind a try/except; missing httpx means
    local probing is unavailable and the client falls back to Haiku.
  - ``anthropic`` / HaikuBackend is imported lazily so the module can be
    imported without the anthropic package installed.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from depthfusion.core.config import DepthFusionConfig

logger = logging.getLogger(__name__)

# ── optional httpx for local LLM probing / completion ─────────────────────────
try:
    import httpx
    _HTTPX_IMPORTABLE = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    _HTTPX_IMPORTABLE = False

_PROBE_TIMEOUT = 2.0      # seconds — fast ping to decide reachability
_COMPLETE_TIMEOUT = 60.0  # seconds — full completion request


class DistillationClient:
    """Async text-completion client with local-LLM-first routing.

    Parameters
    ----------
    config:
        DepthFusionConfig instance. Reads ``distillation_backend`` and
        ``local_llm_url`` fields.

    Graceful degradation:
      - If local backend is selected but httpx is not installed, logs a warning
        and falls back to Haiku.
      - If Haiku backend is selected but HaikuBackend is not healthy (missing
        API key or anthropic package), ``complete()`` returns an empty string.
    """

    def __init__(self, config: "DepthFusionConfig") -> None:
        self._config = config
        self._resolved: str | None = None   # cached resolved backend ("local"|"haiku")

    # ── Public API ─────────────────────────────────────────────────────────────

    async def complete(self, prompt: str, *, max_tokens: int = 512) -> str:
        """Return a completion string for *prompt*.

        Resolves the backend on first call (for ``auto`` mode) and caches it.
        Returns an empty string on any unrecoverable error rather than raising.
        """
        backend = await self._resolve_backend()
        if backend == "local":
            return await self._complete_local(prompt, max_tokens=max_tokens)
        return await self._complete_haiku(prompt, max_tokens=max_tokens)

    def resolved_backend(self) -> str | None:
        """Return the cached resolved backend, or None if not yet resolved."""
        return self._resolved

    # ── Resolution ─────────────────────────────────────────────────────────────

    async def _resolve_backend(self) -> str:
        """Resolve and cache which backend to use."""
        if self._resolved is not None:
            return self._resolved

        mode = (self._config.distillation_backend or "auto").lower()

        if mode == "haiku":
            self._resolved = "haiku"
            return "haiku"

        if mode == "local":
            if not self._config.local_llm_url:
                logger.warning(
                    "DistillationClient: distillation_backend=local but "
                    "local_llm_url is not set — falling back to haiku"
                )
                self._resolved = "haiku"
                return "haiku"
            self._resolved = "local"
            return "local"

        # auto — probe local, fall back to haiku
        if self._config.local_llm_url and await self._probe_local():
            self._resolved = "local"
        else:
            self._resolved = "haiku"
        return self._resolved

    # ── Local backend ──────────────────────────────────────────────────────────

    async def _probe_local(self) -> bool:
        """Return True if the local LLM endpoint is reachable.

        Sends a lightweight GET to the base URL (or /health if available).
        Returns False on any error (connection refused, timeout, missing httpx).
        """
        if not _HTTPX_IMPORTABLE or httpx is None:
            logger.debug(
                "DistillationClient: httpx not installed — cannot probe local LLM"
            )
            return False

        url = self._config.local_llm_url.rstrip("/")
        # Probe /models — standard OpenAI-compat endpoint that vLLM/Ollama expose
        probe_url = f"{url}/models"
        try:
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
                resp = await client.get(probe_url)
                reachable = resp.status_code < 500
                logger.debug(
                    "DistillationClient: probe %s → %s (reachable=%s)",
                    probe_url,
                    resp.status_code,
                    reachable,
                )
                return reachable
        except Exception as exc:
            logger.debug("DistillationClient: probe %s failed: %s", probe_url, exc)
            return False

    async def _complete_local(self, prompt: str, *, max_tokens: int = 512) -> str:
        """POST to local OpenAI-compat /chat/completions endpoint."""
        if not _HTTPX_IMPORTABLE or httpx is None:
            logger.warning(
                "DistillationClient: httpx not installed — cannot use local backend"
            )
            return ""

        url = self._config.local_llm_url.rstrip("/")
        endpoint = f"{url}/chat/completions"
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=_COMPLETE_TIMEOUT) as client:
                resp = await client.post(endpoint, json=payload)
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices") or []
                if choices:
                    msg = choices[0].get("message", {})
                    return str(msg.get("content", ""))
                return ""
        except Exception as exc:
            logger.warning(
                "DistillationClient: local completion failed (%s) — returning empty",
                exc,
            )
            return ""

    # ── Haiku backend ──────────────────────────────────────────────────────────

    async def _complete_haiku(self, prompt: str, *, max_tokens: int = 512) -> str:
        """Delegate to HaikuBackend.complete() (synchronous, run in executor)."""
        try:
            from depthfusion.backends.haiku import HaikuBackend
        except Exception as exc:  # pragma: no cover
            logger.warning("DistillationClient: cannot import HaikuBackend: %s", exc)
            return ""

        backend = HaikuBackend()
        if not backend.healthy():
            logger.debug(
                "DistillationClient: HaikuBackend not healthy "
                "(missing API key or anthropic package)"
            )
            return ""

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: backend.complete(prompt, max_tokens=max_tokens),
            )
            return result or ""
        except Exception as exc:
            logger.warning(
                "DistillationClient: Haiku completion failed (%s) — returning empty",
                exc,
            )
            return ""


__all__ = ["DistillationClient"]
