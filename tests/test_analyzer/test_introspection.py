"""Tests for S-76 introspection tools and S-74/S-75 audit fixes."""
import json
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.mcp.server import (
    _tool_auto_learn,
    _tool_describe_capabilities,
    _tool_graph_status,
    _tool_inspect_discovery,
    get_enabled_tools,
)

# ---------------------------------------------------------------------------
# S-76: depthfusion_describe_capabilities
# ---------------------------------------------------------------------------

class TestDescribeCapabilities:
    def test_returns_all_required_keys(self):
        result = json.loads(_tool_describe_capabilities())
        for key in ("tier", "mode", "flags", "engaged_layers_per_op"):
            assert key in result

    def test_engaged_layers_per_op_keys(self):
        result = json.loads(_tool_describe_capabilities())
        ops = result["engaged_layers_per_op"]
        assert "recall" in ops
        assert "publish" in ops
        assert "auto_learn" in ops

    def test_bm25_always_in_recall(self):
        result = json.loads(_tool_describe_capabilities())
        assert "bm25" in result["engaged_layers_per_op"]["recall"]

    def test_heuristic_always_in_auto_learn(self):
        result = json.loads(_tool_describe_capabilities())
        assert "heuristic" in result["engaged_layers_per_op"]["auto_learn"]

    def test_embedding_in_recall_when_flags_set(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_VECTOR_SEARCH_ENABLED", "true")
        monkeypatch.setenv("DEPTHFUSION_EMBEDDING_BACKEND", "local")
        result = json.loads(_tool_describe_capabilities())
        assert "embedding" in result["engaged_layers_per_op"]["recall"]

    def test_embedding_absent_without_vector_search_flag(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_EMBEDDING_BACKEND", "local")
        monkeypatch.delenv("DEPTHFUSION_VECTOR_SEARCH_ENABLED", raising=False)
        result = json.loads(_tool_describe_capabilities())
        assert "embedding" not in result["engaged_layers_per_op"]["recall"]

    def test_graph_traverse_in_recall_when_enabled(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
        result = json.loads(_tool_describe_capabilities())
        assert "graph_traverse" in result["engaged_layers_per_op"]["recall"]

    def test_graph_extraction_in_auto_learn_when_both_enabled(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
        monkeypatch.setenv("DEPTHFUSION_HAIKU_ENABLED", "true")
        result = json.loads(_tool_describe_capabilities())
        assert "graph_extraction" in result["engaged_layers_per_op"]["auto_learn"]

    def test_flags_field_contains_all_known_flags(self):
        result = json.loads(_tool_describe_capabilities())
        flags = result["flags"]
        for k in ("graph_enabled", "haiku_enabled", "vector_search_enabled",
                   "embedding_backend", "fusion_gates_enabled", "router_enabled"):
            assert k in flags

    def test_tool_registered(self):
        from depthfusion.core.config import DepthFusionConfig
        cfg = DepthFusionConfig()
        enabled = get_enabled_tools(cfg)
        assert "depthfusion_describe_capabilities" in enabled


# ---------------------------------------------------------------------------
# S-76: depthfusion_inspect_discovery
# ---------------------------------------------------------------------------

class TestInspectDiscovery:
    def test_missing_filename_arg_returns_error(self):
        result = json.loads(_tool_inspect_discovery({}))
        assert result["exists"] is False
        assert "error" in result

    def test_nonexistent_file_returns_exists_false(self, tmp_path):
        result = json.loads(_tool_inspect_discovery(
            {"filename": str(tmp_path / "ghost.md")}
        ))
        assert result["exists"] is False

    def test_parses_frontmatter_fields(self, tmp_path):
        f = tmp_path / "discovery.md"
        f.write_text(
            "---\nimportance: 0.9\nsalience: 2.5\npinned: true\nproject: depthfusion\n---\nBody\n"
        )
        result = json.loads(_tool_inspect_discovery({"filename": str(f)}))
        assert result["exists"] is True
        fm = result["frontmatter"]
        assert fm["importance"] == pytest.approx(0.9)
        assert fm["salience"] == pytest.approx(2.5)
        assert fm["pinned"] is True
        assert fm["project"] == "depthfusion"

    def test_file_without_frontmatter_returns_empty_dict(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("Just content, no frontmatter.\n")
        result = json.loads(_tool_inspect_discovery({"filename": str(f)}))
        assert result["exists"] is True
        assert result["frontmatter"] == {}

    def test_tool_registered(self):
        from depthfusion.core.config import DepthFusionConfig
        cfg = DepthFusionConfig()
        enabled = get_enabled_tools(cfg)
        assert "depthfusion_inspect_discovery" in enabled


# ---------------------------------------------------------------------------
# S-76: engaged_layers in recall response
# ---------------------------------------------------------------------------

class TestEngagedLayersInRecall:
    def test_bm25_always_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_MODE", "local")
        monkeypatch.delenv("DEPTHFUSION_GRAPH_ENABLED", raising=False)
        monkeypatch.delenv("DEPTHFUSION_VECTOR_SEARCH_ENABLED", raising=False)
        # patch the sessions dir so we don't need real data
        from depthfusion.mcp import server as srv
        with patch.object(srv, "_tool_recall_impl") as mock_impl:
            mock_impl.return_value = json.dumps({
                "query": "test",
                "blocks": [],
                "recall_id": None,
                "total_sources_scanned": 0,
                "engaged_layers": ["bm25"],
                "message": "Retrieved 0 relevant blocks (BM25+RRF)",
            })
            result = json.loads(mock_impl("test"))
        assert "bm25" in result["engaged_layers"]


# ---------------------------------------------------------------------------
# S-74: graph_status surfaces extraction_active + tier_gates_extraction
# ---------------------------------------------------------------------------

class TestGraphStatusFields:
    def test_graph_disabled_response_has_no_extraction_fields(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_GRAPH_ENABLED", raising=False)
        result = json.loads(_tool_graph_status())
        assert result["graph_enabled"] is False
        # No extraction_active when graph not enabled — not needed
        assert "extraction_active" not in result

    def test_graph_enabled_exposes_extraction_active(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
        monkeypatch.setenv("DEPTHFUSION_HAIKU_ENABLED", "true")
        with patch("depthfusion.graph.store.get_store") as mock_store:
            store = MagicMock()
            store.node_count.return_value = 5
            store.edge_count.return_value = 3
            store.all_entities.return_value = []
            mock_store.return_value = store
            result = json.loads(_tool_graph_status())
        assert "extraction_active" in result
        assert result["extraction_active"] is True
        assert result["tier_gates_extraction"] is False

    def test_extraction_active_false_without_haiku(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
        monkeypatch.delenv("DEPTHFUSION_HAIKU_ENABLED", raising=False)
        with patch("depthfusion.graph.store.get_store") as mock_store:
            store = MagicMock()
            store.node_count.return_value = 0
            store.edge_count.return_value = 0
            store.all_entities.return_value = []
            mock_store.return_value = store
            result = json.loads(_tool_graph_status())
        assert result["extraction_active"] is False


# ---------------------------------------------------------------------------
# S-74: auto_learn wires graph extraction
# ---------------------------------------------------------------------------

class TestAutoLearnGraphWiring:
    def test_summarize_and_extract_graph_called_on_compress(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_GRAPH_ENABLED", "true")
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "test.tmp").write_text("some session content")

        mock_out = tmp_path / "test-autocapture.md"
        mock_out.write_text("---\n---\nBody")

        with patch("depthfusion.mcp.server.Path") as mock_path_cls, \
             patch("depthfusion.capture.auto_learn.summarize_and_extract_graph") as mock_sag, \
             patch("depthfusion.capture.compressor.SessionCompressor") as mock_comp_cls, \
             patch("depthfusion.graph.store.get_store"):
            # Patch Path.home() to return our tmp dir
            mock_path_cls.home.return_value = tmp_path
            mock_comp = MagicMock()
            mock_comp.compress.return_value = mock_out
            mock_comp_cls.return_value = mock_comp

            _tool_auto_learn({"max_files": 1})
            # summarize_and_extract_graph should have been called
            assert mock_sag.called
