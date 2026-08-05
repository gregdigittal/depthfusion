# tests/test_retrieval/test_ambient_trace_filter.py
"""Ambient-trace recall-exclusion filter tests — S-250 AC-2.

Mirrors the conventions in tests/test_retrieval/test_project_filter.py:
  * extract_frontmatter_type parsing
  * filter_blocks_by_ambient_trace across the AC matrix
"""
from __future__ import annotations

from depthfusion.retrieval.hybrid import (
    extract_frontmatter_type,
    filter_blocks_by_ambient_trace,
)


class TestExtractFrontmatterType:
    def test_reads_type_value(self):
        content = "---\nproject: depthfusion\ntype: ambient_trace\n---\n\nbody"
        assert extract_frontmatter_type(content) == "ambient_trace"

    def test_reads_unrelated_type_value(self):
        """type: is already used for other purposes (e.g. discoveries) — passthrough."""
        content = "---\nproject: depthfusion\ntype: decisions\n---\n\nbody"
        assert extract_frontmatter_type(content) == "decisions"

    def test_returns_none_when_no_type_key(self):
        content = "---\nproject: depthfusion\n---\n\nbody"
        assert extract_frontmatter_type(content) is None

    def test_returns_none_for_empty_string(self):
        assert extract_frontmatter_type("") is None

    def test_returns_none_for_plain_markdown(self):
        content = "# A memory file\n\nSome notes."
        assert extract_frontmatter_type(content) is None


def _block(chunk_id: str, type_line: str | None) -> dict:
    fm_lines = ["---", "project: depthfusion"]
    if type_line is not None:
        fm_lines.append(f"type: {type_line}")
    fm_lines.extend(["---", "", "body"])
    return {"chunk_id": chunk_id, "content": "\n".join(fm_lines), "source": "discovery"}


class TestFilterBlocksByAmbientTrace:
    def test_excludes_ambient_trace_by_default(self):
        blocks = [_block("a", "ambient_trace"), _block("b", "decisions"), _block("c", None)]
        result = filter_blocks_by_ambient_trace(blocks)
        ids = {b["chunk_id"] for b in result}
        assert ids == {"b", "c"}

    def test_include_ambient_trace_true_returns_all(self):
        blocks = [_block("a", "ambient_trace"), _block("b", "decisions")]
        result = filter_blocks_by_ambient_trace(blocks, include_ambient_trace=True)
        assert len(result) == 2

    def test_unlabelled_blocks_always_included(self):
        """Back-compat: a block with no `type:` key at all is never excluded."""
        blocks = [_block("a", None)]
        result = filter_blocks_by_ambient_trace(blocks)
        assert len(result) == 1

    def test_type_set_directly_on_block_dict_takes_precedence(self):
        """Mirrors project/sub_scope: block["type"] set at file-load time wins
        over re-parsing frontmatter from content (which may have been
        stripped by section-splitting)."""
        block = {"chunk_id": "x", "type": "ambient_trace", "content": "no frontmatter here"}
        assert filter_blocks_by_ambient_trace([block]) == []

    def test_block_dict_type_none_falls_back_to_frontmatter_parse(self):
        block = _block("y", "ambient_trace")
        assert filter_blocks_by_ambient_trace([block]) == []

    def test_other_type_values_are_unaffected(self):
        blocks = [_block("a", "decisions"), _block("b", "session")]
        result = filter_blocks_by_ambient_trace(blocks)
        assert len(result) == 2

    def test_empty_blocks_list(self):
        assert filter_blocks_by_ambient_trace([]) == []
