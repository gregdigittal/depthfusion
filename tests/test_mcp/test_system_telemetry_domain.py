"""Coverage for system.py (lines 34-97) and telemetry.py (lines 43-350)."""
from __future__ import annotations

import importlib.metadata
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.mcp.tools.system import (
    _tool_research_topic,
    _tool_status,
    register_system,
)
from depthfusion.mcp.tools.telemetry import (
    _emit_startup_event,
    _tool_describe_capabilities,
    _tool_hnsw_capability,
    _tool_surface_skill_candidates,
    _tool_tier_status,
    register_telemetry,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _cfg(tmp_path):
    return SimpleNamespace(
        event_log_path=tmp_path / "events.jsonl",
        memory_store_path=tmp_path / "memories.db",
        telemetry_store_path=tmp_path / "telemetry.db",
        auto_draft_threshold=3,
        rlm_enabled=True,
        router_enabled=True,
        session_enabled=True,
        fusion_enabled=True,
        decision_memory=True,
        operational_memory=True,
    )


# ── system.py ────────────────────────────────────────────────────────────────

def test_tool_status_basic(tmp_path):
    """Covers lines 34-45: returns active + enabled_tools JSON."""
    result = _tool_status(_cfg(tmp_path))
    data = json.loads(result)
    assert data["depthfusion"] == "active"
    assert "enabled_tools" in data
    assert data["rlm_enabled"] is True


def test_tool_status_defaults_when_no_attributes():
    """getattr fallbacks work on a minimal config."""
    result = _tool_status(SimpleNamespace())
    data = json.loads(result)
    assert data["depthfusion"] == "active"
    # all four flags default to True
    assert data["rlm_enabled"] is True
    assert data["router_enabled"] is True


def test_tool_research_topic_empty_topic():
    """Lines 64-68: missing topic → immediate error JSON."""
    result = _tool_research_topic({})
    data = json.loads(result)
    assert "error" in data
    assert data["error"] == "topic is required"


def test_tool_research_topic_with_mock():
    """Lines 64-93: happy path with monkeypatched TopicResearcher."""
    mock_researcher = MagicMock()
    mock_researcher.research.return_value = {
        "saved_to": "/tmp/test-research.md",
        "sources": {"web": ["result1", "result2"], "arxiv": [], "github": ["repo1"]},
    }

    with patch("depthfusion.mcp.tools.system.TopicResearcher", return_value=mock_researcher):
        result = _tool_research_topic(
            {"topic": "HNSW indexing", "slug": "hnsw-research", "sources": ["web", "github"]}
        )

    data = json.loads(result)
    assert data["researched"] is True
    assert data["topic"] == "HNSW indexing"
    assert "source_counts" in data
    assert data["source_counts"]["web"] == 2


def test_tool_research_topic_sources_not_list():
    """When sources is not a list it is reset to the default — covers line 70."""
    mock_researcher = MagicMock()
    mock_researcher.research.return_value = {"saved_to": "", "sources": {}}

    with patch("depthfusion.mcp.tools.system.TopicResearcher", return_value=mock_researcher):
        result = _tool_research_topic({"topic": "embeddings", "sources": "bad"})

    data = json.loads(result)
    # sources defaulted back to list — no crash
    assert "researched" in data


def test_tool_research_topic_researcher_raises():
    """Lines 92-93: exception from researcher → error JSON, not a crash."""
    mock_researcher = MagicMock()
    mock_researcher.research.side_effect = RuntimeError("network timeout")

    with patch("depthfusion.mcp.tools.system.TopicResearcher", return_value=mock_researcher):
        result = _tool_research_topic({"topic": "vector stores"})

    data = json.loads(result)
    assert data["researched"] is False
    assert "network timeout" in data["error"]


def test_register_system_callable():
    """Line 97: stub must not raise."""
    register_system()


# ── telemetry.py ─────────────────────────────────────────────────────────────

def test_tool_hnsw_capability_exception(monkeypatch):
    """Lines 43-47: store.capability() raises → returns disabled JSON."""
    bad_store = MagicMock()
    bad_store.capability.side_effect = RuntimeError("index corrupted")
    monkeypatch.setattr(
        "depthfusion.mcp.tools.telemetry._get_hnsw_store", lambda: bad_store
    )
    result = _tool_hnsw_capability()
    data = json.loads(result)
    assert data["enabled"] is False
    assert data["backend"] == "none"


def test_tool_tier_status_returns_json():
    """Lines 59-72: either success dict or error dict — both are valid JSON."""
    result = _tool_tier_status()
    data = json.loads(result)
    # Either "tier" key (success) or "error" key (TierManager unavailable)
    assert "tier" in data or "error" in data


def test_tool_describe_capabilities_local_defaults():
    """Lines 74-141: baseline local mode — bm25 always in recall layers."""
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("DEPTHFUSION_")}
    env["DEPTHFUSION_MODE"] = "local"
    with patch.dict(os.environ, env, clear=True):
        result = _tool_describe_capabilities()
    data = json.loads(result)
    assert "bm25" in data["engaged_layers_per_op"]["recall"]
    assert data["mode"] == "local"


