"""Tests for the SkillForge HTTP path in RLMClient (E-39).

These tests exercise the SkillForge branch without any non-stdlib
dependency — urllib.request.urlopen is mocked directly.
"""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.core.config import DepthFusionConfig
from depthfusion.recursive.client import RLMClient
from depthfusion.recursive.trajectory import RecursiveTrajectory


def _sf_config(**overrides: str) -> DepthFusionConfig:
    """Build a config with all three SkillForge fields populated by default."""
    defaults = {
        "skillforge_api_url": "http://127.0.0.1:3000",
        "skillforge_api_token": "test-token",
        "skillforge_recursive_skill_id": "skill-uuid-123",
    }
    defaults.update(overrides)
    return DepthFusionConfig(**defaults)


def _urlopen_mock(payload: dict) -> MagicMock:
    """Build a context-manager mock mimicking urllib.request.urlopen()."""
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = response
    cm.__exit__.return_value = False
    # urlopen() is also used directly (not as a context manager) in client.py
    # — make the mock usable both ways.
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    response.close = MagicMock()
    return response


def test_is_skillforge_configured_true_when_all_fields_set():
    """All three SF fields non-empty → is_skillforge_configured() is True."""
    config = _sf_config()
    client = RLMClient(config=config)
    assert client.is_skillforge_configured() is True


def test_is_skillforge_configured_false_when_url_missing():
    """Empty skillforge_api_url → is_skillforge_configured() is False."""
    config = _sf_config(skillforge_api_url="")
    client = RLMClient(config=config)
    assert client.is_skillforge_configured() is False


def test_run_via_skillforge_returns_result_on_200():
    """A 200 response with outputPayload.result returns that text."""
    config = _sf_config()
    client = RLMClient(config=config)

    response = _urlopen_mock({"outputPayload": {"result": "found it"}})

    with patch("urllib.request.urlopen", return_value=response):
        result_text, trajectory = client._run_via_skillforge(
            "my query", "content", "breadth_first"
        )

    assert result_text == "found it"
    assert isinstance(trajectory, RecursiveTrajectory)
    assert trajectory.completed is True


def test_run_via_skillforge_raises_on_http_4xx():
    """An HTTP 401 from SkillForge surfaces as ValueError mentioning 401."""
    config = _sf_config()
    client = RLMClient(config=config)

    http_error = urllib.error.HTTPError(
        url="http://127.0.0.1:3000/api/v1/invocations",
        code=401,
        msg="Unauthorized",
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(ValueError, match="401"):
            client._run_via_skillforge("my query", "content", "breadth_first")


def test_run_falls_back_to_rlm_when_sf_not_configured():
    """SF unconfigured + rlm unavailable → run() returns a stub, no crash."""
    config = _sf_config(skillforge_api_url="")
    client = RLMClient(config=config)

    with patch.object(RLMClient, "is_available", return_value=False):
        client._available = False
        result = client.run("query", "content")

    assert isinstance(result, tuple)
    assert len(result) == 2
    result_text, trajectory = result
    assert isinstance(result_text, str)
    assert isinstance(trajectory, RecursiveTrajectory)
