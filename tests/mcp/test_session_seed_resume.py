"""Tests for depthfusion_session_seed(mode="resume") — S-250 AC-3.

Covers:
- project_slug required
- named checkpoint_id lookup vs most-recent-when-omitted
- "No checkpoint found" error path
- recovery_command present iff git_stash_ref is set
- resume-mode failure degrades to an error dict, never raises
"""
from __future__ import annotations

import json
from unittest.mock import patch

from depthfusion.core.event_store import CheckpointRecord


def _checkpoint(**overrides) -> CheckpointRecord:
    base = dict(
        checkpoint_id="cp-1",
        session_id="sess-1",
        project_slug="depthfusion",
        created_at="2026-08-05T00:00:00+00:00",
        plan_state="working on S-250",
        files_modified=["a.py"],
        git_stash_ref=None,
        context_pct_at_checkpoint=None,
    )
    base.update(overrides)
    return CheckpointRecord(**base)


class _FakeStore:
    def __init__(self, checkpoints: dict[str, CheckpointRecord], latest: CheckpointRecord | None):
        self._checkpoints = checkpoints
        self._latest = latest

    def get_checkpoint(self, checkpoint_id, project_slug):
        return self._checkpoints.get(checkpoint_id)

    def get_latest_checkpoint(self, project_slug, session_id=None):
        return self._latest


def _patched_recall_helpers():
    return patch.multiple(
        "depthfusion.hooks.session_start",
        _detect_project_name=lambda cwd: "depthfusion",
        _recent_git_messages=lambda cwd, n=5: [],
        _build_seed_query=lambda name, msgs: "seed query",
        _recall_and_seed=lambda session_id, top_k=3, snippet_len=800: 2,
    )


class TestSessionSeedResumeMode:
    def test_project_slug_required(self):
        from depthfusion.mcp.tools.project import _tool_session_seed

        result = json.loads(_tool_session_seed({
            "session_id": "sess-1", "mode": "resume",
        }))
        assert "error" in result
        assert "project_slug" in result["error"]

    def test_resumes_from_most_recent_when_checkpoint_id_omitted(self):
        from depthfusion.mcp.tools.project import _tool_session_seed

        latest = _checkpoint(checkpoint_id="cp-latest")
        store = _FakeStore(checkpoints={"cp-latest": latest}, latest=latest)

        with patch(
            "depthfusion.mcp.tools.project._get_fabric_store", return_value=store,
        ), _patched_recall_helpers():
            result = json.loads(_tool_session_seed({
                "session_id": "sess-1",
                "mode": "resume",
                "project_slug": "depthfusion",
            }))

        assert result["checkpoint"]["checkpoint_id"] == "cp-latest"
        assert result["published"] == 2
        assert result["query"] == "seed query"
        assert "recovery_command" not in result

    def test_resumes_from_named_checkpoint_id(self):
        from depthfusion.mcp.tools.project import _tool_session_seed

        named = _checkpoint(checkpoint_id="cp-named", plan_state="named checkpoint")
        latest = _checkpoint(checkpoint_id="cp-other", plan_state="not this one")
        store = _FakeStore(
            checkpoints={"cp-named": named, "cp-other": latest}, latest=latest,
        )

        with patch(
            "depthfusion.mcp.tools.project._get_fabric_store", return_value=store,
        ), _patched_recall_helpers():
            result = json.loads(_tool_session_seed({
                "session_id": "sess-1",
                "mode": "resume",
                "project_slug": "depthfusion",
                "checkpoint_id": "cp-named",
            }))

        assert result["checkpoint"]["checkpoint_id"] == "cp-named"
        assert result["checkpoint"]["plan_state"] == "named checkpoint"

    def test_no_checkpoint_found_returns_error_not_exception(self):
        from depthfusion.mcp.tools.project import _tool_session_seed

        store = _FakeStore(checkpoints={}, latest=None)

        with patch("depthfusion.mcp.tools.project._get_fabric_store", return_value=store):
            result = json.loads(_tool_session_seed({
                "session_id": "sess-1",
                "mode": "resume",
                "project_slug": "depthfusion",
            }))

        assert result["checkpoint"] is None
        assert "error" in result

    def test_recovery_command_present_when_git_stash_ref_set(self):
        from depthfusion.mcp.tools.project import _tool_session_seed

        latest = _checkpoint(git_stash_ref="stash@{0}")
        store = _FakeStore(checkpoints={"cp-1": latest}, latest=latest)

        with patch(
            "depthfusion.mcp.tools.project._get_fabric_store", return_value=store,
        ), _patched_recall_helpers():
            result = json.loads(_tool_session_seed({
                "session_id": "sess-1",
                "mode": "resume",
                "project_slug": "depthfusion",
            }))

        assert result["recovery_command"] == "git stash pop stash@{0}"

    def test_store_exception_degrades_to_error_dict(self):
        from depthfusion.mcp.tools.project import _tool_session_seed

        class BrokenStore:
            def get_latest_checkpoint(self, project_slug, session_id=None):
                raise RuntimeError("store unavailable")

        with patch(
            "depthfusion.mcp.tools.project._get_fabric_store", return_value=BrokenStore(),
        ):
            result = json.loads(_tool_session_seed({
                "session_id": "sess-1",
                "mode": "resume",
                "project_slug": "depthfusion",
            }))

        assert result["checkpoint"] is None
        assert "error" in result
