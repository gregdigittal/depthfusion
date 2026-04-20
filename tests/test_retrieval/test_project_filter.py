# tests/test_retrieval/test_project_filter.py
"""Project-scoped recall filter tests — S-52 / T-162.

≥ 5 tests required by S-52 AC-4. Covers:
  * extract_frontmatter_project parsing
  * filter_blocks_by_project across the AC matrix
  * MCP _tool_recall integration: cross_project default off, explicit project,
    and backward-compat (no-frontmatter files always included)
"""
from __future__ import annotations

import json

import pytest

from depthfusion.retrieval.hybrid import (
    extract_frontmatter_project,
    filter_blocks_by_project,
)

# ---------------------------------------------------------------------------
# extract_frontmatter_project
# ---------------------------------------------------------------------------

class TestExtractFrontmatterProject:
    def test_reads_project_slug(self):
        content = "---\nproject: depthfusion\ntype: decisions\n---\n\nbody"
        assert extract_frontmatter_project(content) == "depthfusion"

    def test_returns_none_when_no_project_key(self):
        content = "---\ntype: decisions\n---\n\nbody"
        assert extract_frontmatter_project(content) is None

    def test_returns_none_for_empty_string(self):
        assert extract_frontmatter_project("") is None

    def test_returns_none_for_plain_markdown(self):
        """Files with no frontmatter at all — typical pre-v0.5 memory files."""
        content = "# A memory file\n\nSome notes about the system."
        assert extract_frontmatter_project(content) is None

    def test_handles_extra_whitespace(self):
        content = "---\nproject:   myapp   \n---\nbody"
        assert extract_frontmatter_project(content) == "myapp"

    def test_stops_at_first_match(self):
        """Multiple project: lines — first one wins."""
        content = (
            "---\nproject: first\ntype: x\n---\n\n"
            "## aside\n\nproject: second (mentioned in body, not frontmatter)"
        )
        assert extract_frontmatter_project(content) == "first"


# ---------------------------------------------------------------------------
# filter_blocks_by_project — the AC matrix
# ---------------------------------------------------------------------------

def _block(chunk_id: str, project_line: str | None, extra: str = "") -> dict:
    """Build a test block dict with optional `project:` frontmatter."""
    fm_lines = ["---"]
    if project_line is not None:
        fm_lines.append(f"project: {project_line}")
    fm_lines.extend(["type: decisions", "---", "", extra or "body"])
    return {"chunk_id": chunk_id, "content": "\n".join(fm_lines), "source": "discovery"}


class TestFilterBlocksByProject:
    def test_cross_project_true_returns_all(self):
        """AC-2: v0.4.x behaviour preserved when cross_project=True."""
        blocks = [
            _block("a", "proja"),
            _block("b", "projb"),
            _block("c", None),
        ]
        result = filter_blocks_by_project(
            blocks, current_project="proja", cross_project=True,
        )
        assert len(result) == 3

    def test_default_filters_out_other_projects(self):
        """AC-1: default recall in project A does not return discoveries tagged project: B."""
        blocks = [
            _block("a", "proja"),
            _block("b", "projb"),
            _block("c", "proja"),
        ]
        result = filter_blocks_by_project(blocks, current_project="proja")
        ids = [b["chunk_id"] for b in result]
        assert "a" in ids and "c" in ids
        assert "b" not in ids

    def test_no_frontmatter_always_included(self):
        """AC-3: discoveries without frontmatter are treated as cross-project."""
        blocks = [
            _block("a", "proja"),
            _block("b", "projb"),
            _block("c", None),           # no project key — legacy file
            {"chunk_id": "d", "content": "raw markdown, no frontmatter at all"},
            {"chunk_id": "e", "content": ""},  # empty content also permissive
        ]
        result = filter_blocks_by_project(blocks, current_project="proja")
        ids = sorted(b["chunk_id"] for b in result)
        assert ids == ["a", "c", "d", "e"]

    def test_current_project_none_returns_all(self):
        """No project context (e.g. recall outside a git repo) → no filtering."""
        blocks = [
            _block("a", "proja"),
            _block("b", "projb"),
        ]
        result = filter_blocks_by_project(blocks, current_project=None)
        assert len(result) == 2

    def test_empty_blocks_list_returns_empty(self):
        result = filter_blocks_by_project([], current_project="proja")
        assert result == []

    def test_block_missing_content_treated_as_projectless(self):
        """Defensive: a block without a `content` key is included, not crashed."""
        blocks = [{"chunk_id": "x", "source": "session"}]
        result = filter_blocks_by_project(blocks, current_project="proja")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# MCP _tool_recall integration
# ---------------------------------------------------------------------------

