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


def test_publish_context_has_s112_structured_fields():
    """S-112 AC-1: publish_context schema exposes optional structured fields."""
    schema = _make_tool_schema("depthfusion_publish_context", "desc")
    props = schema["inputSchema"]["properties"]
    item_props = props["item"]["properties"]
    for field in ("facts", "concepts", "files_read", "files_modified"):
        assert field in item_props, f"S-112: missing field '{field}' in publish_context schema"
        assert item_props[field]["type"] == "array"
        assert item_props[field]["items"]["type"] == "string"
    # These fields are optional — not in required
    required = schema["inputSchema"]["required"]
    assert "item" in required
    item_required = props["item"].get("required", [])
    for field in ("facts", "concepts", "files_read", "files_modified"):
        assert field not in item_required, f"S-112 field '{field}' must be optional"


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


def test_recall_relevant_has_explain_param():
    """S-92: explain boolean with default=False must appear in schema."""
    schema = _make_tool_schema("depthfusion_recall_relevant", "desc")
    props = schema["inputSchema"]["properties"]
    assert "explain" in props
    assert props["explain"]["type"] == "boolean"
    assert props["explain"]["default"] is False
    # explain is optional — must not be in required list
    assert "explain" not in schema["inputSchema"]["required"]


def test_auto_learn_has_ambient_mode_fields():
    """S-110 T-370: auto_learn schema exposes ambient mode parameters."""
    schema = _make_tool_schema("depthfusion_auto_learn", "desc")
    props = schema["inputSchema"]["properties"]
    assert "mode" in props
    assert props["mode"]["type"] == "string"
    # ambient-specific fields
    for field in ("tool_name", "session_id", "files_read", "files_modified"):
        assert field in props, f"S-110: missing '{field}' in auto_learn schema"
    # files_read and files_modified are arrays
    assert props["files_read"]["type"] == "array"
    assert props["files_modified"]["type"] == "array"
    # mode and ambient fields are all optional
    assert "mode" not in schema["inputSchema"]["required"]
    assert "tool_name" not in schema["inputSchema"]["required"]