def test_tool_describe_capabilities_vps_tier_fallback(monkeypatch):
    """Lines 93-99: vps mode + TierManager failure → tier = 'vps-tier1'."""
    import os

    def _bad_tier_manager(*a, **kw):
        raise RuntimeError("db unavailable")

    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")

    # Patch TierManager at its origin so the function-level from-import picks it up
    with patch("depthfusion.storage.tier_manager.TierManager", side_effect=_bad_tier_manager):
        result = _tool_describe_capabilities()

    data = json.loads(result)
    assert data["tier"] == "vps-tier1"
    assert data["mode"] == "vps"


def test_emit_startup_event_package_not_found(tmp_path):
    """Lines 161-162: PackageNotFoundError handled → version = 'unknown'."""
    from depthfusion.metrics.collector import MetricsCollector

    recorded = {}

    def _mock_record(self, event_name, value, extra):
        recorded["version"] = extra.get("server_version")

    def _bad_version(name):
        raise importlib.metadata.PackageNotFoundError(name)

    with patch("importlib.metadata.version", side_effect=_bad_version), \
         patch.object(MetricsCollector, "record", _mock_record):
        _emit_startup_event(tools_enabled=5, metrics_dir=tmp_path)

    assert recorded.get("version") == "unknown"


def test_emit_startup_event_metrics_failure(tmp_path):
    """Outer except at line 168: MetricsCollector failure is logged, never raised."""
    from depthfusion.metrics.collector import MetricsCollector

    def _raise(*a, **kw):
        raise OSError("permission denied")

    with patch.object(MetricsCollector, "record", _raise):
        # Must not raise
        _emit_startup_event(tools_enabled=3, metrics_dir=tmp_path)


# ── _tool_surface_skill_candidates ───────────────────────────────────────────

def test_surface_skill_candidates_empty_store(tmp_path):
    """Lines 276-282 + 338-346: empty TelemetryStore → no candidates."""
    result = _tool_surface_skill_candidates(
        {"threshold": 1, "dry_run": True}, _cfg(tmp_path)
    )
    data = json.loads(result)
    assert data["candidates_found"] == 0
    assert data["candidates_drafted"] == 0
    assert data["dry_run"] is True


def test_surface_skill_candidates_dry_run_with_patterns(tmp_path):
    """Lines 287-336 (dry_run=True branch): patterns found, no SkillForge call."""
    from depthfusion.mcp.tools.telemetry import _tool_record_telemetry

    cfg = _cfg(tmp_path)
    # Record the same tool_name across two distinct session IDs
    for i in range(2):
        _tool_record_telemetry(
            {"session_id": f"s-{i}", "tool_name": "recall", "agent": "test"},
            cfg,
        )

    result = _tool_surface_skill_candidates(
        {"threshold": 1, "dry_run": True}, cfg
    )
    data = json.loads(result)
    assert data["candidates_found"] >= 1
    assert data["dry_run"] is True
    assert data["candidates_drafted"] >= 1


def test_surface_skill_candidates_already_tracked(tmp_path):
    """Lines 299-311: second run with same patterns → already_tracked increments."""
    from depthfusion.mcp.tools.telemetry import _tool_record_telemetry

    cfg = _cfg(tmp_path)
    for i in range(2):
        _tool_record_telemetry(
            {"session_id": f"s2-{i}", "tool_name": "capture", "agent": "test"},
            cfg,
        )

    # First run: inserts candidate
    _tool_surface_skill_candidates({"threshold": 1, "dry_run": True}, cfg)
    # Second run: candidate already inserted → already_tracked path
    result = _tool_surface_skill_candidates({"threshold": 1, "dry_run": True}, cfg)
    data = json.loads(result)
    assert data["already_tracked"] >= 1


def test_surface_skill_candidates_with_skillforge_call(tmp_path):
    """Lines 313-324: non-dry-run with post_skill_draft mock."""
    from depthfusion.mcp.tools.telemetry import _tool_record_telemetry

    cfg = _cfg(tmp_path)
    for i in range(2):
        _tool_record_telemetry(
            {"session_id": f"s3-{i}", "tool_name": "publish", "agent": "test"},
            cfg,
        )

    mock_post = MagicMock(return_value={"id": "skill-abc"})
    with patch("depthfusion.mcp.skillforge_client.post_skill_draft", mock_post):
        result = _tool_surface_skill_candidates(
            {"threshold": 1, "dry_run": False}, cfg
        )

    data = json.loads(result)
    assert data["dry_run"] is False
    assert data["candidates_drafted"] >= 1
    # First run inserts via SkillForge; skillforge_id should be set
    drafted = [item for item in data["items"] if item["drafted"]]
    if drafted:
        assert drafted[0]["skillforge_id"] == "skill-abc"


def test_register_telemetry_callable():
    """Line 350: stub must not raise."""
    register_telemetry()
