"""S-259 (T-879/T-880/T-881) — stdio error-handling hardening.

Tests that the main() request loop emits JSON-RPC error responses instead of
silently dropping requests when an unhandled exception occurs, and that
non-dict params are caught early with -32602 before reaching .get() calls.

Three scenarios:
  1. Exception in _process_request → -32603 error on stdout (T-879/T-880)
  2. Non-dict params on tools/call → -32602 from _process_request (T-881)
  3. BrokenPipeError → clean exit code 0 (subprocess smoke test)
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_main_patches():
    """Return a dict of context-manager patches that let main() reach the loop.

    Mocks out startup I/O (config load, health check, startup event) without
    touching the request-processing logic under test.
    """
    return {
        "depthfusion.mcp.server._emit_startup_event": patch(
            "depthfusion.mcp.server._emit_startup_event"
        ),
        "depthfusion.mcp.server._check_backend_health": patch(
            "depthfusion.mcp.server._check_backend_health"
        ),
        "depthfusion.mcp.server.get_enabled_tools": patch(
            "depthfusion.mcp.server.get_enabled_tools", return_value=[]
        ),
        "depthfusion.core.config.DepthFusionConfig.from_env": patch(
            "depthfusion.core.config.DepthFusionConfig.from_env",
            return_value=MagicMock(),
        ),
    }


# ---------------------------------------------------------------------------
# Test 1: Exception → -32603 error response
# ---------------------------------------------------------------------------

class TestUnhandledExceptionEmitsErrorResponse:
    """T-879/T-880: an exception inside _process_request must produce a
    JSON-RPC error on stdout — not silence that causes Claude Code to time out.
    """

    def test_error_response_written_to_stdout(self, capsys, monkeypatch):
        request = {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {"name": "depthfusion_status"},
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request) + "\n"))

        patches = _minimal_main_patches()
        with (
            patch("depthfusion.mcp.server._process_request",
                  side_effect=RuntimeError("deliberate boom")),
            patches["depthfusion.mcp.server._emit_startup_event"],
            patches["depthfusion.mcp.server._check_backend_health"],
            patches["depthfusion.mcp.server.get_enabled_tools"],
            patches["depthfusion.core.config.DepthFusionConfig.from_env"],
        ):
            from depthfusion.mcp.server import main
            main()

        captured = capsys.readouterr()
        assert captured.out.strip(), "Expected JSON on stdout, got nothing"
        response = json.loads(captured.out.strip())
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 42
        assert "error" in response
        assert response["error"]["code"] == -32603
        assert "deliberate boom" in response["error"]["message"]

    def test_response_id_matches_request_id(self, capsys, monkeypatch):
        """The id in the error response must echo the id from the request."""
        request = {"jsonrpc": "2.0", "id": "session-xyz", "method": "tools/list"}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request) + "\n"))

        patches = _minimal_main_patches()
        with (
            patch("depthfusion.mcp.server._process_request",
                  side_effect=ValueError("another error")),
            patches["depthfusion.mcp.server._emit_startup_event"],
            patches["depthfusion.mcp.server._check_backend_health"],
            patches["depthfusion.mcp.server.get_enabled_tools"],
            patches["depthfusion.core.config.DepthFusionConfig.from_env"],
        ):
            from depthfusion.mcp.server import main
            main()

        response = json.loads(capsys.readouterr().out.strip())
        assert response["id"] == "session-xyz"

    def test_no_response_emitted_for_request_without_id(self, capsys, monkeypatch):
        """Notifications (no id field) must not emit a response even on exception."""
        request = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request) + "\n"))

        patches = _minimal_main_patches()
        with (
            patch("depthfusion.mcp.server._process_request",
                  side_effect=RuntimeError("should not surface")),
            patches["depthfusion.mcp.server._emit_startup_event"],
            patches["depthfusion.mcp.server._check_backend_health"],
            patches["depthfusion.mcp.server.get_enabled_tools"],
            patches["depthfusion.core.config.DepthFusionConfig.from_env"],
        ):
            from depthfusion.mcp.server import main
            main()

        captured = capsys.readouterr()
        # req_id is None (no "id" key) → no error response should be emitted
        assert captured.out.strip() == "", (
            "Must not emit an error response when the request has no id"
        )


# ---------------------------------------------------------------------------
# Test 2: Non-dict params → -32602 Invalid Params
# ---------------------------------------------------------------------------

class TestNonDictParamsReturnsInvalidParamsError:
    """T-881: params sent as a JSON array (not object) must return -32602
    from _process_request — not crash with AttributeError inside the loop.
    """

    def test_list_params_on_tools_call_returns_32602(self):
        from depthfusion.mcp.server import _process_request

        request = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": [],  # list instead of dict
        }
        response = _process_request(request, config=MagicMock())
        assert "error" in response, "Expected an error response"
        assert response["error"]["code"] == -32602
        assert response["id"] == 7

    def test_string_params_on_tools_call_returns_32602(self):
        from depthfusion.mcp.server import _process_request

        request = {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": "bad",  # string instead of dict
        }
        response = _process_request(request, config=MagicMock())
        assert response["error"]["code"] == -32602
        assert response["id"] == 8

    def test_none_params_on_tools_call_returns_32602(self):
        from depthfusion.mcp.server import _process_request

        request = {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": None,
        }
        response = _process_request(request, config=MagicMock())
        assert response["error"]["code"] == -32602
        assert response["id"] == 9

    def test_dict_params_not_rejected(self):
        """Valid dict params must reach tool dispatch (not return -32602)."""
        from depthfusion.mcp.server import _process_request

        request = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "depthfusion_status", "arguments": {}},
        }
        # Patch _handle_tools_call so we don't need real infra
        with patch("depthfusion.mcp.server._handle_tools_call", return_value={"ok": True}), \
             patch("depthfusion.mcp.server._record_ambient_activity"):
            response = _process_request(request, config=MagicMock())

        assert "error" not in response, (
            "Valid dict params must not produce a -32602 error"
        )
        assert response.get("result") == {"ok": True}


# ---------------------------------------------------------------------------
# Test 3: BrokenPipeError → clean exit (subprocess smoke test)
# ---------------------------------------------------------------------------

class TestBrokenPipeExitsCleanly:
    """T-879: sending one request then closing stdin must result in exit code 0.

    This is a subprocess smoke test — it verifies that the server starts, reads
    at least one line from stdin, and exits cleanly when the pipe closes.
    """

    def test_server_exits_zero_when_stdin_closes(self):
        env = {
            **os.environ,
            # Point to local mode so no VPS connectivity is needed
            "DEPTHFUSION_MODE": "local",
            # Disable optional threads to keep startup fast
            "DEPTHFUSION_AUTONOMIC": "0",
            "DEPTHFUSION_GRAPH_ENABLED": "false",
        }
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "from unittest.mock import patch, MagicMock;"
                    "patches = ["
                    "  patch('depthfusion.mcp.server._emit_startup_event'),"
                    "  patch('depthfusion.mcp.server._check_backend_health'),"
                    "  patch('depthfusion.mcp.server.get_enabled_tools', return_value=[]),"
                    "  patch('depthfusion.core.config.DepthFusionConfig.from_env', return_value=MagicMock()),"
                    "];"
                    "[p.start() for p in patches];"
                    "from depthfusion.mcp.server import main; main()"
                ),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        # Send one valid request, then close stdin to signal EOF
        request = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26"}}
        )
        proc.stdin.write((request + "\n").encode())
        proc.stdin.close()

        try:
            returncode = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("Server did not exit within 10 seconds after stdin closed")

        assert returncode == 0, (
            f"Expected exit code 0, got {returncode}. "
            f"stderr: {proc.stderr.read().decode()[:500]}"
        )
