"""Tests for DistillationClient — E-68 S-228 T-787.

Covers:
  - local-up: probe succeeds → uses local backend
  - local-down → haiku fallback (auto mode)
  - explicit-haiku override
  - explicit-local override (no probe needed)
  - status reporting in _tool_status
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from depthfusion.cognitive.distillation_client import DistillationClient
from depthfusion.core.config import DepthFusionConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs) -> DepthFusionConfig:
    """Return a minimal DepthFusionConfig with distillation overrides."""
    return DepthFusionConfig(
        distillation_backend=kwargs.get("distillation_backend", "auto"),
        local_llm_url=kwargs.get("local_llm_url", "http://127.0.0.1:11434/v1"),
    )


# ---------------------------------------------------------------------------
# _probe_local tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_local_returns_true_when_reachable():
    """_probe_local returns True when the /models endpoint responds 2xx."""
    config = _make_config(local_llm_url="http://127.0.0.1:11434/v1")
    client = DistillationClient(config)

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_httpx_client = AsyncMock()
    mock_httpx_client.__aenter__ = AsyncMock(return_value=mock_httpx_client)
    mock_httpx_client.__aexit__ = AsyncMock(return_value=False)
    mock_httpx_client.get = AsyncMock(return_value=mock_response)

    with patch("depthfusion.cognitive.distillation_client._HTTPX_IMPORTABLE", True), \
         patch("depthfusion.cognitive.distillation_client.httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value = mock_httpx_client
        result = await client._probe_local()

    assert result is True


@pytest.mark.asyncio
async def test_probe_local_returns_false_on_connection_error():
    """_probe_local returns False when the endpoint raises a connection error."""
    config = _make_config(local_llm_url="http://127.0.0.1:11434/v1")
    client = DistillationClient(config)

    mock_httpx_client = AsyncMock()
    mock_httpx_client.__aenter__ = AsyncMock(return_value=mock_httpx_client)
    mock_httpx_client.__aexit__ = AsyncMock(return_value=False)
    mock_httpx_client.get = AsyncMock(side_effect=ConnectionRefusedError("refused"))

    with patch("depthfusion.cognitive.distillation_client._HTTPX_IMPORTABLE", True), \
         patch("depthfusion.cognitive.distillation_client.httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value = mock_httpx_client
        result = await client._probe_local()

    assert result is False


@pytest.mark.asyncio
async def test_probe_local_returns_false_when_httpx_missing():
    """_probe_local returns False gracefully when httpx is not installed."""
    config = _make_config(local_llm_url="http://127.0.0.1:11434/v1")
    client = DistillationClient(config)

    with patch("depthfusion.cognitive.distillation_client._HTTPX_IMPORTABLE", False), \
         patch("depthfusion.cognitive.distillation_client.httpx", None):
        result = await client._probe_local()

    assert result is False


# ---------------------------------------------------------------------------
# auto backend — local-up path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_auto_uses_local_when_reachable():
    """auto mode: probe succeeds → complete() uses local backend."""
    config = _make_config(
        distillation_backend="auto",
        local_llm_url="http://127.0.0.1:11434/v1",
    )
    client = DistillationClient(config)

    with patch.object(client, "_probe_local", new=AsyncMock(return_value=True)), \
         patch.object(client, "_complete_local", new=AsyncMock(return_value="local result")), \
         patch.object(client, "_complete_haiku", new=AsyncMock(return_value="haiku result")):
        result = await client.complete("hello")

    assert result == "local result"
    assert client.resolved_backend() == "local"


# ---------------------------------------------------------------------------
# auto backend — local-down → haiku fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_auto_falls_back_to_haiku_when_local_unreachable():
    """auto mode: probe fails → complete() falls back to haiku."""
    config = _make_config(
        distillation_backend="auto",
        local_llm_url="http://127.0.0.1:11434/v1",
    )
    client = DistillationClient(config)

    with patch.object(client, "_probe_local", new=AsyncMock(return_value=False)), \
         patch.object(client, "_complete_local", new=AsyncMock(return_value="local result")), \
         patch.object(client, "_complete_haiku", new=AsyncMock(return_value="haiku fallback")):
        result = await client.complete("hello")

    assert result == "haiku fallback"
    assert client.resolved_backend() == "haiku"


@pytest.mark.asyncio
async def test_complete_auto_falls_back_to_haiku_when_no_local_url():
    """auto mode with no local_llm_url → probe never called → haiku."""
    config = _make_config(
        distillation_backend="auto",
        local_llm_url="",  # empty
    )
    client = DistillationClient(config)

    with patch.object(client, "_probe_local", new=AsyncMock(return_value=False)) as mock_probe, \
         patch.object(client, "_complete_haiku", new=AsyncMock(return_value="haiku only")):
        result = await client.complete("hello")

    # probe should not be called because local_llm_url is empty
    mock_probe.assert_not_called()
    assert result == "haiku only"
    assert client.resolved_backend() == "haiku"


# ---------------------------------------------------------------------------
# explicit haiku override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_haiku_backend_skips_probe():
    """distillation_backend=haiku: probe is never called; haiku backend used."""
    config = _make_config(
        distillation_backend="haiku",
        local_llm_url="http://127.0.0.1:11434/v1",
    )
    client = DistillationClient(config)

    with patch.object(client, "_probe_local", new=AsyncMock()) as mock_probe, \
         patch.object(client, "_complete_haiku", new=AsyncMock(return_value="explicit haiku")):
        result = await client.complete("test prompt")

    mock_probe.assert_not_called()
    assert result == "explicit haiku"
    assert client.resolved_backend() == "haiku"


# ---------------------------------------------------------------------------
# explicit local override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_local_backend_skips_probe():
    """distillation_backend=local: probe is never called; local backend used directly."""
    config = _make_config(
        distillation_backend="local",
        local_llm_url="http://127.0.0.1:11434/v1",
    )
    client = DistillationClient(config)

    with patch.object(client, "_probe_local", new=AsyncMock()) as mock_probe, \
         patch.object(client, "_complete_local", new=AsyncMock(return_value="explicit local")):
        result = await client.complete("test prompt")

    mock_probe.assert_not_called()
    assert result == "explicit local"
    assert client.resolved_backend() == "local"


@pytest.mark.asyncio
async def test_explicit_local_without_url_falls_back_to_haiku():
    """distillation_backend=local but local_llm_url is empty → warn + use haiku."""
    config = _make_config(
        distillation_backend="local",
        local_llm_url="",
    )
    client = DistillationClient(config)

    with patch.object(client, "_complete_haiku", new=AsyncMock(return_value="fallback haiku")):
        result = await client.complete("test prompt")

    assert result == "fallback haiku"
    assert client.resolved_backend() == "haiku"


# ---------------------------------------------------------------------------
# _complete_local internals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_local_parses_openai_compat_response():
    """_complete_local extracts text from an OpenAI-compat JSON response."""
    config = _make_config(local_llm_url="http://127.0.0.1:11434/v1")
    client = DistillationClient(config)

    fake_response_body = {
        "choices": [{"message": {"role": "assistant", "content": "hello from local"}}]
    }

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = fake_response_body

    mock_http_client = AsyncMock()
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)
    mock_http_client.post = AsyncMock(return_value=mock_response)

    with patch("depthfusion.cognitive.distillation_client._HTTPX_IMPORTABLE", True), \
         patch("depthfusion.cognitive.distillation_client.httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value = mock_http_client
        result = await client._complete_local("what is the capital of France?")

    assert result == "hello from local"


# ---------------------------------------------------------------------------
# _tool_status reporting (AC-4)
# ---------------------------------------------------------------------------

def test_tool_status_reports_distillation_backend():
    """_tool_status includes a 'distillation' dict with backend and local URL."""
    from depthfusion.mcp.tools.system import _tool_status

    config = DepthFusionConfig(
        distillation_backend="auto",
        local_llm_url="http://127.0.0.1:11434/v1",
    )
    raw = _tool_status(config)
    data = json.loads(raw)

    assert "distillation" in data
    dist = data["distillation"]
    assert "configured_backend" in dist
    assert "local_llm_url" in dist
    assert "resolved_backend" in dist
    assert dist["configured_backend"] == "auto"
    assert dist["local_llm_url"] == "http://127.0.0.1:11434/v1"
    # resolved_backend must always be a concrete value — never "auto ..."
    assert dist["resolved_backend"] in ("local", "haiku")


def test_tool_status_auto_mode_resolves_local_when_reachable():
    """_tool_status auto mode: resolved_backend='local' when sync probe succeeds."""
    from unittest.mock import patch

    from depthfusion.mcp.tools.system import _tool_status

    config = DepthFusionConfig(
        distillation_backend="auto",
        local_llm_url="http://127.0.0.1:11434/v1",
    )
    with patch("socket.create_connection"):
        raw = _tool_status(config)
    data = json.loads(raw)
    assert data["distillation"]["resolved_backend"] == "local"


def test_tool_status_auto_mode_resolves_haiku_when_unreachable():
    """_tool_status auto mode: resolved_backend='haiku' when sync probe fails."""
    from unittest.mock import patch

    from depthfusion.mcp.tools.system import _tool_status

    config = DepthFusionConfig(
        distillation_backend="auto",
        local_llm_url="http://127.0.0.1:11434/v1",
    )
    with patch("socket.create_connection", side_effect=OSError("refused")):
        raw = _tool_status(config)
    data = json.loads(raw)
    assert data["distillation"]["resolved_backend"] == "haiku"


def test_tool_status_reports_explicit_haiku():
    """_tool_status resolved_backend reflects 'haiku' when explicitly set."""
    from depthfusion.mcp.tools.system import _tool_status

    config = DepthFusionConfig(
        distillation_backend="haiku",
        local_llm_url="",
    )
    raw = _tool_status(config)
    data = json.loads(raw)

    dist = data["distillation"]
    assert dist["configured_backend"] == "haiku"
    assert dist["resolved_backend"] == "haiku"


def test_tool_status_reports_explicit_local():
    """_tool_status resolved_backend reflects 'local' when explicitly set."""
    from depthfusion.mcp.tools.system import _tool_status

    config = DepthFusionConfig(
        distillation_backend="local",
        local_llm_url="http://127.0.0.1:8000/v1",
    )
    raw = _tool_status(config)
    data = json.loads(raw)

    dist = data["distillation"]
    assert dist["configured_backend"] == "local"
    assert dist["resolved_backend"] == "local"
    assert dist["local_llm_url"] == "http://127.0.0.1:8000/v1"
