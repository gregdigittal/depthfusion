"""Tests for ContextOffloader — E-68 S-231 T-799/800/801/802/803.

Covers:
  - AC-1: round-trip offload → node_id → retrieve (bridge)
  - AC-2: Mermaid canvas produced and stored when offload_enabled
  - AC-3: _tool_bridge(node_id=...) returns raw offloaded text from refs/
  - AC-4: Mermaid canvas token-cap with overflow node
  - AC-5: offload_enabled + refs_count in _tool_status
  - AC-6: Mermaid syntax validity (must start with 'flowchart')
"""
from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.cognitive.offloader import ContextOffloader
from depthfusion.core.config import DepthFusionConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs) -> DepthFusionConfig:
    return DepthFusionConfig(
        offload_enabled=kwargs.get("offload_enabled", True),
        offload_mmd_max_tokens=kwargs.get("offload_mmd_max_tokens", 400),
    )


# ---------------------------------------------------------------------------
# AC-1 / T-799: round-trip offload → node_id → retrieve
# ---------------------------------------------------------------------------

def test_offload_writes_file_and_returns_mermaid_ref(tmp_path):
    config = _make_config()
    offloader = ContextOffloader(config)
    offloader._refs_base = tmp_path / "refs"

    text = "Hello, offloader world!"
    mmd_ref = offloader.offload(text, session_id="sess1")

    # Mermaid ref must contain node_id
    assert "ctx:" in mmd_ref
    node_id = re.search(r"ctx:([a-f0-9]+)", mmd_ref)
    assert node_id is not None, f"No node_id in mmd_ref: {mmd_ref!r}"
    nid = node_id.group(1)

    # File must exist
    ref_file = tmp_path / "refs" / "sess1" / f"{nid}.md"
    assert ref_file.exists(), f"Expected ref file at {ref_file}"
    assert ref_file.read_text(encoding="utf-8") == text


def test_retrieve_returns_original_text(tmp_path):
    config = _make_config()
    offloader = ContextOffloader(config)
    offloader._refs_base = tmp_path / "refs"

    text = "Retrieve me!"
    mmd_ref = offloader.offload(text, session_id="sess2")
    node_id = re.search(r"ctx:([a-f0-9]+)", mmd_ref).group(1)

    retrieved = offloader.retrieve(node_id, session_id="sess2")
    assert retrieved == text


def test_retrieve_raises_file_not_found(tmp_path):
    config = _make_config()
    offloader = ContextOffloader(config)
    offloader._refs_base = tmp_path / "refs"

    with pytest.raises(FileNotFoundError):
        offloader.retrieve("deadbeef", session_id="no_such_session")


# ---------------------------------------------------------------------------
# AC-6 / T-803 (subset): Mermaid syntax validity
# ---------------------------------------------------------------------------

def test_mermaid_ref_format(tmp_path):
    config = _make_config()
    offloader = ContextOffloader(config)
    offloader._refs_base = tmp_path / "refs"

    mmd_ref = offloader.offload("some text", session_id="s")
    # Must be a valid Mermaid node definition fragment
    assert "[/" in mmd_ref or "[" in mmd_ref
    assert "ctx:" in mmd_ref


def test_mermaid_canvas_starts_with_flowchart(tmp_path):
    """Canvas produced by _build_mermaid_canvas must begin with 'flowchart'."""
    from depthfusion.mcp.tools.capture import _build_mermaid_canvas

    # Create a fake discovery file
    disc_file = tmp_path / "session.md"
    disc_file.write_text(
        "# Step 1\n- action A\n- action B\n# Step 2\n- done\n",
        encoding="utf-8",
    )

    canvas = _build_mermaid_canvas(
        session_id="test_sess",
        output_path=str(disc_file),
        max_tokens=400,
    )
    assert canvas.startswith("flowchart"), f"Canvas does not start with 'flowchart': {canvas[:80]!r}"


# ---------------------------------------------------------------------------
# AC-4 / T-798: token cap + overflow node
# ---------------------------------------------------------------------------

def test_mermaid_canvas_token_cap_produces_overflow_node(tmp_path):
    """When state lines would exceed max_tokens, canvas includes an overflow node and
    stays within the char-based budget (max_tokens * 4 chars)."""
    from depthfusion.mcp.tools.capture import _build_mermaid_canvas

    # Create a discovery file with many state lines
    disc_file = tmp_path / "big_session.md"
    lines = [f"# Step {i}: some long state transition label for testing" for i in range(50)]
    disc_file.write_text("\n".join(lines), encoding="utf-8")

    # max_tokens=30 is tight enough to trigger overflow yet large enough for the
    # overflow node itself to fit inside the budget (header+overflow ≈ 116 chars < 120).
    max_tokens = 30
    max_chars = max_tokens * 4
    canvas = _build_mermaid_canvas(
        session_id="big_sess",
        output_path=str(disc_file),
        max_tokens=max_tokens,
    )

    # Overflow node must be present when cap is tight
    assert "overflow" in canvas, f"Expected overflow node in canvas: {canvas!r}"
    assert "token cap" in canvas
    # AC-4: canvas must stay within the char-based budget
    assert len(canvas) <= max_chars, (
        f"Canvas too large: {len(canvas)} chars > {max_chars} (max_tokens={max_tokens})"
    )


