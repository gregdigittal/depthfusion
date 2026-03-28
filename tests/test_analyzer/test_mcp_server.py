"""Tests for MCP server module."""
from __future__ import annotations

from depthfusion.core.config import DepthFusionConfig
from depthfusion.mcp.server import TOOLS, get_enabled_tools


def test_tools_dict_has_five_entries():
    assert len(TOOLS) == 5
    expected = {
        "depthfusion_status",
        "depthfusion_recall_relevant",
        "depthfusion_tag_session",
        "depthfusion_publish_context",
        "depthfusion_run_recursive",
    }
    assert set(TOOLS.keys()) == expected


def test_get_enabled_tools_all_flags_true():
    config = DepthFusionConfig(rlm_enabled=True, router_enabled=True)
    enabled = get_enabled_tools(config)
    assert set(enabled) == set(TOOLS.keys())
    assert len(enabled) == 5


def test_get_enabled_tools_rlm_disabled_excludes_recursive():
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=True)
    enabled = get_enabled_tools(config)
    assert "depthfusion_run_recursive" not in enabled
    assert len(enabled) == 4


def test_get_enabled_tools_router_disabled_excludes_publish():
    config = DepthFusionConfig(rlm_enabled=True, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_publish_context" not in enabled
    assert len(enabled) == 4


def test_get_enabled_tools_both_disabled():
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_run_recursive" not in enabled
    assert "depthfusion_publish_context" not in enabled
    assert len(enabled) == 3


def test_core_tools_always_enabled():
    """Status, recall, and tag are never gated by feature flags."""
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_status" in enabled
    assert "depthfusion_recall_relevant" in enabled
    assert "depthfusion_tag_session" in enabled


def test_server_module_importable():
    """MCP server module must be importable without side effects."""
    import depthfusion.mcp.server  # noqa: F401
    assert True  # If we get here, import succeeded
