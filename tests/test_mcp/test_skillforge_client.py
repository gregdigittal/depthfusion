"""Coverage for depthfusion.mcp.skillforge_client (lines 47-86).

Lines 40-45 (no-URL early exit) are already covered by default env.
This file covers the "URL is set" branch: payload construction, header auth,
successful response parsing, and all retry/error paths.
"""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from depthfusion.mcp.skillforge_client import post_skill_draft


def _fake_resp(data: dict) -> MagicMock:
    """MagicMock context manager whose .read() returns serialised data.

    __enter__ must return *self* so that `with urlopen(...) as resp:` binds
    `resp` to the same mock object that has `.read()` configured.
    """
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.__enter__.return_value = resp
    return resp


# ── no-URL guard (lines 40-45) ────────────────────────────────────────────────

def test_no_url_returns_none(monkeypatch):
    """DEPTHFUSION_SKILLFORGE_URL unset → immediate None (no HTTP call)."""
    monkeypatch.delenv("DEPTHFUSION_SKILLFORGE_URL", raising=False)
    result = post_skill_draft("skill", "desc", "pkey", 3)
    assert result is None


# ── success path (lines 47-67) ────────────────────────────────────────────────

def test_success_returns_parsed_dict(monkeypatch):
    """URL set, urlopen succeeds → parsed JSON dict returned (lines 47-67)."""
    monkeypatch.setenv("DEPTHFUSION_SKILLFORGE_URL", "http://localhost:9999")
    monkeypatch.delenv("DEPTHFUSION_SKILLFORGE_API_KEY", raising=False)
    with patch("urllib.request.urlopen", return_value=_fake_resp({"id": "skill-123"})):
        result = post_skill_draft("My Skill", "desc", "pattern-key", 5)
    assert result is not None
    assert result["id"] == "skill-123"


def test_success_with_api_key(monkeypatch):
    """api_key present → Authorization header path taken (line 59-60)."""
    monkeypatch.setenv("DEPTHFUSION_SKILLFORGE_URL", "http://localhost:9999")
    monkeypatch.setenv("DEPTHFUSION_SKILLFORGE_API_KEY", "tok-secret")
    with patch("urllib.request.urlopen", return_value=_fake_resp({"id": "y", "status": "ok"})):
        result = post_skill_draft("s", "d", "p", 1)
    assert result is not None
    assert result["id"] == "y"


# ── HTTPError retry path (lines 68-80, 82-86) ────────────────────────────────

def test_http_error_all_attempts_returns_none(monkeypatch):
    """HTTPError on every attempt → None after 3 retries (lines 68-72, 79-80, 82-86)."""
    monkeypatch.setenv("DEPTHFUSION_SKILLFORGE_URL", "http://localhost:9999")
    monkeypatch.delenv("DEPTHFUSION_SKILLFORGE_API_KEY", raising=False)
    http_err = urllib.error.HTTPError(
        url="http://localhost:9999/skills/draft",
        code=503,
        msg="Service Unavailable",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=http_err), \
         patch("time.sleep"):  # suppress 3 seconds of real sleep
        result = post_skill_draft("s", "d", "p", 1)
    assert result is None


# ── generic Exception retry path (lines 73-80, 82-86) ───────────────────────

def test_generic_exception_all_attempts_returns_none(monkeypatch):
    """Non-HTTP exception on every attempt → None (lines 73-77, 79-80, 82-86)."""
    monkeypatch.setenv("DEPTHFUSION_SKILLFORGE_URL", "http://localhost:9999")
    with patch("urllib.request.urlopen", side_effect=ConnectionError("connection refused")), \
         patch("time.sleep"):
        result = post_skill_draft("s", "d", "p", 1)
    assert result is None