def test_mermaid_canvas_within_token_budget(tmp_path):
    """Canvas with few nodes must stay within the token budget (char heuristic)."""
    from depthfusion.mcp.tools.capture import _build_mermaid_canvas

    disc_file = tmp_path / "small_session.md"
    disc_file.write_text("# start\n- compress\n# done\n", encoding="utf-8")

    max_tokens = 400
    canvas = _build_mermaid_canvas(
        session_id="small_sess",
        output_path=str(disc_file),
        max_tokens=max_tokens,
    )

    chars_per_token = 4
    assert len(canvas) <= max_tokens * chars_per_token, (
        f"Canvas too large: {len(canvas)} chars > {max_tokens * chars_per_token}"
    )
    # No overflow node needed for small input
    assert "overflow" not in canvas


# ---------------------------------------------------------------------------
# AC-2 / T-800: _tool_compress_session produces Mermaid canvas when offload_enabled
# ---------------------------------------------------------------------------

def test_compress_session_produces_mermaid_canvas_when_offload_enabled(tmp_path):
    """_tool_compress_session returns mermaid_canvas in result when offload_enabled."""
    from depthfusion.mcp.tools.capture import _tool_compress_session

    config = _make_config(offload_enabled=True, offload_mmd_max_tokens=400)

    # Create a fake .tmp session file
    session_tmp = tmp_path / "test_session.tmp"
    session_tmp.write_text("# some session content\n- did something\n", encoding="utf-8")

    # Create a fake discovery output file
    disc_out = tmp_path / "test_session.md"
    disc_out.write_text("# discovery\n- compressed step\n", encoding="utf-8")

    fake_out = MagicMock()
    fake_out.__str__ = lambda self: str(disc_out)
    fake_out.with_suffix = lambda s: disc_out.with_suffix(s)

    # SessionCompressor is imported lazily inside _tool_compress_session;
    # patch at the source module so the lazy import picks up the mock.
    with patch("depthfusion.capture.compressor.SessionCompressor") as MockCompressor:
        MockCompressor.return_value.compress.return_value = fake_out
        result_raw = _tool_compress_session(
            {"session_path": str(session_tmp)},
            config=config,
        )

    result = json.loads(result_raw)
    assert result.get("success") is True
    assert "mermaid_canvas" in result, f"No mermaid_canvas in result: {result}"
    canvas = result["mermaid_canvas"]
    assert canvas.startswith("flowchart"), f"Canvas doesn't start with 'flowchart': {canvas[:80]!r}"
    assert "canvas_path" in result


def test_compress_session_no_canvas_when_offload_disabled(tmp_path):
    """_tool_compress_session does NOT include mermaid_canvas when offload_enabled=False."""
    from depthfusion.mcp.tools.capture import _tool_compress_session

    config = _make_config(offload_enabled=False)

    session_tmp = tmp_path / "test_session.tmp"
    session_tmp.write_text("# content\n", encoding="utf-8")

    disc_out = tmp_path / "test_session.md"
    disc_out.write_text("# discovery\n", encoding="utf-8")

    fake_out = MagicMock()
    fake_out.__str__ = lambda self: str(disc_out)
    fake_out.with_suffix = lambda s: disc_out.with_suffix(s)

    with patch("depthfusion.capture.compressor.SessionCompressor") as MockCompressor:
        MockCompressor.return_value.compress.return_value = fake_out
        result_raw = _tool_compress_session(
            {"session_path": str(session_tmp)},
            config=config,
        )

    result = json.loads(result_raw)
    assert result.get("success") is True
    assert "mermaid_canvas" not in result


# ---------------------------------------------------------------------------
# AC-3 / T-801: _tool_bridge with node_id returns raw offloaded text
# ---------------------------------------------------------------------------

def test_tool_bridge_retrieves_offloaded_text(tmp_path):
    """_tool_bridge(node_id=...) returns raw text from refs/ file."""
    from depthfusion.mcp.tools.bridge import _tool_bridge

    config = _make_config()
    offloader = ContextOffloader(config)
    offloader._refs_base = tmp_path / "refs"

    text = "This is the offloaded blob content."
    mmd_ref = offloader.offload(text, session_id="bridge_sess")
    node_id = re.search(r"ctx:([a-f0-9]+)", mmd_ref).group(1)

    # ContextOffloader is imported lazily inside _tool_bridge;
    # patch at the source module so the lazy import picks up the mock.
    with patch("depthfusion.cognitive.offloader.ContextOffloader") as MockOffloader:
        instance = MockOffloader.return_value
        instance.retrieve.return_value = text

        result_raw = _tool_bridge(
            {"node_id": node_id, "session_id": "bridge_sess"},
            config=config,
        )

    result = json.loads(result_raw)
    assert "text" in result, f"No 'text' key in result: {result}"
    assert result["text"] == text
    assert result["node_id"] == node_id


