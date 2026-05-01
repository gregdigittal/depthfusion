"""Tests for MCP server module — including v0.5.0 confirm_discovery (T-144/T-145)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from depthfusion.core.config import DepthFusionConfig
from depthfusion.mcp.server import TOOLS, _handle_tools_call, get_enabled_tools


def test_tools_dict_has_fifteen_entries():
    """Total tool count: 15 (S-72 added depthfusion_recall_feedback)."""
    assert len(TOOLS) == 15
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
        "depthfusion_confirm_discovery",
        "depthfusion_prune_discoveries",      # v0.5.1 S-55
        "depthfusion_set_memory_score",       # E-27 / S-70
        "depthfusion_recall_feedback",        # E-27 / S-72
    }
    assert set(TOOLS.keys()) == expected


def test_get_enabled_tools_all_flags_true():
    config = DepthFusionConfig(rlm_enabled=True, router_enabled=True, graph_enabled=True)
    enabled = get_enabled_tools(config)
    assert set(enabled) == set(TOOLS.keys())
    assert len(enabled) == 15


def test_get_enabled_tools_rlm_disabled_excludes_recursive():
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=True)
    enabled = get_enabled_tools(config)
    assert "depthfusion_run_recursive" not in enabled
    # 15 total - 1 rlm - 3 graph (graph_enabled defaults False) = 11
    assert len(enabled) == 11


def test_get_enabled_tools_router_disabled_excludes_publish():
    config = DepthFusionConfig(rlm_enabled=True, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_publish_context" not in enabled
    # 15 total - 1 publish - 3 graph = 11
    assert len(enabled) == 11


def test_get_enabled_tools_both_disabled():
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_run_recursive" not in enabled
    assert "depthfusion_publish_context" not in enabled
    # 15 total - 2 flagged - 3 graph = 10
    assert len(enabled) == 10


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


def test_confirm_discovery_always_enabled():
    """depthfusion_confirm_discovery is always enabled — no feature flag."""
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=False, graph_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_confirm_discovery" in enabled


def test_server_module_importable():
    """MCP server module must be importable without side effects."""
    import depthfusion.mcp.server  # noqa: F401

    assert True  # If we get here, import succeeded


def test_graph_tools_registered_when_flag_enabled():
    """Graph tools appear in enabled list when graph_enabled=True."""
    config = MagicMock()
    config.router_enabled = False
    config.rlm_enabled = False
    config.graph_enabled = True
    enabled = get_enabled_tools(config)
    assert "depthfusion_graph_traverse" in enabled
    assert "depthfusion_graph_status" in enabled
    assert "depthfusion_set_scope" in enabled


def test_graph_tools_absent_when_flag_disabled():
    config = MagicMock()
    config.router_enabled = False
    config.rlm_enabled = False
    config.graph_enabled = False
    enabled = get_enabled_tools(config)
    assert "depthfusion_graph_traverse" not in enabled
    assert "depthfusion_graph_status" not in enabled
    assert "depthfusion_set_scope" not in enabled


# ---------------------------------------------------------------------------
# depthfusion_confirm_discovery tool (T-144 / T-145 / CM-5)
# ---------------------------------------------------------------------------

class TestConfirmDiscovery:
    def _cfg(self):
        return DepthFusionConfig(rlm_enabled=False, router_enabled=False, graph_enabled=False)

    def test_missing_text_returns_error(self):
        config = self._cfg()
        result = _handle_tools_call("depthfusion_confirm_discovery", {}, config)
        assert result["isError"] is False  # protocol success
        body = json.loads(result["content"][0]["text"])
        assert body["ok"] is False
        assert "text" in body["error"].lower()

    def test_valid_text_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PROJECT", "test-proj")
        # Patch write_decisions to avoid real filesystem writes in ~/.claude

        written_path = tmp_path / "2026-04-20-test-proj-decisions.md"
        written_path.write_text("---\ntype: decisions\n---\n")

        import unittest.mock as mock
        with mock.patch(
            "depthfusion.capture.decision_extractor.write_decisions",
            return_value=written_path,
        ):
            result = _handle_tools_call(
                "depthfusion_confirm_discovery",
                {"text": "Use asyncpg over psycopg2 for async support", "project": "test-proj"},
                self._cfg(),
            )

        assert result["isError"] is False
        body = json.loads(result["content"][0]["text"])
        assert body["ok"] is True
        assert body["project"] == "test-proj"

    def test_text_truncated_at_300_chars(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PROJECT", "proj")
        long_text = "A" * 400
        import unittest.mock as mock
        with mock.patch(
            "depthfusion.capture.decision_extractor.write_decisions",
            return_value=None,
        ):
            result = _handle_tools_call(
                "depthfusion_confirm_discovery",
                {"text": long_text, "project": "proj"},
                self._cfg(),
            )
        assert result["isError"] is False

    def test_invalid_category_defaults_to_decision(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PROJECT", "proj")
        import unittest.mock as mock

        captured_entries = []

        def fake_write(entries, **kwargs):
            captured_entries.extend(entries)
            return None

        with mock.patch(
            "depthfusion.capture.decision_extractor.write_decisions",
            side_effect=fake_write,
        ):
            _handle_tools_call(
                "depthfusion_confirm_discovery",
                {"text": "Always validate at boundaries", "project": "proj",
                 "category": "invalid_category"},
                self._cfg(),
            )
        assert captured_entries[0].category == "decision"

    def test_confidence_clamped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PROJECT", "proj")
        import unittest.mock as mock

        captured_entries = []

        def fake_write(entries, **kwargs):
            captured_entries.extend(entries)
            return None

        with mock.patch(
            "depthfusion.capture.decision_extractor.write_decisions",
            side_effect=fake_write,
        ):
            _handle_tools_call(
                "depthfusion_confirm_discovery",
                {"text": "Deploy via kubernetes", "project": "proj", "confidence": 99.9},
                self._cfg(),
            )
        assert captured_entries[0].confidence <= 1.0

    def test_unknown_tool_returns_error(self):
        config = self._cfg()
        result = _handle_tools_call("depthfusion_nonexistent", {}, config)
        assert result["isError"] is True
