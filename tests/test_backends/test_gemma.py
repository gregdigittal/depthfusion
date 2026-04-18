# tests/test_backends/test_gemma.py
"""GemmaBackend behaviour tests (mocked urllib.request transport).

Covers:
  - Construction from env vars + explicit args
  - healthy() construction-time check
  - complete() / rerank() / extract_structured() happy paths
  - Typed-error translation: HTTP 429 / 503 / 529 / timeout
  - Graceful degradation on parse failures
  - OpenAI-compatible request payload shape verification
  - Concurrency-cap config propagation

Backlog: T-135, T-122.
"""
from __future__ import annotations

import json
import socket
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.backends.base import (
    BackendOverloadError,
    BackendTimeoutError,
    RateLimitError,
)
from depthfusion.backends.gemma import GemmaBackend

# ── Helpers ──────────────────────────────────────────────────────────────


def _mock_response(text: str) -> MagicMock:
    """Build a mock urlopen() context-manager response with a vLLM
    OpenAI-compatible payload containing the given text.
    """
    payload = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    data = json.dumps(payload).encode("utf-8")

    resp = MagicMock()
    resp.read.return_value = data
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(code: int, msg: str = "err") -> urllib.error.HTTPError:
    """Construct an HTTPError with a specific status code."""
    return urllib.error.HTTPError(
        url="http://test/v1/chat/completions",
        code=code,
        msg=msg,
        hdrs=None,
        fp=BytesIO(b"{}"),
    )


# ── Construction / config ───────────────────────────────────────────────


def test_gemma_default_config_from_env_absent(monkeypatch):
    """Absent env vars → defaults (127.0.0.1, gemma-3-12b-it-AWQ)."""
    monkeypatch.delenv("DEPTHFUSION_GEMMA_URL", raising=False)
    monkeypatch.delenv("DEPTHFUSION_GEMMA_MODEL", raising=False)
    monkeypatch.delenv("DEPTHFUSION_GEMMA_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DEPTHFUSION_GEMMA_MAX_CONCURRENT", raising=False)
    b = GemmaBackend()
    assert b._url == "http://127.0.0.1:8000/v1"
    assert b._model == "google/gemma-3-12b-it-AWQ"
    assert b._timeout == 30.0
    assert b._max_concurrent == 4


