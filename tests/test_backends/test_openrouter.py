"""Tests for OpenRouterBackend."""
from __future__ import annotations

import json
import socket
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.backends.base import BackendOverloadError, BackendTimeoutError, RateLimitError
from depthfusion.backends.openrouter import OpenRouterBackend

# ---------------------------------------------------------------------------
# healthy() — construction-time gate
# ---------------------------------------------------------------------------


def test_healthy_returns_false_when_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    backend = OpenRouterBackend()
    assert backend.healthy() is False


def test_healthy_returns_true_when_api_key_present(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-key")
    backend = OpenRouterBackend()
    assert backend.healthy() is True


def test_healthy_returns_true_with_explicit_api_key():
    backend = OpenRouterBackend(api_key="sk-explicit-key")
    assert backend.healthy() is True


def test_healthy_false_with_empty_api_key():
    backend = OpenRouterBackend(api_key="")
    assert backend.healthy() is False


# ---------------------------------------------------------------------------
# _post_chat — header injection
# ---------------------------------------------------------------------------


_URLOPEN = "depthfusion.backends.openrouter.urllib.request.urlopen"


def _make_response(body: dict) -> MagicMock:
    raw = json.dumps(body).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = raw
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_post_chat_sends_bearer_auth_header(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-bearer-test")
    backend = OpenRouterBackend()

    captured_headers = {}

    def fake_urlopen(req, timeout=None):
        captured_headers.update(req.headers)
        body = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}]
        }
        return _make_response(body)

    with patch(_URLOPEN, side_effect=fake_urlopen):
        result = backend.complete("hello", max_tokens=64)

    assert result == "ok"
    # urllib capitalises the first letter of each header word
    assert "Authorization" in captured_headers
    assert captured_headers["Authorization"] == "Bearer sk-bearer-test"


def test_post_chat_sends_x_title_header(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-title-test")
    backend = OpenRouterBackend()

    captured_headers = {}

    def fake_urlopen(req, timeout=None):
        captured_headers.update(req.headers)
        body = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        return _make_response(body)

    with patch(_URLOPEN, side_effect=fake_urlopen):
        backend.complete("hello", max_tokens=64)

    assert "X-title" in captured_headers  # urllib normalises to title-case
    assert captured_headers["X-title"] == "DepthFusion"


def test_post_chat_uses_default_openrouter_base_url(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-url-test")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    backend = OpenRouterBackend()

    captured_url = []

    def fake_urlopen(req, timeout=None):
        captured_url.append(req.full_url)
        body = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        return _make_response(body)

    with patch(_URLOPEN, side_effect=fake_urlopen):
        backend.complete("hello", max_tokens=64)

    assert captured_url[0].startswith("https://openrouter.ai/api/v1/")


def test_post_chat_respects_overridden_base_url(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-url-test")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://custom.proxy/v1")
    backend = OpenRouterBackend()

    captured_url = []

    def fake_urlopen(req, timeout=None):
        captured_url.append(req.full_url)
        body = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        return _make_response(body)

    with patch(_URLOPEN, side_effect=fake_urlopen):
        backend.complete("hello", max_tokens=64)

    assert captured_url[0].startswith("https://custom.proxy/v1/")


# ---------------------------------------------------------------------------
# model kwarg threading
# ---------------------------------------------------------------------------


def test_complete_model_kwarg_overrides_instance_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-model-test")
    backend = OpenRouterBackend(model="openai/gpt-4o")

    captured_body: list[dict] = []

    def fake_urlopen(req, timeout=None):
        captured_body.append(json.loads(req.data.decode()))
        body = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        return _make_response(body)

    with patch(_URLOPEN, side_effect=fake_urlopen):
        backend.complete("hello", max_tokens=64, model="google/gemini-1.5-pro")

    assert captured_body[0]["model"] == "google/gemini-1.5-pro"


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def test_429_translates_to_rate_limit_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-err-test")
    backend = OpenRouterBackend()

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    with patch(_URLOPEN, side_effect=fake_urlopen):
        with pytest.raises(RateLimitError):
            backend.complete("hello", max_tokens=64)


def test_503_translates_to_overload_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-err-test")
    backend = OpenRouterBackend()

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

    with patch(_URLOPEN, side_effect=fake_urlopen):
        with pytest.raises(BackendOverloadError):
            backend.complete("hello", max_tokens=64)


def test_timeout_translates_to_backend_timeout_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-err-test")
    backend = OpenRouterBackend()

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError(socket.timeout("timed out"))

    with patch(_URLOPEN, side_effect=fake_urlopen):
        with pytest.raises(BackendTimeoutError):
            backend.complete("hello", max_tokens=64)


# ---------------------------------------------------------------------------
# Backend name and defaults
# ---------------------------------------------------------------------------


def test_backend_name_is_openrouter():
    backend = OpenRouterBackend(api_key="any")
    assert backend.name == "openrouter"


def test_default_model_is_gpt4o(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-model-test")
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    backend = OpenRouterBackend()
    assert "gpt-4o" in backend._model
