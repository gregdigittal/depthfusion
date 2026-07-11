"""S-225: CacheManager wired into /api/v1/search."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Set auth env BEFORE importing production modules that wire auth at import time.
os.environ.setdefault("DEPTHFUSION_V2_LEGACY_AUTH", "1")
os.environ.setdefault("DEPTHFUSION_API_TOKEN", "test-cache-token")

from depthfusion.mcp.http_server import _check_mcp_auth, _search_cache_key, app  # noqa: E402

_FAKE_RECALL = json.dumps({"blocks": [
    {"chunk_id": "c1", "source": "file.md", "snippet": "hello", "score": 0.9}
]})
_AUTH_HEADERS = {"Authorization": "Bearer test-cache-token"}


@pytest.fixture(autouse=True)
def reset_cache_singleton():
    """Reset the module-level _SEARCH_CACHE between tests."""
    import depthfusion.mcp.http_server as mod
    original = mod._SEARCH_CACHE
    mod._SEARCH_CACHE = None
    yield
    mod._SEARCH_CACHE = original


@pytest.fixture()
def client():
    """TestClient with auth dependency overridden."""
    async def _no_auth() -> None:
        return None

    app.dependency_overrides[_check_mcp_auth] = _no_auth
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    app.dependency_overrides.pop(_check_mcp_auth, None)


def _make_config(cache_enabled: bool):
    from depthfusion.core.config import DepthFusionConfig
    return DepthFusionConfig(cache_enabled=cache_enabled)


def test_cache_disabled_skips_manager(client):
    """When cache_enabled=False the CacheManager is never created."""
    with (
        patch("depthfusion.mcp.http_server.DepthFusionConfig.from_env",
              return_value=_make_config(False)),
        patch("depthfusion.mcp.tools._shared._tool_recall_impl",
              return_value=_FAKE_RECALL) as mock_recall,
    ):
        r = client.post("/api/v1/search", json={"q": "test", "limit": 5})
        assert r.status_code == 200
        assert mock_recall.call_count == 1

        # Second identical call — no cache, so recall fires again
        r2 = client.post("/api/v1/search", json={"q": "test", "limit": 5})
        assert r2.status_code == 200
        assert mock_recall.call_count == 2


def test_cache_hit_skips_recall(client):
    """Second request with same (q, limit) returns from cache without calling recall."""
    with (
        patch("depthfusion.mcp.http_server.DepthFusionConfig.from_env",
              return_value=_make_config(True)),
        patch("depthfusion.mcp.tools._shared._tool_recall_impl",
              return_value=_FAKE_RECALL) as mock_recall,
        patch("depthfusion.mcp.http_server._get_search_cache",
              return_value=_make_in_memory_cache()),
    ):
        r1 = client.post("/api/v1/search", json={"q": "hello", "limit": 5})
        assert r1.status_code == 200
        assert mock_recall.call_count == 1

        r2 = client.post("/api/v1/search", json={"q": "hello", "limit": 5})
        assert r2.status_code == 200
        assert mock_recall.call_count == 1  # still 1 — served from cache

        assert r1.json() == r2.json()


def test_cache_miss_on_different_query(client):
    """Different queries each hit recall (different cache keys)."""
    with (
        patch("depthfusion.mcp.http_server.DepthFusionConfig.from_env",
              return_value=_make_config(True)),
        patch("depthfusion.mcp.tools._shared._tool_recall_impl",
              return_value=_FAKE_RECALL) as mock_recall,
        patch("depthfusion.mcp.http_server._get_search_cache",
              return_value=_make_in_memory_cache()),
    ):
        client.post("/api/v1/search", json={"q": "alpha", "limit": 5})
        client.post("/api/v1/search", json={"q": "beta", "limit": 5})
        assert mock_recall.call_count == 2


def test_cache_key_deterministic():
    """Same inputs always produce the same cache key."""
    k1 = _search_cache_key("hello world", 10)
    k2 = _search_cache_key("hello world", 10)
    assert k1 == k2
    assert k1.startswith("search/")


def test_cache_key_differentiates_limit():
    """limit=5 and limit=10 should produce different cache keys."""
    assert _search_cache_key("q", 5) != _search_cache_key("q", 10)


def _make_in_memory_cache():
    """CacheManager backed by :memory: SQLite — no disk, no key needed."""
    from depthfusion.cache.manager import CacheManager
    return CacheManager(db_path=":memory:")
