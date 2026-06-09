from __future__ import annotations

import json
import logging
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Optional

from depthfusion.backends.base import BackendOverloadError, BackendTimeoutError, RateLimitError
from depthfusion.backends.gemma import GemmaBackend

logger = logging.getLogger(__name__)

_DEFAULT_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "openai/gpt-4o"
_DEFAULT_TIMEOUT_SECONDS = 60.0


class OpenRouterBackend(GemmaBackend):
    """OpenRouter adapter — delegates to external LLMs via the OpenAI-compat API.

    Inherits all HTTP/retry/timeout machinery from GemmaBackend; only overrides
    the base URL, auth header injection, and key-presence gate.

    Graceful degradation: missing OPENROUTER_API_KEY → healthy() returns False;
    all callers use healthy() as a gate, so the server continues without the key.
    """

    name = "openrouter"

    def __init__(
        self,
        url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_concurrent: Optional[int] = None,
        api_key: Optional[str] = None,
    ) -> None:
        resolved_url = url if url is not None else os.environ.get(
            "OPENROUTER_BASE_URL", _DEFAULT_URL
        )
        resolved_model = model if model is not None else os.environ.get(
            "OPENROUTER_DEFAULT_MODEL", _DEFAULT_MODEL
        )
        resolved_timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT_SECONDS

        super().__init__(
            url=resolved_url,
            model=resolved_model,
            timeout=resolved_timeout,
            max_concurrent=max_concurrent,
        )

        self._api_key = api_key if api_key is not None else os.environ.get(
            "OPENROUTER_API_KEY"
        )
        if not self._api_key:
            logger.warning(
                "OpenRouterBackend: OPENROUTER_API_KEY not set — "
                "bridge tools disabled; set the key to enable them"
            )

    def healthy(self) -> bool:
        """Construction-time readiness check. Never makes a network call.

        Requires both a valid URL/model (from GemmaBackend) and an API key.
        """
        return super().healthy() and bool(self._api_key)

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 2048,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """Delegate prompt to OpenRouter.

        The `model` kwarg is accepted for convenience when callers pass it
        explicitly; if provided it overrides self._model for this call only.
        """
        if model is not None:
            self._model = model
        return super().complete(prompt, max_tokens=max_tokens, system=system)

    def _post_chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
    ) -> dict[str, Any]:
        """POST to OpenRouter with Bearer auth and attribution header."""
        payload = json.dumps({
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self._url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "X-Title": "DepthFusion",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise RateLimitError(f"OpenRouter returned 429: {exc}") from exc
            if exc.code in (503, 529):
                raise BackendOverloadError(
                    f"OpenRouter returned {exc.code}: {exc}"
                ) from exc
            raise
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise BackendTimeoutError(f"OpenRouter timeout: {exc}") from exc
            reason_str = str(exc.reason).lower() if exc.reason else str(exc).lower()
            if "timed out" in reason_str:
                raise BackendTimeoutError(f"OpenRouter timeout: {exc}") from exc
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise BackendTimeoutError(f"OpenRouter timeout: {exc}") from exc