class TestRecallToolProjectFiltering:
    """Integration: _tool_recall respects cross_project + explicit project args."""

    def test_cross_project_true_returns_blocks_from_all_projects(self, tmp_path, monkeypatch):
        """AC-2: With cross_project=True, blocks from every project appear.

        Also asserts detect_project is NOT called — verified via outcome
        (both project files appear) rather than a vacuous mock check,
        because the lazy import pattern in _tool_recall makes some patch
        targets unreliable.
        """
        fake_home = tmp_path / "home"
        (fake_home / ".claude" / "shared" / "discoveries").mkdir(parents=True)
        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake_home))

        # Write one file for each project — both must appear in output.
        disc = fake_home / ".claude" / "shared" / "discoveries"
        (disc / "a.md").write_text(
            "---\nproject: proja\ntype: decisions\n---\n"
            "## redis\n\nUse redis for caching",
            encoding="utf-8",
        )
        (disc / "b.md").write_text(
            "---\nproject: projb\ntype: decisions\n---\n"
            "## redis\n\nUse redis for session storage",
            encoding="utf-8",
        )

        # Defense in depth: even if detect_project WERE called, patching it
        # to return a project that doesn't match either file would filter
        # everything out. By returning "none-of-these" we prove that the
        # cross_project=True branch short-circuits the filter: if it didn't,
        # the result would be empty.
        import depthfusion.hooks.git_post_commit as gpc
        monkeypatch.setattr(gpc, "detect_project", lambda *a, **kw: "noproject")

        from depthfusion.mcp.server import _tool_recall
        result_json = _tool_recall({
            "query": "redis caching",
            "top_k": 10,
            "cross_project": True,
        })
        result = json.loads(result_json)
        chunk_ids = [b["chunk_id"] for b in result["blocks"]]
        # Both project files must be present — proves filter was skipped
        assert any("a" in cid for cid in chunk_ids)
        assert any("b" in cid for cid in chunk_ids)

    def test_default_filters_to_explicit_project(self, tmp_path, monkeypatch):
        """Passing project='proja' filters out projb blocks."""
        fake_home = tmp_path / "home"
        (fake_home / ".claude" / "shared" / "discoveries").mkdir(parents=True)
        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake_home))

        disc = fake_home / ".claude" / "shared" / "discoveries"
        (disc / "a.md").write_text(
            "---\nproject: proja\ntype: decisions\n---\n"
            "## redis\n\nUse redis for caching",
            encoding="utf-8",
        )
        (disc / "b.md").write_text(
            "---\nproject: projb\ntype: decisions\n---\n"
            "## redis\n\nUse redis for session storage",
            encoding="utf-8",
        )

        from depthfusion.mcp.server import _tool_recall
        result_json = _tool_recall({
            "query": "redis caching",
            "top_k": 10,
            "project": "proja",
        })
        result = json.loads(result_json)
        chunk_ids = [b["chunk_id"] for b in result["blocks"]]
        # Only project A should appear
        assert any("a" in cid for cid in chunk_ids)
        assert not any(cid.startswith("b") for cid in chunk_ids)

    def test_filter_returns_helpful_message_when_no_matches(self, tmp_path, monkeypatch):
        """When filter zeroes out all blocks, surface a message pointing at cross_project."""
        fake_home = tmp_path / "home"
        (fake_home / ".claude" / "shared" / "discoveries").mkdir(parents=True)
        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake_home))

        (fake_home / ".claude" / "shared" / "discoveries" / "b.md").write_text(
            "---\nproject: projb\ntype: decisions\n---\n"
            "## anything\n\nsome content",
            encoding="utf-8",
        )

        from depthfusion.mcp.server import _tool_recall
        result_json = _tool_recall({
            "query": "anything",
            "project": "proja",
        })
        result = json.loads(result_json)
        assert result["blocks"] == []
        assert "cross_project=true" in result["message"]

    def test_legacy_memory_files_returned_regardless_of_project(self, tmp_path, monkeypatch):
        """AC-3: memory files without frontmatter must still appear."""
        fake_home = tmp_path / "home"
        (fake_home / ".claude" / "shared" / "discoveries").mkdir(parents=True)
        mem_dir = fake_home / ".claude" / "projects" / "-home-gregmorris" / "memory"
        mem_dir.mkdir(parents=True)
        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake_home))

        # Legacy memory file — no frontmatter at all
        (mem_dir / "preferences.md").write_text(
            "## Preferences\n\nUse redis when possible for caching layers.",
            encoding="utf-8",
        )
        # Cross-project discovery that should be filtered out
        disc = fake_home / ".claude" / "shared" / "discoveries"
        (disc / "other.md").write_text(
            "---\nproject: projb\n---\n## redis\n\nUse redis\n",
            encoding="utf-8",
        )

        from depthfusion.mcp.server import _tool_recall
        result_json = _tool_recall({
            "query": "redis caching",
            "project": "proja",
        })
        result = json.loads(result_json)
        chunk_ids = [b["chunk_id"] for b in result["blocks"]]
        # Legacy memory file must appear; projb discovery must not
        assert any("preferences" in cid for cid in chunk_ids)
        assert not any("other" in cid for cid in chunk_ids)


# ---------------------------------------------------------------------------
# MCP tool schema surfaces the new parameters
# ---------------------------------------------------------------------------

