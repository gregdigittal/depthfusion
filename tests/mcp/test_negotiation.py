"""S-267: MCP protocol negotiation tests — stdio and HTTP layers."""
import json
import sys
from io import StringIO
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# stdio — _process_request direct calls
# ---------------------------------------------------------------------------


class TestStdioNegotiation:
    """Test the stdio JSON-RPC initialize/tools flow via _process_request."""

    def _process(self, request: dict) -> dict | None:
        from depthfusion.mcp.server import _process_request  # noqa: PLC0415

        return _process_request(request, config=None)

    def test_initialize_returns_server_info(self):
        resp = self._process({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0.0.1"},
            },
        })
        assert resp is not None
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        result = resp["result"]
        assert "protocolVersion" in result
        assert "serverInfo" in result

    def test_initialize_unknown_version_still_responds(self):
        """stdio server currently accepts any protocolVersion (documented limitation)."""
        resp = self._process({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {
                "protocolVersion": "1999-01-01",  # unknown version
                "capabilities": {},
                "clientInfo": {"name": "old-client", "version": "0.0.1"},
            },
        })
        # Known limitation: no version guard yet — server returns success.
        # TODO: add a negotiation error when version is not in supported list.
        assert resp is not None
        assert "result" in resp or "error" in resp

    def test_tools_call_without_initialize_succeeds(self):
        """stdio is a single-session pipe; no session guard needed."""
        resp = self._process({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list",
            "params": {},
        })
        assert resp is not None
        assert "result" in resp
        assert "tools" in resp["result"]


# ---------------------------------------------------------------------------
# HTTP — FastAPI TestClient
# ---------------------------------------------------------------------------


class TestHttpNegotiation:
    """Test MCP HTTP transport protocol-version handling."""

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient  # noqa: PLC0415
        import depthfusion.mcp.http_server as _mod  # noqa: PLC0415
        from depthfusion.identity.models import Principal  # noqa: PLC0415

        # Override FastAPI's dependency injection so auth is bypassed
        _fake_principal = Principal(principal_id="test")

        async def _noop_auth():
            return _fake_principal

        _mod.app.dependency_overrides[_mod.require_principal] = _noop_auth
        try:
            with TestClient(_mod.app, raise_server_exceptions=False) as c:
                yield c
        finally:
            _mod.app.dependency_overrides.clear()

    def test_valid_protocol_version_accepted(self, client):
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.0.1"},
                },
            },
            headers={"mcp-protocol-version": "2025-03-26"},
        )
        # 200 or 202 — the server accepted the request
        assert resp.status_code in (200, 201, 202)

    def test_unknown_protocol_version_header_rejected(self, client):
        """HTTP layer validates mcp-protocol-version header."""
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {}},
            },
            headers={"mcp-protocol-version": "1999-01-01"},
        )
        assert resp.status_code in (400, 404, 422)

    def test_unknown_session_id_rejected(self, client):
        """A request referencing a nonexistent session must fail gracefully."""
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "mcp-protocol-version": "2025-03-26",
                "mcp-session-id": "nonexistent-session-id-xyz",
            },
        )
        assert resp.status_code in (400, 404)

    def test_sse_without_prior_initialize_requires_session(self, client):
        """GET /mcp (SSE) without a prior POST session returns 400.
        The server requires POST /mcp → initialize before opening the SSE stream."""
        resp = client.get(
            "/mcp",
            headers={"mcp-protocol-version": "2025-03-26"},
        )
        assert resp.status_code == 400
