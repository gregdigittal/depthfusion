"""Tests for S-111: SessionStart auto-recall seed hook."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.hooks.session_start import (
    _build_seed_query,
    _detect_project_name,
    _recent_git_messages,
    handle_session_start,
)
from depthfusion.router.bus import FileBus


# ---------------------------------------------------------------------------
# Project detection helpers
# ---------------------------------------------------------------------------

class TestDetectProjectName:
    def test_uses_git_remote_repo_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="https://github.com/acme/myproject.git\n"
            )
            name = _detect_project_name(tmp_path)
        assert name == "myproject"

    def test_strips_dot_git_suffix(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="git@github.com:acme/repo.git\n"
            )
            name = _detect_project_name(tmp_path)
        assert name == "repo"

    def test_falls_back_to_dir_name_on_git_failure(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            name = _detect_project_name(tmp_path)
        assert name == tmp_path.name

    def test_falls_back_to_dir_name_on_exception(self, tmp_path):
        with patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            name = _detect_project_name(tmp_path)
        assert name == tmp_path.name


class TestRecentGitMessages:
    def test_returns_commit_messages(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="feat: add thing\nfix: bug\n"
            )
            messages = _recent_git_messages(tmp_path)
        assert messages == ["feat: add thing", "fix: bug"]

    def test_returns_empty_on_git_failure(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            messages = _recent_git_messages(tmp_path)
        assert messages == []

    def test_returns_empty_on_exception(self, tmp_path):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 3)):
            messages = _recent_git_messages(tmp_path)
        assert messages == []


class TestBuildSeedQuery:
    def test_includes_project_name(self):
        query = _build_seed_query("myproject", [])
        assert "myproject" in query

    def test_includes_recent_commits(self):
        query = _build_seed_query("proj", ["feat: add auth", "fix: null check"])
        assert "feat: add auth" in query
        assert "fix: null check" in query

    def test_caps_commits_at_3(self):
        messages = [f"commit {i}" for i in range(10)]
        query = _build_seed_query("proj", messages)
        assert "commit 0" in query
        assert "commit 1" in query
        assert "commit 2" in query
        assert "commit 9" not in query


# ---------------------------------------------------------------------------
# handle_session_start — feature flag and graceful degradation
# ---------------------------------------------------------------------------

class TestHandleSessionStart:
    def test_disabled_by_flag_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AUTO_RECALL_AT_SESSION_START", "false")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        handle_session_start({"session_id": "s1"})
        assert list(tmp_path.glob("*.jsonl")) == []

    def test_no_exception_on_unreachable_recall(self, tmp_path, monkeypatch):
        """AC-5: hook exits 0 when DepthFusion recall is unavailable."""
        monkeypatch.setenv("DEPTHFUSION_AUTO_RECALL_AT_SESSION_START", "true")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        with patch(
            "depthfusion.hooks.session_start._recall_and_seed",
            side_effect=ConnectionError("server down"),
        ):
            handle_session_start({"session_id": "s2"})
        # No exception raised — AC-5 satisfied

    def test_no_exception_on_empty_payload(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AUTO_RECALL_AT_SESSION_START", "true")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))
        with patch(
            "depthfusion.hooks.session_start._recall_and_seed",
            side_effect=RuntimeError("no recall"),
        ):
            handle_session_start({})

    def test_published_items_have_correct_tags(self, tmp_path, monkeypatch):
        """AC-2 + AC-3: seed items have ['session-seed', session_id] and importance=0.9."""
        monkeypatch.setenv("DEPTHFUSION_AUTO_RECALL_AT_SESSION_START", "true")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))

        # Fake recall returning one block
        mock_recall_result = json.dumps({
            "blocks": [{"snippet": "some recalled context", "chunk_id": "c1"}]
        })
        with patch(
            "depthfusion.mcp.server._tool_recall_impl",
            return_value=mock_recall_result,
        ):
            handle_session_start({"session_id": "test-session-123"})

        results = FileBus(bus_dir=tmp_path).subscribe(["session-seed"])
        assert len(results) == 1
        item = results[0]
        assert "session-seed" in item.tags
        assert "test-session-123" in item.tags
        assert item.importance == pytest.approx(0.9)
        assert item.source_agent == "depthfusion-session-seed"

    def test_respects_top_k_env(self, tmp_path, monkeypatch):
        """top_k is passed through from env var."""
        monkeypatch.setenv("DEPTHFUSION_AUTO_RECALL_AT_SESSION_START", "true")
        monkeypatch.setenv("DEPTHFUSION_AUTO_RECALL_TOP_K", "2")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))

        captured = {}

        def fake_recall_impl(args):
            captured["top_k"] = args.get("top_k")
            return json.dumps({"blocks": []})

        with patch("depthfusion.mcp.server._tool_recall_impl", side_effect=fake_recall_impl):
            handle_session_start({"session_id": "s3"})

        assert captured.get("top_k") == 2

    def test_uses_sessionid_from_payload(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_AUTO_RECALL_AT_SESSION_START", "true")
        monkeypatch.setenv("DEPTHFUSION_BUS_FILE_DIR", str(tmp_path))

        mock_recall_result = json.dumps({
            "blocks": [{"snippet": "context block"}]
        })
        with patch("depthfusion.mcp.server._tool_recall_impl", return_value=mock_recall_result):
            handle_session_start({"session_id": "my-unique-session"})

        results = FileBus(bus_dir=tmp_path).subscribe(["session-seed"])
        assert len(results) == 1
        assert "my-unique-session" in results[0].tags


# ---------------------------------------------------------------------------
# MCP tool schema
# ---------------------------------------------------------------------------

class TestSessionSeedSchema:
    def test_session_seed_has_session_id_required(self):
        from depthfusion.mcp.server import _make_tool_schema
        schema = _make_tool_schema("depthfusion_session_seed", "desc")
        assert "session_id" in schema["inputSchema"]["required"]

    def test_session_seed_has_top_k_and_snippet_len(self):
        from depthfusion.mcp.server import _make_tool_schema
        schema = _make_tool_schema("depthfusion_session_seed", "desc")
        props = schema["inputSchema"]["properties"]
        assert "top_k" in props
        assert props["top_k"]["minimum"] == 1
        assert "snippet_len" in props
        assert props["snippet_len"]["minimum"] == 200

    def test_session_seed_in_enabled_tools(self):
        from depthfusion.mcp.server import get_enabled_tools
        from depthfusion.core.config import DepthFusionConfig
        config = DepthFusionConfig()
        tools = get_enabled_tools(config)
        assert "depthfusion_session_seed" in tools
