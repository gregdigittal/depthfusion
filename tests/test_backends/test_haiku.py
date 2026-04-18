# tests/test_backends/test_haiku.py
"""HaikuBackend behaviour tests (mocked Anthropic SDK).

Covers:
  - Availability gating on DEPTHFUSION_API_KEY presence + SDK import
  - C2 fix: explicit api_key= to SDK constructor (never ANTHROPIC_API_KEY)
  - complete() / rerank() / extract_structured() happy paths
  - Typed-error translation: 429 / 529 / timeout
  - Graceful degradation: parse errors return safe-degenerate, NOT raise
  - embed() always returns None (Anthropic has no embeddings endpoint)

Backlog: T-116, T-122.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from depthfusion.backends.base import (
    BackendOverloadError,
    BackendTimeoutError,
    RateLimitError,
)
from depthfusion.backends.haiku import HaikuBackend

# ── Availability / construction ──────────────────────────────────────────


def test_haiku_unavailable_without_api_key(monkeypatch):
    """No DEPTHFUSION_API_KEY, no ANTHROPIC_API_KEY → unhealthy."""
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    backend = HaikuBackend()
    assert backend.healthy() is False


def test_haiku_ignores_anthropic_api_key_env_var(monkeypatch):
    """C2 fix: HaikuBackend reads DEPTHFUSION_API_KEY only — setting
    ANTHROPIC_API_KEY must NOT enable it, otherwise the billing-isolation
    guarantee (project convention) is broken.
    """
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-used")
    backend = HaikuBackend()
    assert backend.healthy() is False, (
        "HaikuBackend must NOT fall back to ANTHROPIC_API_KEY; "
        "the C2 fix requires DEPTHFUSION_API_KEY only."
    )


def test_haiku_healthy_when_depthfusion_api_key_set(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-ant-test-key")
    backend = HaikuBackend()
    assert backend.healthy() is True


def test_haiku_explicit_api_key_takes_precedence(monkeypatch):
    """Caller-supplied api_key overrides env var — used by tests and
    alternative credential sources.
    """
    monkeypatch.setenv("DEPTHFUSION_API_KEY", "env-key")
    with patch("depthfusion.backends.haiku.anthropic.Anthropic") as mock_ctor:
        HaikuBackend(api_key="explicit-key")
        mock_ctor.assert_called_once()
        assert mock_ctor.call_args.kwargs["api_key"] == "explicit-key"


def test_haiku_passes_explicit_api_key_not_env_default(monkeypatch):
    """Regression for C2: the SDK constructor is called with api_key=.
    (Otherwise the SDK would fall back to reading ANTHROPIC_API_KEY from env.)
    """
    monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-depthfusion-only")
    with patch("depthfusion.backends.haiku.anthropic.Anthropic") as mock_ctor:
        HaikuBackend()
        assert "api_key" in mock_ctor.call_args.kwargs
        assert mock_ctor.call_args.kwargs["api_key"] == "sk-depthfusion-only"


def test_haiku_name_is_haiku():
    assert HaikuBackend().name == "haiku"


# ── embed() always returns None ──────────────────────────────────────────


def test_haiku_embed_returns_none(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-test")
    assert HaikuBackend().embed(["a", "b"]) is None


def test_haiku_embed_returns_none_even_when_unavailable(monkeypatch):
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    assert HaikuBackend().embed(["text"]) is None


# ── complete() ───────────────────────────────────────────────────────────


def _make_haiku_with_mock_client(monkeypatch):
    """Build a HaikuBackend whose `_client.messages.create` is a MagicMock
    the caller can configure.
    """
    monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-test")
    backend = HaikuBackend()
    backend._client = MagicMock()
    return backend


def test_complete_returns_text_from_response(monkeypatch):
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="The answer is 42.")]
    backend._client.messages.create.return_value = mock_msg

    result = backend.complete("What is life?", max_tokens=100)
    assert result == "The answer is 42."


def test_complete_returns_empty_string_when_unavailable(monkeypatch):
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    backend = HaikuBackend()
    assert backend.complete("prompt", max_tokens=100) == ""


def test_complete_forwards_system_prompt_when_given(monkeypatch):
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="ok")]
    backend._client.messages.create.return_value = mock_msg

    backend.complete("prompt", max_tokens=50, system="You are terse.")
    kwargs = backend._client.messages.create.call_args.kwargs
    assert kwargs["system"] == "You are terse."


def test_complete_omits_system_when_not_given(monkeypatch):
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="ok")]
    backend._client.messages.create.return_value = mock_msg

    backend.complete("prompt", max_tokens=50)
    kwargs = backend._client.messages.create.call_args.kwargs
    assert "system" not in kwargs


def test_complete_returns_empty_when_response_has_no_content(monkeypatch):
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = []
    backend._client.messages.create.return_value = mock_msg

    assert backend.complete("prompt", max_tokens=50) == ""


# ── Typed error translation ──────────────────────────────────────────────


def test_complete_translates_rate_limit_error(monkeypatch):
    """AC-01-4: 429 must surface as typed RateLimitError so the fallback
    chain can react. This is the honest-assessment fix — v0.4.x's
    HaikuReranker swallowed 429 silently.
    """
    import anthropic

    backend = _make_haiku_with_mock_client(monkeypatch)
    # anthropic.RateLimitError requires a minimal stub response
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    backend._client.messages.create.side_effect = anthropic.RateLimitError(
        message="Rate limited",
        response=mock_response,
        body=None,
    )

    with pytest.raises(RateLimitError):
        backend.complete("prompt", max_tokens=50)


def test_complete_translates_timeout_error(monkeypatch):
    import anthropic

    backend = _make_haiku_with_mock_client(monkeypatch)
    # APITimeoutError takes a request argument
    mock_request = MagicMock()
    backend._client.messages.create.side_effect = anthropic.APITimeoutError(request=mock_request)

    with pytest.raises(BackendTimeoutError):
        backend.complete("prompt", max_tokens=50)


def test_complete_translates_overload_529(monkeypatch):
    """HTTP 529 → BackendOverloadError (distinct from rate-limit).
    The backend is healthy but temporarily saturated.
    """
    import anthropic

    backend = _make_haiku_with_mock_client(monkeypatch)
    # Build an APIStatusError with status_code=529
    mock_response = MagicMock()
    mock_response.status_code = 529
    err = anthropic.APIStatusError(
        message="Overloaded",
        response=mock_response,
        body=None,
    )
    # Some SDK versions don't set status_code from the response; patch it.
    err.status_code = 529
    backend._client.messages.create.side_effect = err

    with pytest.raises(BackendOverloadError):
        backend.complete("prompt", max_tokens=50)


# ── rerank() ────────────────────────────────────────────────────────────


def test_rerank_empty_docs_returns_empty(monkeypatch):
    backend = _make_haiku_with_mock_client(monkeypatch)
    assert backend.rerank("q", [], top_k=5) == []


def test_rerank_parses_json_indices(monkeypatch):
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="[2, 0, 1]")]
    backend._client.messages.create.return_value = mock_msg

    docs = ["alpha text", "beta text", "gamma text"]
    result = backend.rerank("query", docs, top_k=3)

    # Indices 2, 0, 1 with linear-decay scores
    assert [r[0] for r in result] == [2, 0, 1]
    # Score at rank 0 is 1.0, rank 1 is 0.95, rank 2 is 0.90
    assert result[0][1] == pytest.approx(1.0)
    assert result[1][1] == pytest.approx(0.95)
    assert result[2][1] == pytest.approx(0.90)


def test_rerank_respects_top_k(monkeypatch):
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="[0, 1, 2, 3]")]
    backend._client.messages.create.return_value = mock_msg

    result = backend.rerank("q", ["a", "b", "c", "d"], top_k=2)
    assert len(result) == 2


def test_rerank_dedups_duplicate_indices(monkeypatch):
    """A misbehaving model might return the same index twice; we dedup."""
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="[1, 1, 0]")]
    backend._client.messages.create.return_value = mock_msg

    result = backend.rerank("q", ["a", "b"], top_k=3)
    assert [r[0] for r in result] == [1, 0]  # dedup keeps first occurrence


def test_rerank_filters_out_of_range_indices(monkeypatch):
    """Model might return indices beyond len(docs) — skip them."""
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="[5, 0]")]
    backend._client.messages.create.return_value = mock_msg

    result = backend.rerank("q", ["a", "b"], top_k=3)
    assert [r[0] for r in result] == [0]


def test_rerank_degenerate_on_invalid_json(monkeypatch):
    """If the model returns garbage, fall back to identity ordering rather
    than raise. The caller (e.g., pipeline) expects a list[tuple] — raising
    here would require every caller to wrap in try/except.
    """
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="this is not json")]
    backend._client.messages.create.return_value = mock_msg

    result = backend.rerank("q", ["a", "b", "c"], top_k=2)
    assert result == [(0, 0.0), (1, 0.0)]


def test_rerank_degenerate_when_response_is_not_list(monkeypatch):
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"not": "a list"}')]
    backend._client.messages.create.return_value = mock_msg

    result = backend.rerank("q", ["a", "b"], top_k=2)
    assert result == [(0, 0.0), (1, 0.0)]


def test_rerank_propagates_rate_limit(monkeypatch):
    """Typed errors from the SDK propagate; parse errors degenerate.
    This asymmetry is deliberate per the AC-01-4 contract.
    """
    import anthropic
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    backend._client.messages.create.side_effect = anthropic.RateLimitError(
        message="limited", response=mock_response, body=None,
    )

    with pytest.raises(RateLimitError):
        backend.rerank("q", ["a", "b"], top_k=2)


def test_rerank_degenerate_when_unavailable(monkeypatch):
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    backend = HaikuBackend()
    result = backend.rerank("q", ["a", "b", "c"], top_k=2)
    assert result == [(0, 0.0), (1, 0.0)]


# ── extract_structured() ────────────────────────────────────────────────


def test_extract_structured_parses_valid_json(monkeypatch):
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"name": "alpha", "count": 3}')]
    backend._client.messages.create.return_value = mock_msg

    result = backend.extract_structured(
        "Extract the entity",
        {"type": "object", "properties": {"name": {"type": "string"}}},
    )
    assert result == {"name": "alpha", "count": 3}


def test_extract_structured_handles_markdown_code_fence(monkeypatch):
    """claude-haiku sometimes wraps JSON in ```json ... ``` — we tolerate it."""
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(
        text='```json\n{"wrapped": true}\n```',
    )]
    backend._client.messages.create.return_value = mock_msg

    result = backend.extract_structured("prompt", {})
    assert result == {"wrapped": True}


def test_extract_structured_returns_none_on_parse_failure(monkeypatch):
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="not json at all")]
    backend._client.messages.create.return_value = mock_msg

    assert backend.extract_structured("prompt", {}) is None


def test_extract_structured_returns_none_for_non_object(monkeypatch):
    """Protocol contract: must return dict or None, not list/string."""
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='["not", "an", "object"]')]
    backend._client.messages.create.return_value = mock_msg

    assert backend.extract_structured("prompt", {}) is None


def test_extract_structured_returns_none_when_unavailable(monkeypatch):
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    backend = HaikuBackend()
    assert backend.extract_structured("prompt", {}) is None


def test_extract_structured_propagates_rate_limit(monkeypatch):
    import anthropic
    backend = _make_haiku_with_mock_client(monkeypatch)
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    backend._client.messages.create.side_effect = anthropic.RateLimitError(
        message="limited", response=mock_response, body=None,
    )

    with pytest.raises(RateLimitError):
        backend.extract_structured("prompt", {})
