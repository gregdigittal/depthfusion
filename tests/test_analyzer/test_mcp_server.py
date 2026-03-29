"""Tests for MCP server module."""
from __future__ import annotations

from depthfusion.core.config import DepthFusionConfig
from depthfusion.mcp.server import TOOLS, get_enabled_tools


def test_tools_dict_has_eleven_entries():
    assert len(TOOLS) == 11
    expected = {
        "depthfusion_status",
        "depthfusion_recall_relevant",
        "depthfusion_tag_session",
        "depthfusion_publish_context",
        "depthfusion_run_recursive",
        "depthfusion_tier_status",
        "depthfusion_auto_learn",
        "depthfusion_compress_session",
        "depthfusion_graph_traverse",
        "depthfusion_graph_status",
        "depthfusion_set_scope",
    }
    assert set(TOOLS.keys()) == expected


def test_get_enabled_tools_all_flags_true():
    config = DepthFusionConfig(rlm_enabled=True, router_enabled=True, graph_enabled=True)
    enabled = get_enabled_tools(config)
    assert set(enabled) == set(TOOLS.keys())
    assert len(enabled) == 11


def test_get_enabled_tools_rlm_disabled_excludes_recursive():
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=True)
    enabled = get_enabled_tools(config)
    assert "depthfusion_run_recursive" not in enabled
    assert len(enabled) == 7


def test_get_enabled_tools_router_disabled_excludes_publish():
    config = DepthFusionConfig(rlm_enabled=True, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_publish_context" not in enabled
    assert len(enabled) == 7


def test_get_enabled_tools_both_disabled():
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_run_recursive" not in enabled
    assert "depthfusion_publish_context" not in enabled
    assert len(enabled) == 6


def test_core_tools_always_enabled():
    """Status, recall, tag, and v0.3.0 tools are never gated by feature flags."""
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_status" in enabled
    assert "depthfusion_recall_relevant" in enabled
    assert "depthfusion_tag_session" in enabled
    assert "depthfusion_tier_status" in enabled
    assert "depthfusion_auto_learn" in enabled
    assert "depthfusion_compress_session" in enabled


def test_server_module_importable():
    """MCP server module must be importable without side effects."""
    import depthfusion.mcp.server  # noqa: F401
    assert True  # If we get here, import succeeded


def test_graph_tools_registered_when_flag_enabled():
    """Graph tools appear in enabled list when graph_enabled=True."""
    from unittest.mock import MagicMock
    config = MagicMock()
    config.router_enabled = False
    config.rlm_enabled = False
    config.graph_enabled = True
    enabled = get_enabled_tools(config)
    assert "depthfusion_graph_traverse" in enabled
    assert "depthfusion_graph_status" in enabled
    assert "depthfusion_set_scope" in enabled


def test_graph_tools_absent_when_flag_disabled():
    from unittest.mock import MagicMock
    config = MagicMock()
    config.router_enabled = False
    config.rlm_enabled = False
    config.graph_enabled = False
    enabled = get_enabled_tools(config)
    assert "depthfusion_graph_traverse" not in enabled
