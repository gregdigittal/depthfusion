"""Tests for S-250 session checkpointing wired into mcp/http_server.py.

Covers:
- T-848: DELETE /mcp publishes a checkpoint BEFORE the session is popped
  from `_MCP_STREAMABLE_SESSIONS`, and teardown proceeds even if the
  checkpoint publish fails.
- T-852: the FastAPI lifespan's shutdown path checkpoints every still-open
  streamable-http session.
- `_detect_git_stash_ref`: read-only, never creates a stash.
- `_publish_session_checkpoint`: skips when there is no project context,
  and never raises out of a failure.

These call the endpoint / lifespan functions directly (no real network
bind — consistent with tests/mcp/test_dispatch_parity.py and
tests/core/test_hygiene_scheduler.py's `TestLifespanIntegration`, which
call `_lifespan(app)` directly rather than starting uvicorn).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException


class _FakeHeaders(dict):
    def get(self, key, default=None):  # noqa: A003 - mirrors Starlette Headers.get
        return super().get(key.lower(), default)


class _FakeRequest:
    def __init__(self, headers: dict | None = None) -> None:
        self.headers = _FakeHeaders({k.lower(): v for k, v in (headers or {}).items()})


# ---------------------------------------------------------------------------
# DELETE /mcp — T-848
# ---------------------------------------------------------------------------


class TestDeleteEndpointCheckpoint:
    @pytest.mark.asyncio
    async def test_missing_session_id_returns_400(self):
        from depthfusion.mcp import http_server as hs

        with pytest.raises(HTTPException) as exc_info:
            await hs.streamable_http_delete_endpoint(
                _FakeRequest({}), _principal=None, _origin=None,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_unknown_session_returns_404(self):
        from depthfusion.mcp import http_server as hs

        with pytest.raises(HTTPException) as exc_info:
            await hs.streamable_http_delete_endpoint(
                _FakeRequest({"Mcp-Session-Id": "no-such-session"}),
                _principal=None, _origin=None,
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_checkpoint_published_before_session_popped(self, monkeypatch):
        """Regression guard for the T-848 ordering requirement: the checkpoint
        publish call must see the session still present in the registry."""
        from depthfusion.mcp import http_server as hs

        session_id = "sess-order-check"
        hs._MCP_STREAMABLE_SESSIONS[session_id] = asyncio.Queue()

        calls: list[str] = []

        async def fake_checkpoint(sid: str) -> None:
            calls.append(sid)
            # This is the load-bearing assertion: if the pop() ever moves
            # before the checkpoint call, this fails.
            assert sid in hs._MCP_STREAMABLE_SESSIONS, (
                "checkpoint publish ran AFTER the session was popped — "
                "violates T-848's 'before session is removed from dict' requirement"
            )

        monkeypatch.setattr(hs, "_publish_session_checkpoint", fake_checkpoint)

        try:
            result = await hs.streamable_http_delete_endpoint(
                _FakeRequest({"Mcp-Session-Id": session_id}),
                _principal=None, _origin=None,
            )
        finally:
            hs._MCP_STREAMABLE_SESSIONS.pop(session_id, None)

        assert result == {"ok": True}
        assert calls == [session_id]
        assert session_id not in hs._MCP_STREAMABLE_SESSIONS

    @pytest.mark.asyncio
    async def test_checkpoint_failure_does_not_block_teardown(self, monkeypatch):
        """A checkpoint publish exception must not prevent session teardown."""
        from depthfusion.mcp import http_server as hs

        session_id = "sess-checkpoint-fails"
        hs._MCP_STREAMABLE_SESSIONS[session_id] = asyncio.Queue()

        async def broken_checkpoint(sid: str) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(hs, "_publish_session_checkpoint", broken_checkpoint)

        result = await hs.streamable_http_delete_endpoint(
            _FakeRequest({"Mcp-Session-Id": session_id}),
            _principal=None, _origin=None,
        )

        assert result == {"ok": True}
        assert session_id not in hs._MCP_STREAMABLE_SESSIONS


# ---------------------------------------------------------------------------
# Lifespan shutdown checkpointing — T-852
# ---------------------------------------------------------------------------


class TestLifespanShutdownCheckpoint:
    @pytest.mark.asyncio
    async def test_open_sessions_checkpointed_on_shutdown(self, monkeypatch):
        from depthfusion.mcp import http_server as hs

        s1, s2 = "open-sess-1", "open-sess-2"
        hs._MCP_STREAMABLE_SESSIONS[s1] = asyncio.Queue()
        hs._MCP_STREAMABLE_SESSIONS[s2] = asyncio.Queue()

        checkpointed: list[str] = []

        async def fake_checkpoint(sid: str) -> None:
            checkpointed.append(sid)

        monkeypatch.setattr(hs, "_publish_session_checkpoint", fake_checkpoint)
        monkeypatch.setattr(hs, "build_scheduler", lambda: None)

        try:
            async with hs._lifespan(hs.app):
                pass
        finally:
            hs._MCP_STREAMABLE_SESSIONS.pop(s1, None)
            hs._MCP_STREAMABLE_SESSIONS.pop(s2, None)

        assert set(checkpointed) == {s1, s2}

    @pytest.mark.asyncio
    async def test_no_open_sessions_is_a_no_op(self, monkeypatch):
        from depthfusion.mcp import http_server as hs

        calls: list[str] = []

        async def fake_checkpoint(sid: str) -> None:
            calls.append(sid)

        monkeypatch.setattr(hs, "_publish_session_checkpoint", fake_checkpoint)
        monkeypatch.setattr(hs, "build_scheduler", lambda: None)

        async with hs._lifespan(hs.app):
            pass

        assert calls == []

    @pytest.mark.asyncio
    async def test_shutdown_checkpoint_error_does_not_prevent_scheduler_shutdown(
        self, monkeypatch,
    ):
        """A broken checkpoint sweep must not prevent the hygiene scheduler
        from shutting down (the pre-existing S-248 guarantee)."""
        from unittest.mock import MagicMock

        from depthfusion.mcp import http_server as hs

        mock_scheduler = MagicMock()
        monkeypatch.setattr(hs, "build_scheduler", lambda: mock_scheduler)

        async def broken_sweep() -> None:
            raise RuntimeError("sweep exploded")

        monkeypatch.setattr(hs, "_checkpoint_open_sessions_on_shutdown", broken_sweep)

        async with hs._lifespan(hs.app):
            pass

        mock_scheduler.shutdown.assert_called_once_with(wait=False)


# ---------------------------------------------------------------------------
# _detect_git_stash_ref — read-only by construction
# ---------------------------------------------------------------------------


class TestDetectGitStashRef:
    def test_returns_none_outside_a_git_repo(self, tmp_path):
        from depthfusion.mcp.http_server import _detect_git_stash_ref

        assert _detect_git_stash_ref(tmp_path) is None

    def test_never_creates_a_stash(self, tmp_path):
        """Calling this on a real repo with uncommitted changes must not
        stash anything — it is read-only by design."""
        import subprocess

        from depthfusion.mcp.http_server import _detect_git_stash_ref

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True,
        )
        subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
        (tmp_path / "a.txt").write_text("hello")
        subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
        (tmp_path / "a.txt").write_text("uncommitted change")

        ref = _detect_git_stash_ref(tmp_path)

        assert ref is None  # no existing stash — must not have created one
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True,
        )
        assert "a.txt" in status.stdout  # the uncommitted change is still there

    def test_reads_an_existing_stash_ref(self, tmp_path):
        import subprocess

        from depthfusion.mcp.http_server import _detect_git_stash_ref

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True,
        )
        subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
        (tmp_path / "a.txt").write_text("hello")
        subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
        (tmp_path / "a.txt").write_text("changed")
        subprocess.run(["git", "stash", "push", "-q"], cwd=tmp_path, check=True)

        ref = _detect_git_stash_ref(tmp_path)

        assert ref == "stash@{0}"


# ---------------------------------------------------------------------------
# _publish_session_checkpoint — project-context skip + never-raise
# ---------------------------------------------------------------------------


class TestPublishSessionCheckpoint:
    @pytest.mark.asyncio
    async def test_skips_when_project_detection_returns_unknown(self, monkeypatch):
        from depthfusion.mcp import http_server as hs

        monkeypatch.setattr(
            "depthfusion.hooks.git_post_commit.detect_project", lambda: "unknown",
        )

        calls = []
        fake_store = type("FakeStore", (), {
            "publish_checkpoint": staticmethod(
                lambda **kw: calls.append(kw) or asyncio.sleep(0)
            ),
        })()
        monkeypatch.setattr(
            "depthfusion.mcp.tools._state._get_fabric_store", lambda: fake_store,
        )

        await hs._publish_session_checkpoint("sess-no-project")

        assert calls == []

    @pytest.mark.asyncio
    async def test_never_raises_when_fabric_store_publish_fails(self, monkeypatch):
        from depthfusion.mcp import http_server as hs

        monkeypatch.setattr(
            "depthfusion.hooks.git_post_commit.detect_project", lambda: "depthfusion",
        )

        class BrokenStore:
            async def publish_checkpoint(self, **kwargs):
                raise RuntimeError("store unavailable")

        monkeypatch.setattr(
            "depthfusion.mcp.tools._state._get_fabric_store", lambda: BrokenStore(),
        )

        # Must not raise.
        await hs._publish_session_checkpoint("sess-broken-store")
