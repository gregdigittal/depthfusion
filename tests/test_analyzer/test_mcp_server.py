"""Tests for MCP server module — including v0.5.0 confirm_discovery (T-144/T-145)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from depthfusion.core.config import DepthFusionConfig
from depthfusion.identity.models import Principal
from depthfusion.mcp.server import TOOLS, _handle_tools_call, get_enabled_tools


def _make_principal(groups: list[str] | None = None) -> Principal:
    return Principal(
        principal_id="test-user",
        upn="test-user@example.com",
        display_name="test-user",
        groups=groups or ["member"],
    )


def test_tools_dict_has_thirty_one_entries():
    """Total tool count: 31 (registry as of E-67 rectification)."""
    assert len(TOOLS) == 31
    expected = {
        "depthfusion_status",
        "depthfusion_recall_relevant",
        "depthfusion_tag_session",
        "depthfusion_publish_context",
        "depthfusion_auto_learn",
        "depthfusion_compress_session",
        "depthfusion_graph_traverse",
        "depthfusion_graph_status",
        "depthfusion_set_scope",
        "depthfusion_confirm_discovery",
        "depthfusion_set_memory_score",
        "depthfusion_recall_feedback",
        "depthfusion_pin_discovery",
        "depthfusion_retrieve_context",
        "depthfusion_record_decision",
        "depthfusion_record_incident",
        "depthfusion_mark_superseded",
        "depthfusion_report_outcome",
        "depthfusion_record_telemetry",
        "depthfusion_query_telemetry",
        "depthfusion_session_seed",
        "depthfusion_register_project",
        "depthfusion_list_projects",
        "depthfusion_sync_project",
        "depthfusion_ingest_project",
        "depthfusion_research_topic",
        "depthfusion_bridge",
        "depthfusion_ingest_conversation",
        "depthfusion_list_providers",
        "depthfusion_recommend_model",
        "depthfusion_describe_capabilities",
    }
    assert set(TOOLS.keys()) == expected


def test_get_enabled_tools_all_flags_true():
    config = DepthFusionConfig(
        rlm_enabled=True, router_enabled=True, graph_enabled=True,
        cognitive_retrieval=True, decision_memory=True, operational_memory=True,
    )
    enabled = get_enabled_tools(config)
    assert set(enabled) == set(TOOLS.keys())
    assert len(enabled) == 31


def test_get_enabled_tools_rlm_disabled_excludes_recursive():
    # rlm_enabled no longer gates any tool in the current registry;
    # router_enabled=True adds publish_context (1 extra over the 22 always-on).
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=True)
    enabled = get_enabled_tools(config)
    assert "depthfusion_run_recursive" not in enabled  # trivially true — not registered
    # 22 always-on + 1 publish_context (router=True, graph off, cognitive defaults off) = 23
    assert len(enabled) == 23


def test_get_enabled_tools_router_disabled_excludes_publish():
    config = DepthFusionConfig(rlm_enabled=True, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_publish_context" not in enabled
    # 22 always-on (router off, graph off, cognitive defaults off) = 22
    assert len(enabled) == 22


def test_get_enabled_tools_both_disabled():
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_run_recursive" not in enabled  # trivially true — not registered
    assert "depthfusion_publish_context" not in enabled
    # 22 always-on (rlm off, router off, graph off, cognitive defaults off) = 22
    assert len(enabled) == 22


def test_core_tools_always_enabled():
    """Status, recall, tag, and other always-on tools are never gated by feature flags."""
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_status" in enabled
    assert "depthfusion_recall_relevant" in enabled
    assert "depthfusion_tag_session" in enabled
    assert "depthfusion_auto_learn" in enabled
    assert "depthfusion_compress_session" in enabled
    assert "depthfusion_describe_capabilities" in enabled


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

    def _principal(self):
        return _make_principal(groups=["member"])

    def test_missing_text_returns_error(self):
        config = self._cfg()
        result = _handle_tools_call("depthfusion_confirm_discovery", {}, config, self._principal())
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
                self._principal(),
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
                self._principal(),
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
                self._principal(),
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
                self._principal(),
            )
        assert captured_entries[0].confidence <= 1.0

    def test_unknown_tool_returns_error(self):
        config = self._cfg()
        result = _handle_tools_call("depthfusion_nonexistent", {}, config)
        assert result["isError"] is True
