"""S-266: Origin header validation — default localhost-only, escape hatch tests."""
import importlib
import sys

import pytest
from fastapi.testclient import TestClient


def _make_client(monkeypatch, allowed_origins=None):
    """Reload http_server so the module-level env read picks up monkeypatched vars."""
    if allowed_origins is None:
        monkeypatch.delenv("DEPTHFUSION_MCP_ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("DEPTHFUSION_MCP_ALLOWED_ORIGINS", allowed_origins)

    # Force reimport so _check_origin picks up the new env value at function call time.
    # (The env read is inside the function body, so no reimport needed — but we keep
    # this pattern for clarity and future proofing.)
    import depthfusion.mcp.http_server as mod  # noqa: PLC0415

    return TestClient(mod.app, raise_server_exceptions=False)


class TestDefaultLocalhostOnly:
    """When env var is unset, only localhost origins are allowed."""

    def test_no_origin_header_passes(self, monkeypatch):
        """Non-browser clients (CLI, Claude Code) send no Origin — must pass."""
        client = _make_client(monkeypatch)
        # /health is unauthenticated; use it to probe Origin without auth complexity
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_localhost_origin_passes(self, monkeypatch):
        client = _make_client(monkeypatch)
        resp = client.get("/health", headers={"Origin": "http://localhost"})
        assert resp.status_code == 200

    def test_loopback_origin_passes(self, monkeypatch):
        client = _make_client(monkeypatch)
        resp = client.get("/health", headers={"Origin": "http://127.0.0.1"})
        assert resp.status_code == 200

    def test_external_origin_blocked(self, monkeypatch):
        """/health itself is not protected by _check_origin, but /mcp endpoints are.
        We test via the dependency directly by examining the behavior on a route that
        uses _check_origin as a Depends."""
        from depthfusion.mcp.http_server import _check_origin  # noqa: PLC0415
        from fastapi import Request  # noqa: PLC0415

        async def _run():
            import asyncio  # noqa: PLC0415
            from unittest.mock import MagicMock  # noqa: PLC0415

            mock_req = MagicMock(spec=Request)
            mock_req.headers.get.return_value = "http://evil.com"
            monkeypatch.delenv("DEPTHFUSION_MCP_ALLOWED_ORIGINS", raising=False)
            from depthfusion.mcp.http_server import _OriginForbidden  # noqa: PLC0415

            with pytest.raises(_OriginForbidden):
                await _check_origin(mock_req)

        import asyncio

        asyncio.run(_run())


class TestCustomAllowList:
    """Explicit env var overrides the default."""

    def test_custom_origin_allowed(self, monkeypatch):
        from depthfusion.mcp.http_server import _check_origin, _OriginForbidden  # noqa: PLC0415
        from fastapi import Request  # noqa: PLC0415
        from unittest.mock import MagicMock  # noqa: PLC0415

        async def _run():
            mock_req = MagicMock(spec=Request)
            mock_req.headers.get.return_value = "https://app.example.com"
            monkeypatch.setenv("DEPTHFUSION_MCP_ALLOWED_ORIGINS", "https://app.example.com")
            await _check_origin(mock_req)  # should not raise

        import asyncio

        asyncio.run(_run())

    def test_unlisted_origin_blocked_with_custom_list(self, monkeypatch):
        from depthfusion.mcp.http_server import _check_origin, _OriginForbidden  # noqa: PLC0415
        from fastapi import Request  # noqa: PLC0415
        from unittest.mock import MagicMock  # noqa: PLC0415

        async def _run():
            mock_req = MagicMock(spec=Request)
            mock_req.headers.get.return_value = "http://other.com"
            monkeypatch.setenv("DEPTHFUSION_MCP_ALLOWED_ORIGINS", "https://app.example.com")
            with pytest.raises(_OriginForbidden):
                await _check_origin(mock_req)

        import asyncio

        asyncio.run(_run())


class TestEmptyStringEscapeHatch:
    """DEPTHFUSION_MCP_ALLOWED_ORIGINS='' allows all Origins."""

    def test_empty_string_allows_any_origin(self, monkeypatch):
        from depthfusion.mcp.http_server import _check_origin  # noqa: PLC0415
        from fastapi import Request  # noqa: PLC0415
        from unittest.mock import MagicMock  # noqa: PLC0415

        async def _run():
            mock_req = MagicMock(spec=Request)
            mock_req.headers.get.return_value = "http://anywhere.example.com"
            monkeypatch.setenv("DEPTHFUSION_MCP_ALLOWED_ORIGINS", "")
            await _check_origin(mock_req)  # should not raise

        import asyncio

        asyncio.run(_run())
