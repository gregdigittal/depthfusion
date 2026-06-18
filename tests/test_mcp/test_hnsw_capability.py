"""Tests for E-45 HNSW capability tool + recall/publish response fields.

These tests intentionally exercise the HNSW-disabled (default) path. They
must NOT require hnswlib to be installed — the DEPTHFUSION_HNSW_ENABLED env
var is left at its default ("false") so _get_hnsw_store() short-circuits
before touching hnswlib.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import depthfusion.mcp.tools._state as _state
from depthfusion.mcp import server as mcp_server


@pytest.fixture(autouse=True)
def _reset_hnsw_singleton():
    """Reset module-level HNSW singleton between tests."""
    _state._HNSW_STORE = None
    _state._HNSW_INIT_ATTEMPTED = False
    yield
    _state._HNSW_STORE = None
    _state._HNSW_INIT_ATTEMPTED = False


def test_hnsw_capability_returns_disabled_when_env_not_set():
    """When the env flag is off, capability reports enabled=False."""
    env = {k: v for k, v in os.environ.items() if k != "DEPTHFUSION_HNSW_ENABLED"}
    with patch.dict(os.environ, env, clear=True):
        raw = mcp_server._tool_hnsw_capability()
        cap = json.loads(raw)
        assert cap["enabled"] is False
        assert cap["backend"] == "none"
        assert cap["entry_count"] == 0
        assert "model" in cap
        assert "dimension" in cap
        assert "index_path" in cap



def test_publish_context_returns_indexed_in_hnsw_false_when_disabled(tmp_path: Path):
    """With HNSW disabled, publish_context still works and reports False."""
    import depthfusion.mcp.tools.capture as _capture
    from depthfusion.router.bus import FileBus

    bus = FileBus(bus_dir=tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "DEPTHFUSION_HNSW_ENABLED"}
    with patch.dict(os.environ, env, clear=True), \
         patch.object(_capture, "_get_context_bus", return_value=bus):
        payload = {
            "item": {
                "item_id": "hnsw-test-1",
                "content": "hello world",
                "source_agent": "test",
                "tags": ["t"],
            }
        }
        raw = mcp_server._tool_publish_context(payload)
        result = json.loads(raw)
        assert result.get("published") is True
        assert "indexed_in_hnsw" in result
        assert result["indexed_in_hnsw"] is False


def test_publish_context_returns_indexed_in_hnsw_bool(tmp_path: Path):
    """`indexed_in_hnsw` is always present and is a bool — never missing/None."""
    import depthfusion.mcp.tools.capture as _capture
    from depthfusion.router.bus import FileBus

    bus = FileBus(bus_dir=tmp_path)
    with patch.object(_capture, "_get_context_bus", return_value=bus):
        payload = {
            "item": {
                "item_id": "hnsw-test-2",
                "content": "another body",
                "source_agent": "test",
                "tags": ["t"],
            }
        }
        raw = mcp_server._tool_publish_context(payload)
        result = json.loads(raw)
        assert "indexed_in_hnsw" in result, (
            f"E-45 contract: indexed_in_hnsw missing from response: {result}"
        )
        assert isinstance(result["indexed_in_hnsw"], bool)


def test_recall_returns_strategy_and_hnsw_available_fields(tmp_path: Path, monkeypatch):
    """`strategy` and `hnsw_available` MUST be present in every recall response."""
    # Isolate to an empty home so the recall returns "No session context available".
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "DEPTHFUSION_HNSW_ENABLED"}
    with patch.dict(os.environ, env, clear=True):
        raw = mcp_server._tool_recall({"query": "anything"})
        result = json.loads(raw)
        assert "strategy" in result, f"E-45 contract: strategy missing: {result}"
        assert "hnsw_available" in result, (
            f"E-45 contract: hnsw_available missing: {result}"
        )
        assert result["strategy"] == "bm25-only"
        assert isinstance(result["hnsw_available"], bool)
        assert result["hnsw_available"] is False
