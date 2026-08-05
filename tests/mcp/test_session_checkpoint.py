"""Tests for depthfusion_session_checkpoint — E-73 S-250/S-254 T-850.

Covers:
- Tool registration (TOOLS / TOOL_SCHEMAS / DISPATCHABLE / _TOOL_FLAGS parity)
- Round-trip persistence via a real EventStore/JSONGraphStore (mirrors the
  TestRealCollaborators shape in tests/core/test_event_store.py) — the
  checkpoint published through the MCP tool must be the same record
  EventStore.get_checkpoint / get_latest_checkpoint and
  depthfusion_session_seed(mode="resume") hand back.
- Invalid-argument handling degrades to a clean error dict, never raises.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from depthfusion.core.event_store import EventStore, InMemoryStreamBackend
from depthfusion.graph.store import JSONGraphStore


class TestToolRegistration:
    def test_registered_in_tools_and_schemas(self):
        from depthfusion.mcp.tools._registry import TOOL_SCHEMAS, TOOLS

        assert "depthfusion_session_checkpoint" in TOOLS
        assert "depthfusion_session_checkpoint" in TOOL_SCHEMAS
        schema = TOOL_SCHEMAS["depthfusion_session_checkpoint"]
        assert set(schema["required"]) == {
            "session_id", "project_slug", "plan_state", "files_modified",
        }

    def test_dispatchable_and_invokable(self):
        from depthfusion.mcp.server import DISPATCHABLE

        assert "depthfusion_session_checkpoint" in DISPATCHABLE

    def test_always_enabled(self):
        from depthfusion.mcp.tools._registry import _TOOL_FLAGS, get_enabled_tools

        assert _TOOL_FLAGS["depthfusion_session_checkpoint"] is None
        enabled = get_enabled_tools(config=object())
        assert "depthfusion_session_checkpoint" in enabled

    def test_dispatch_tool_invokes_implementation(self):
        from depthfusion.mcp.server import _dispatch_tool

        with patch(
            "depthfusion.mcp.server._tool_session_checkpoint",
            return_value='{"checkpoint_id": "cp-x"}',
        ) as mocked:
            result = _dispatch_tool(
                "depthfusion_session_checkpoint",
                {"session_id": "s1", "project_slug": "depthfusion",
                 "plan_state": "x", "files_modified": []},
                config=object(),
            )
        mocked.assert_called_once()
        assert json.loads(result) == {"checkpoint_id": "cp-x"}


@pytest.fixture
def real_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("DEPTHFUSION_LOCK_DIR", str(tmp_path / "locks"))
    graph = JSONGraphStore(path=tmp_path / "graph.json")
    stream = InMemoryStreamBackend()
    store = EventStore(graph=graph, stream=stream)
    return store


def _patched_recall_helpers():
    return patch.multiple(
        "depthfusion.hooks.session_start",
        _detect_project_name=lambda cwd: "depthfusion",
        _recent_git_messages=lambda cwd, n=5: [],
        _build_seed_query=lambda name, msgs: "seed query",
        _recall_and_seed=lambda session_id, top_k=3, snippet_len=800: 0,
    )


class TestRealCollaborators:
    """Real EventStore + real JSONGraphStore + real filesystem — no mocks of
    the persistence path. Only the recall-seed helpers (unrelated to
    checkpointing) are stubbed, matching tests/mcp/test_session_seed_resume.py.
    """

    def test_invocation_persists_real_checkpoint_retrievable_by_get_checkpoint(
        self, real_store,
    ):
        from depthfusion.mcp.tools.project import _tool_session_checkpoint

        with patch(
            "depthfusion.mcp.tools.project._get_fabric_store", return_value=real_store,
        ):
            raw = _tool_session_checkpoint({
                "session_id": "sess-mcp-1",
                "project_slug": "depthfusion",
                "plan_state": "implementing T-850 MCP checkpoint tool",
                "files_modified": ["src/depthfusion/mcp/tools/project.py"],
                "git_stash_ref": "stash@{0}",
                "context_pct_at_checkpoint": 42.5,
            })

        result = json.loads(raw)
        assert "error" not in result
        checkpoint_id = result["checkpoint_id"]
        assert result["session_id"] == "sess-mcp-1"
        assert result["project_slug"] == "depthfusion"
        assert result["created_at"]

        fetched = real_store.get_checkpoint(checkpoint_id, "depthfusion")
        assert fetched is not None
        assert fetched.plan_state == "implementing T-850 MCP checkpoint tool"
        assert fetched.files_modified == ["src/depthfusion/mcp/tools/project.py"]
        assert fetched.git_stash_ref == "stash@{0}"
        assert fetched.context_pct_at_checkpoint == 42.5

    def test_invocation_persists_checkpoint_retrievable_by_get_latest_checkpoint(
        self, real_store,
    ):
        from depthfusion.mcp.tools.project import _tool_session_checkpoint

        with patch(
            "depthfusion.mcp.tools.project._get_fabric_store", return_value=real_store,
        ):
            raw = _tool_session_checkpoint({
                "session_id": "sess-mcp-2",
                "project_slug": "depthfusion",
                "plan_state": "second checkpoint",
                "files_modified": [],
            })
        result = json.loads(raw)

        latest = real_store.get_latest_checkpoint("depthfusion")
        assert latest is not None
        assert latest.checkpoint_id == result["checkpoint_id"]
        assert latest.plan_state == "second checkpoint"
        assert latest.git_stash_ref is None
        assert latest.context_pct_at_checkpoint is None

    def test_invocation_retrievable_via_session_seed_resume(self, real_store):
        from depthfusion.mcp.tools.project import (
            _tool_session_checkpoint,
            _tool_session_seed,
        )

        with patch(
            "depthfusion.mcp.tools.project._get_fabric_store", return_value=real_store,
        ):
            publish_raw = _tool_session_checkpoint({
                "session_id": "sess-mcp-3",
                "project_slug": "depthfusion",
                "plan_state": "checkpoint before compact",
                "files_modified": ["a.py", "b.py"],
                "git_stash_ref": "stash@{0}",
            })
            checkpoint_id = json.loads(publish_raw)["checkpoint_id"]

            with _patched_recall_helpers():
                resume_raw = _tool_session_seed({
                    "session_id": "sess-mcp-3",
                    "mode": "resume",
                    "project_slug": "depthfusion",
                })

        resumed = json.loads(resume_raw)
        assert resumed["checkpoint"]["checkpoint_id"] == checkpoint_id
        assert resumed["checkpoint"]["plan_state"] == "checkpoint before compact"
        assert resumed["checkpoint"]["files_modified"] == ["a.py", "b.py"]
        assert resumed["recovery_command"] == "git stash pop stash@{0}"


class TestInvalidArguments:
    def test_missing_session_id(self):
        from depthfusion.mcp.tools.project import _tool_session_checkpoint

        result = json.loads(_tool_session_checkpoint({
            "project_slug": "depthfusion",
            "plan_state": "x",
            "files_modified": [],
        }))
        assert result == {"error": "session_id is required"}

    def test_missing_project_slug(self):
        from depthfusion.mcp.tools.project import _tool_session_checkpoint

        result = json.loads(_tool_session_checkpoint({
            "session_id": "s1",
            "plan_state": "x",
            "files_modified": [],
        }))
        assert result == {"error": "project_slug is required"}

    def test_missing_plan_state(self):
        from depthfusion.mcp.tools.project import _tool_session_checkpoint

        result = json.loads(_tool_session_checkpoint({
            "session_id": "s1",
            "project_slug": "depthfusion",
            "files_modified": [],
        }))
        assert result == {"error": "plan_state is required"}

    def test_blank_plan_state_rejected(self):
        from depthfusion.mcp.tools.project import _tool_session_checkpoint

        result = json.loads(_tool_session_checkpoint({
            "session_id": "s1",
            "project_slug": "depthfusion",
            "plan_state": "   ",
            "files_modified": [],
        }))
        assert result == {"error": "plan_state is required"}

    def test_files_modified_missing(self):
        from depthfusion.mcp.tools.project import _tool_session_checkpoint

        result = json.loads(_tool_session_checkpoint({
            "session_id": "s1",
            "project_slug": "depthfusion",
            "plan_state": "x",
        }))
        assert result == {"error": "files_modified is required and must be a list"}

    def test_files_modified_wrong_type(self):
        from depthfusion.mcp.tools.project import _tool_session_checkpoint

        result = json.loads(_tool_session_checkpoint({
            "session_id": "s1",
            "project_slug": "depthfusion",
            "plan_state": "x",
            "files_modified": "not-a-list",
        }))
        assert result == {"error": "files_modified is required and must be a list"}

    def test_invalid_context_pct_type(self):
        from depthfusion.mcp.tools.project import _tool_session_checkpoint

        result = json.loads(_tool_session_checkpoint({
            "session_id": "s1",
            "project_slug": "depthfusion",
            "plan_state": "x",
            "files_modified": [],
            "context_pct_at_checkpoint": "not-a-number",
        }))
        assert result == {"error": "context_pct_at_checkpoint must be a number"}

    def test_store_exception_degrades_to_error_dict(self):
        from depthfusion.mcp.tools.project import _tool_session_checkpoint

        class BrokenStore:
            async def publish_checkpoint(self, **kwargs):
                raise RuntimeError("store unavailable")

        with patch(
            "depthfusion.mcp.tools.project._get_fabric_store",
            return_value=BrokenStore(),
        ):
            result = json.loads(_tool_session_checkpoint({
                "session_id": "s1",
                "project_slug": "depthfusion",
                "plan_state": "x",
                "files_modified": [],
            }))

        assert "error" in result
        assert "store unavailable" in result["error"]