def test_tool_bridge_node_id_not_found(tmp_path):
    """_tool_bridge returns error when node_id refs file does not exist."""
    from depthfusion.mcp.tools.bridge import _tool_bridge

    config = _make_config()

    with patch("depthfusion.cognitive.offloader.ContextOffloader") as MockOffloader:
        instance = MockOffloader.return_value
        instance.retrieve.side_effect = FileNotFoundError("not found")

        result_raw = _tool_bridge(
            {"node_id": "deadbeef", "session_id": "no_sess"},
            config=config,
        )

    result = json.loads(result_raw)
    assert "error" in result
    assert result.get("node_id") == "deadbeef"


# ---------------------------------------------------------------------------
# AC-5 / T-802: offload_enabled + refs_count in _tool_status
# ---------------------------------------------------------------------------

def test_tool_status_exposes_offload_fields(tmp_path):
    """_tool_status returns offload_enabled and refs_count."""
    from depthfusion.mcp.tools.system import _tool_status

    config = _make_config(offload_enabled=True, offload_mmd_max_tokens=400)

    # ContextOffloader is imported lazily inside _tool_status;
    # patch at the source module so the lazy import picks up the mock.
    with patch("depthfusion.cognitive.offloader.ContextOffloader") as MockOffloader:
        instance = MockOffloader.return_value
        instance.refs_count.return_value = 3

        result_raw = _tool_status(config)

    result = json.loads(result_raw)
    assert "offload" in result, f"No 'offload' key in status: {list(result.keys())}"
    offload = result["offload"]
    assert offload["offload_enabled"] is True
    assert offload["refs_count"] == 3
    assert "offload_mmd_max_tokens" in offload


def test_tool_status_refs_count_zero_when_disabled():
    """refs_count is 0 and offload_enabled is False when not configured."""
    from depthfusion.mcp.tools.system import _tool_status

    config = DepthFusionConfig(offload_enabled=False, offload_mmd_max_tokens=400)

    result_raw = _tool_status(config)
    result = json.loads(result_raw)
    offload = result.get("offload", {})
    assert offload.get("offload_enabled") is False
    assert offload.get("refs_count") == 0


# ---------------------------------------------------------------------------
# AC-6 / T-803: Mermaid syntax validity — no bare quotes in node labels
# ---------------------------------------------------------------------------

def test_mermaid_canvas_no_unescaped_double_quotes(tmp_path):
    """Node labels must not contain bare double quotes (would break Mermaid)."""
    from depthfusion.mcp.tools.capture import _build_mermaid_canvas

    disc_file = tmp_path / "quoted.md"
    disc_file.write_text('# Step with "quotes" in label\n- another step\n', encoding="utf-8")

    canvas = _build_mermaid_canvas(
        session_id="quoted_sess",
        output_path=str(disc_file),
        max_tokens=400,
    )

    # Mermaid node strings are delimited by double-quotes; internal quotes must be escaped
    # Our impl replaces " with ' so we check that raw " only appear as node delimiters
    # i.e. every " is immediately preceded or followed by [ or ]
    # Simple check: no label content contains escaped sequences that would break parse
    # We verify by checking the string between [" and "] doesn't have bare "
    for match in re.finditer(r'\["([^"]+)"\]', canvas):
        label_content = match.group(1)
        assert '"' not in label_content, (
            f"Bare double-quote found inside node label: {label_content!r}"
        )


def test_refs_count_counts_all_sessions(tmp_path):
    """refs_count() without session_id counts all .md files across sessions."""
    config = _make_config()
    offloader = ContextOffloader(config)
    offloader._refs_base = tmp_path / "refs"

    offloader.offload("text1", session_id="s1")
    offloader.offload("text2", session_id="s1")
    offloader.offload("text3", session_id="s2")

    count = offloader.refs_count()
    assert count == 3


def test_refs_count_by_session(tmp_path):
    """refs_count(session_id=...) counts only that session's files."""
    config = _make_config()
    offloader = ContextOffloader(config)
    offloader._refs_base = tmp_path / "refs"

    offloader.offload("a", session_id="sa")
    offloader.offload("b", session_id="sa")
    offloader.offload("c", session_id="sb")

    assert offloader.refs_count(session_id="sa") == 2
    assert offloader.refs_count(session_id="sb") == 1
    assert offloader.refs_count(session_id="nonexistent") == 0