def test_tool_description_documents_cross_project_param():
    """Schema description must mention the new cross_project + project args."""
    from depthfusion.mcp.server import TOOLS
    desc = TOOLS["depthfusion_recall_relevant"]
    assert "cross_project" in desc
    assert "project" in desc


# ---------------------------------------------------------------------------
# Review-gate regressions
# ---------------------------------------------------------------------------

class TestReviewGateRegressions:
    def test_detect_project_returning_unknown_does_not_hide_all_blocks(
        self, tmp_path, monkeypatch,
    ):
        """Regression: in a bare MCP client (no git remote, no env var),
        detect_project() returns the literal string 'unknown'. Filtering
        against that would zero out every real discovery. The fix treats
        'unknown' as 'no project context' and skips the filter entirely.
        """
        fake_home = tmp_path / "home"
        (fake_home / ".claude" / "shared" / "discoveries").mkdir(parents=True)
        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake_home))

        disc = fake_home / ".claude" / "shared" / "discoveries"
        (disc / "a.md").write_text(
            "---\nproject: proja\ntype: decisions\n---\n"
            "## redis\n\nUse redis for caching",
            encoding="utf-8",
        )

        import depthfusion.hooks.git_post_commit as gpc
        monkeypatch.setattr(gpc, "detect_project", lambda *a, **kw: "unknown")

        from depthfusion.mcp.server import _tool_recall
        result_json = _tool_recall({"query": "redis caching"})
        result = json.loads(result_json)
        # Must NOT return the zero-blocks "no context found for project 'unknown'"
        # response. The file IS tagged with proja, and there's no real project
        # context to filter against, so it should appear.
        assert len(result["blocks"]) >= 1
        chunk_ids = [b["chunk_id"] for b in result["blocks"]]
        assert any("a" in cid for cid in chunk_ids)

    def test_explicit_project_slug_is_sanitised(self, tmp_path, monkeypatch):
        """Path-traversal guard: `project="../other"` must be sanitised to
        "other" (or similar safe slug) before use, matching the allowlist
        that detect_project() applies internally.
        """
        fake_home = tmp_path / "home"
        (fake_home / ".claude" / "shared" / "discoveries").mkdir(parents=True)
        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake_home))

        # Only a file tagged project=other exists.
        disc = fake_home / ".claude" / "shared" / "discoveries"
        (disc / "only.md").write_text(
            "---\nproject: other\ntype: decisions\n---\n"
            "## topic\n\nSome content about topic",
            encoding="utf-8",
        )

        from depthfusion.mcp.server import _tool_recall
        # Malicious slug: `../other` — after sanitisation becomes `other`.
        result_json = _tool_recall({
            "query": "topic content",
            "project": "../other",
        })
        result = json.loads(result_json)
        chunk_ids = [b["chunk_id"] for b in result["blocks"]]
        # The file tagged `project: other` must match the sanitised slug.
        assert any("only" in cid for cid in chunk_ids)

    def test_sanitise_slug_helper_strips_traversal(self):
        """Direct unit test on the helper for tightly-scoped coverage."""
        from depthfusion.mcp.server import _sanitise_project_slug

        assert _sanitise_project_slug("../../etc") == "etc"
        assert _sanitise_project_slug("/absolute/path") == "absolute-path"
        assert _sanitise_project_slug("Project Name!") == "project-name"
        # Long slugs capped at 40 chars
        assert len(_sanitise_project_slug("a" * 100)) == 40
        # Pure separators → empty (signals "no project provided")
        assert _sanitise_project_slug("///...///") == ""
        assert _sanitise_project_slug("") == ""

    def test_frontmatter_regex_ignores_body_prose(self):
        """A discovery file whose BODY contains a `project:` line (e.g. in a
        code snippet) must not override the real frontmatter tag.
        """
        content = (
            "---\nproject: realtag\ntype: decisions\n---\n\n"
            "## code example\n\n"
            "    project: fake-from-body\n\n"
            "And more text."
        )
        assert extract_frontmatter_project(content) == "realtag"

    def test_frontmatter_regex_requires_opening_block(self):
        """A file without the opening `---\\n...\\n---` block returns None
        even if the body contains `project: X`.
        """
        content = "# A plain memory file\n\nproject: looks-like-frontmatter-but-isnt"
        assert extract_frontmatter_project(content) is None


@pytest.mark.parametrize("arg_name", ["cross_project", "project"])
def test_recall_accepts_new_args_without_error(arg_name, tmp_path, monkeypatch):
    """Smoke test: passing the new args must not raise or KeyError."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake_home))

    from depthfusion.mcp.server import _tool_recall
    args = {"query": "anything"}
    if arg_name == "cross_project":
        args["cross_project"] = True
    else:
        args["project"] = "nothing"
    result_json = _tool_recall(args)
    # Must return valid JSON (the no-context message, probably)
    data = json.loads(result_json)
    assert "blocks" in data
