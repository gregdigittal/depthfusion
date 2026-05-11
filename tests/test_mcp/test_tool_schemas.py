"""Tests for MCP tool schema completeness and correctness."""
from depthfusion.mcp.server import TOOLS, _make_tool_schema


def test_recall_relevant_has_query_required():
    schema = _make_tool_schema("depthfusion_recall_relevant", "desc")
    assert "query" in schema["inputSchema"]["required"]
    props = schema["inputSchema"]["properties"]
    assert props["query"]["type"] == "string"
    assert props["top_k"]["type"] == "integer"
    assert props["top_k"]["minimum"] == 1
    assert props["top_k"]["maximum"] == 20


def test_set_memory_score_has_filename_required():
    schema = _make_tool_schema("depthfusion_set_memory_score", "desc")
    assert "filename" in schema["inputSchema"]["required"]
    props = schema["inputSchema"]["properties"]
    assert props["importance"]["minimum"] == 0.0
    assert props["importance"]["maximum"] == 1.0
    assert props["salience"]["minimum"] == 0.0
    assert props["salience"]["maximum"] == 5.0


def test_recall_feedback_has_recall_id_required():
    schema = _make_tool_schema("depthfusion_recall_feedback", "desc")
    assert "recall_id" in schema["inputSchema"]["required"]


def test_prune_discoveries_has_no_required():
    schema = _make_tool_schema("depthfusion_prune_discoveries", "desc")
    assert schema["inputSchema"]["required"] == []
    assert "age_days" in schema["inputSchema"]["properties"]
    assert "confirm" in schema["inputSchema"]["properties"]


def test_confirm_discovery_has_content_required():
    schema = _make_tool_schema("depthfusion_confirm_discovery", "desc")
    assert "content" in schema["inputSchema"]["required"]


def test_pin_discovery_has_filename_required():
    schema = _make_tool_schema("depthfusion_pin_discovery", "desc")
    assert "filename" in schema["inputSchema"]["required"]


def test_all_tools_return_valid_schema_structure():
    for name, description in TOOLS.items():
        schema = _make_tool_schema(name, description)
        assert schema["name"] == name
        assert "inputSchema" in schema
        assert schema["inputSchema"]["type"] == "object"
        assert "properties" in schema["inputSchema"]
        assert "required" in schema["inputSchema"]
        assert isinstance(schema["inputSchema"]["required"], list)


def test_snippet_len_bounds():
    schema = _make_tool_schema("depthfusion_recall_relevant", "desc")
    props = schema["inputSchema"]["properties"]
    assert props["snippet_len"]["minimum"] == 200
    assert props["snippet_len"]["maximum"] == 8000