def test_gemma_config_from_env_vars(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GEMMA_URL", "http://gex44.local:9000/v1/")
    monkeypatch.setenv("DEPTHFUSION_GEMMA_MODEL", "google/gemma-3-27b")
    monkeypatch.setenv("DEPTHFUSION_GEMMA_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("DEPTHFUSION_GEMMA_MAX_CONCURRENT", "8")
    b = GemmaBackend()
    # Trailing slash stripped
    assert b._url == "http://gex44.local:9000/v1"
    assert b._model == "google/gemma-3-27b"
    assert b._timeout == 60.0
    assert b._max_concurrent == 8


def test_gemma_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GEMMA_URL", "http://should-not-win:8000/v1")
    b = GemmaBackend(url="http://explicit:9000/v1", model="mistral", timeout=10.0, max_concurrent=2)
    assert b._url == "http://explicit:9000/v1"
    assert b._model == "mistral"
    assert b._timeout == 10.0
    assert b._max_concurrent == 2


def test_gemma_invalid_env_timeout_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GEMMA_TIMEOUT_SECONDS", "not-a-number")
    b = GemmaBackend()
    assert b._timeout == 30.0


def test_gemma_invalid_env_max_concurrent_falls_back(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_GEMMA_MAX_CONCURRENT", "banana")
    b = GemmaBackend()
    assert b._max_concurrent == 4


def test_gemma_name_is_gemma():
    assert GemmaBackend().name == "gemma"


# ── healthy() ────────────────────────────────────────────────────────────


def test_gemma_healthy_when_url_and_model_configured():
    assert GemmaBackend().healthy() is True


def test_gemma_unhealthy_when_url_explicitly_empty():
    b = GemmaBackend(url="")
    assert b.healthy() is False


def test_gemma_unhealthy_when_model_explicitly_empty():
    b = GemmaBackend(model="")
    assert b.healthy() is False


def test_gemma_healthy_makes_no_network_call():
    """Protocol contract: healthy() must not make network calls."""
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        b = GemmaBackend()
        b.healthy()
        mock_open.assert_not_called()


# ── complete() happy + payload ───────────────────────────────────────────


def test_complete_returns_assistant_text():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response("The answer is 42.")
        b = GemmaBackend()
        assert b.complete("What is life?", max_tokens=100) == "The answer is 42."


def test_complete_posts_openai_compatible_payload():
    """Verifies the exact payload shape sent to vLLM: model + messages +
    max_tokens; user-role wrapping when no system prompt.
    """
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response("ok")
        b = GemmaBackend(url="http://test/v1", model="gemma-test")
        b.complete("hello", max_tokens=50)

        req = mock_open.call_args.args[0]
        assert req.full_url == "http://test/v1/chat/completions"
        assert req.get_method() == "POST"
        body = json.loads(req.data.decode("utf-8"))
        assert body["model"] == "gemma-test"
        assert body["max_tokens"] == 50
        assert body["messages"] == [{"role": "user", "content": "hello"}]


def test_complete_forwards_system_prompt_as_first_message():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response("ok")
        b = GemmaBackend()
        b.complete("hello", max_tokens=50, system="You are terse.")

        body = json.loads(mock_open.call_args.args[0].data.decode("utf-8"))
        assert body["messages"] == [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "hello"},
        ]


def test_complete_returns_empty_when_response_has_no_choices():
    empty_resp = MagicMock()
    empty_resp.read.return_value = b'{"choices": []}'
    empty_resp.__enter__ = MagicMock(return_value=empty_resp)
    empty_resp.__exit__ = MagicMock(return_value=False)
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = empty_resp
        b = GemmaBackend()
        assert b.complete("prompt", max_tokens=50) == ""


def test_complete_returns_empty_on_malformed_json_response():
    bad_resp = MagicMock()
    bad_resp.read.return_value = b"not json"
    bad_resp.__enter__ = MagicMock(return_value=bad_resp)
    bad_resp.__exit__ = MagicMock(return_value=False)
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = bad_resp
        b = GemmaBackend()
        assert b.complete("prompt", max_tokens=50) == ""


def test_complete_sets_content_type_header():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response("ok")
        b = GemmaBackend()
        b.complete("hello", max_tokens=50)
        req = mock_open.call_args.args[0]
        # Request.headers stores capitalised header names
        assert req.headers.get("Content-type") == "application/json"


def test_complete_uses_configured_timeout():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response("ok")
        b = GemmaBackend(timeout=7.5)
        b.complete("prompt", max_tokens=10)
        assert mock_open.call_args.kwargs["timeout"] == 7.5


# ── Typed-error translation ──────────────────────────────────────────────


def test_complete_translates_429_to_rate_limit():
    """AC-01-4 parity: HTTP 429 → RateLimitError propagated."""
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = _http_error(429, "Too Many Requests")
        b = GemmaBackend()
        with pytest.raises(RateLimitError):
            b.complete("prompt", max_tokens=50)


def test_complete_translates_503_to_overload():
    """vLLM returns 503 when capacity-exhausted — we treat it as overload."""
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = _http_error(503, "Service Unavailable")
        b = GemmaBackend()
        with pytest.raises(BackendOverloadError):
            b.complete("prompt", max_tokens=50)


def test_complete_translates_529_to_overload():
    """529 is Anthropic's overload code; GemmaBackend accepts it for
    cross-backend error-class uniformity.
    """
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = _http_error(529, "Overloaded")
        b = GemmaBackend()
        with pytest.raises(BackendOverloadError):
            b.complete("prompt", max_tokens=50)


def test_complete_translates_urllib_timeout_to_backend_timeout():
    """socket.timeout wrapped in URLError → BackendTimeoutError."""
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = urllib.error.URLError(socket.timeout("timed out"))
        b = GemmaBackend()
        with pytest.raises(BackendTimeoutError):
            b.complete("prompt", max_tokens=50)


def test_complete_translates_direct_timeout_error_to_backend_timeout():
    """Raw socket.timeout without URLError wrapping still translates."""
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = socket.timeout("timed out")
        b = GemmaBackend()
        with pytest.raises(BackendTimeoutError):
            b.complete("prompt", max_tokens=50)


def test_complete_translates_timeout_error_class():
    """Python's built-in TimeoutError → BackendTimeoutError."""
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = TimeoutError("timed out")
        b = GemmaBackend()
        with pytest.raises(BackendTimeoutError):
            b.complete("prompt", max_tokens=50)


def test_complete_returns_empty_on_non_translatable_exception():
    """Non-HTTP, non-timeout errors (e.g. unexpected RuntimeError) swallow
    to empty string — preserves graceful-degradation contract for the
    call-site's own try/except pattern.
    """
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = RuntimeError("unexpected")
        b = GemmaBackend()
        assert b.complete("prompt", max_tokens=50) == ""


def test_complete_preserves_500_errors_as_generic():
    """5xx responses other than 503/529 are generic server errors —
    they fall through to the 'unexpected exception' safe-degenerate path,
    not the typed-error path (callers won't drive fallback on generic 500s
    because they're non-deterministic, unlike 429/529 which have clear
    retry semantics).
    """
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = _http_error(500, "Internal Server Error")
        b = GemmaBackend()
        # Generic 500 → non-translatable → returns empty via complete()'s
        # broad except (not raised as a typed error)
        assert b.complete("prompt", max_tokens=50) == ""


# ── embed() always returns None ─────────────────────────────────────────


def test_gemma_embed_returns_none():
    """vps-gpu routes embedding → LocalEmbeddingBackend; GemmaBackend
    explicitly declines the capability.
    """
    assert GemmaBackend().embed(["any", "text"]) is None


def test_gemma_embed_makes_no_network_call():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        GemmaBackend().embed(["text"])
        mock_open.assert_not_called()


# ── rerank() ────────────────────────────────────────────────────────────


def test_rerank_empty_docs_returns_empty():
    """No HTTP call should occur for empty docs."""
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        result = GemmaBackend().rerank("q", [], top_k=5)
        assert result == []
        mock_open.assert_not_called()


def test_rerank_parses_json_indices():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response("[2, 0, 1]")
        b = GemmaBackend()
        result = b.rerank("query", ["alpha", "beta", "gamma"], top_k=3)
    assert [r[0] for r in result] == [2, 0, 1]
    assert result[0][1] == pytest.approx(1.0)
    assert result[1][1] == pytest.approx(0.95)
    assert result[2][1] == pytest.approx(0.90)


def test_rerank_respects_top_k():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response("[0, 1, 2, 3]")
        b = GemmaBackend()
        assert len(b.rerank("q", ["a", "b", "c", "d"], top_k=2)) == 2


def test_rerank_degenerate_on_invalid_json():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response("not json")
        b = GemmaBackend()
        assert b.rerank("q", ["a", "b"], top_k=2) == [(0, 0.0), (1, 0.0)]


def test_rerank_propagates_rate_limit():
    """AC-01-4 parity: typed errors propagate from rerank() too."""
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = _http_error(429, "Too Many Requests")
        b = GemmaBackend()
        with pytest.raises(RateLimitError):
            b.rerank("q", ["a", "b"], top_k=2)


# ── extract_structured() ────────────────────────────────────────────────


def test_extract_structured_parses_valid_json():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response('{"name": "alpha", "count": 3}')
        b = GemmaBackend()
        result = b.extract_structured(
            "Extract entity",
            {"type": "object", "properties": {"name": {"type": "string"}}},
        )
    assert result == {"name": "alpha", "count": 3}


def test_extract_structured_handles_code_fence():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response('```json\n{"wrapped": true}\n```')
        b = GemmaBackend()
        assert b.extract_structured("prompt", {}) == {"wrapped": True}


def test_extract_structured_returns_none_on_parse_failure():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response("not json")
        b = GemmaBackend()
        assert b.extract_structured("prompt", {}) is None


def test_extract_structured_returns_none_for_non_object():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response('["an", "array"]')
        b = GemmaBackend()
        assert b.extract_structured("prompt", {}) is None


def test_extract_structured_propagates_rate_limit():
    with patch("depthfusion.backends.gemma.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = _http_error(429, "Too Many Requests")
        b = GemmaBackend()
        with pytest.raises(RateLimitError):
            b.extract_structured("prompt", {})
