"""Tests for _check_backend_health startup warning (silent-degradation detection).

When DEPTHFUSION_MODE is vps-cpu or vps-gpu but the required backends are
unhealthy (missing API key / SDK), _check_backend_health must emit a WARNING-
level log so users see the degradation in MCP server stderr output.
"""
from __future__ import annotations

import logging
from unittest.mock import patch, MagicMock

import pytest

from depthfusion.mcp.server import _check_backend_health
from depthfusion.backends.null import NullBackend


class TestCheckBackendHealth:
    def test_local_mode_does_nothing(self, caplog):
        with caplog.at_level(logging.WARNING, logger="depthfusion.mcp.server"):
            _check_backend_health("local")
        assert not caplog.records

    def test_vps_cpu_healthy_backends_no_warning(self, caplog):
        mock_backend = MagicMock()
        mock_backend.__class__ = MagicMock  # not NullBackend

        with patch(
            "depthfusion.backends.factory.get_backend",
            return_value=mock_backend,
        ), caplog.at_level(logging.WARNING, logger="depthfusion.mcp.server"):
            _check_backend_health("vps-cpu")
        assert not any("SILENT DEGRADATION" in r.message for r in caplog.records)

    def test_vps_cpu_null_backends_emits_warning(self, caplog):
        with patch(
            "depthfusion.backends.factory._try_construct",
            return_value=None,  # all backends unhealthy → NullBackend
        ), caplog.at_level(logging.WARNING, logger="depthfusion.mcp.server"):
            _check_backend_health("vps-cpu")

        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("SILENT DEGRADATION" in m for m in warning_msgs), (
            "Expected SILENT DEGRADATION warning when vps-cpu backends are NullBackend"
        )
        assert any("DEPTHFUSION_API_KEY" in m for m in warning_msgs), (
            "Expected API key hint in warning message"
        )

    def test_vps_gpu_null_backends_emits_warning(self, caplog):
        with patch(
            "depthfusion.backends.factory._try_construct",
            return_value=None,
        ), caplog.at_level(logging.WARNING, logger="depthfusion.mcp.server"):
            _check_backend_health("vps-gpu")

        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("SILENT DEGRADATION" in m for m in warning_msgs)
        assert any("DEPTHFUSION_GEMMA_URL" in m for m in warning_msgs), (
            "Expected Gemma URL hint in warning for vps-gpu"
        )

    def test_exception_in_health_check_does_not_raise(self, caplog):
        with patch(
            "depthfusion.backends.factory.get_backend",
            side_effect=RuntimeError("unexpected"),
        ), caplog.at_level(logging.DEBUG, logger="depthfusion.mcp.server"):
            # Must not raise — startup health check is best-effort
            _check_backend_health("vps-cpu")
