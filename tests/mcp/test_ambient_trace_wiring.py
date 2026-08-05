"""T-849: ambient-trace side-effect wiring into `_process_request` (S-250 AC-2)
and the per-session activity tracker feeding real checkpoint data (S-250 AC-1).

TestRealCollaborators-shaped (tests/core/test_hygiene_scheduler.py:333
convention, mirrored by tests/core/test_event_store.py's own
TestRealCollaborators): a REAL JSONGraphStore + REAL EventStore + REAL
InMemoryStreamBackend back every assertion in this file — no MagicMock
stands in for the event store or the stream bus anywhere below. The only
things replaced are: (1) the lazy-singleton lookup function
`_get_fabric_store` (swapped to return the real, tmp_path-backed store
instead of the process-wide singleton — a test-isolation seam, not a mock
of the store itself), and (2) `DEPTHFUSION_PROJECT`, set to a fixed value
so project auto-detection is deterministic in CI.

Covers:
  * A `tools/call` request through `_process_request` produces a real
    `event_type="ambient_trace"` Entity in the graph (T-849's publish-side
    wiring; `EventStore.publish_ambient_trace` itself predates this task
    and is exercised directly in tests/core/test_event_store.py).
  * The pre-existing, already-tested `filter_blocks_by_ambient_trace`
    predicate excludes a `type: ambient_trace`-tagged block from standard
    recall but returns it when `include_ambient_trace=True` — recorded here
    to document the shared `ambient_trace` tag convention between the
    graph-fabric publish side (this task) and the markdown-block recall
    side (retrieval/hybrid.py, landed and tested separately); the two
    subsystems are decoupled by design (event fabric vs. on-disk recall
    corpus), so this test exercises the real filter function rather than
    fabricating a fake end-to-end file-backed recall corpus.
  * A `DELETE /mcp`-style checkpoint (`_publish_session_checkpoint`) for a
    session that made a tracked tool call carries real, non-placeholder
    `plan_state`/`files_modified` — the literal S-250 AC-1 gap this task
    closes.

Regression-proven: with the `_record_ambient_activity(...)` call removed
from `mcp/server.py::_process_request`'s `tools/call` branch,
`test_tool_call_publishes_a_real_ambient_trace_event` and
`test_delete_mcp_checkpoint_carries_tracked_activity` both fail (no
ambient_trace entity is ever written; the checkpoint reverts to the
`""`/`[]` placeholder). Verified manually during implementation by
commenting out that call, re-running this file (FAIL), then restoring it
(PASS) — see T-849 implementation notes.
"""
from __future__ import annotations

import time

import pytest

from depthfusion.core.config import DepthFusionConfig
from depthfusion.core.event_store import EventStore, InMemoryStreamBackend
from depthfusion.graph.store import JSONGraphStore
from depthfusion.identity.models import Principal
from depthfusion.mcp import session_activity
from depthfusion.mcp.server import _process_request
from depthfusion.retrieval.hybrid import filter_blocks_by_ambient_trace


def _owner_principal(principal_id: str = "ambient-test-user") -> Principal:
    return Principal(
        principal_id=principal_id,
        upn=f"{principal_id}@example.com",
        display_name=principal_id,
        groups=["owner"],
    )


def _wait_for(predicate, timeout: float = 2.0, interval: float = 0.05) -> bool:
    """Poll *predicate* until it returns truthy or *timeout* elapses.

    The ambient-trace publish happens on a dedicated background loop thread
    (fire-and-forget, by design — see mcp/server.py's `_get_ambient_trace_loop`
    docstring), so the write to the real, tmp_path-backed JSONGraphStore
    lands asynchronously relative to `_process_request`'s return. This is a
    real background thread (not mocked/stepped manually), so the test polls
    for its effect rather than asserting it synchronously.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def real_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("DEPTHFUSION_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("DEPTHFUSION_PROJECT", "depthfusion")
    graph = JSONGraphStore(path=tmp_path / "graph.json")
    stream = InMemoryStreamBackend()
    store = EventStore(graph=graph, stream=stream)

    from depthfusion.mcp.tools import _state

    monkeypatch.setattr(_state, "_get_fabric_store", lambda: store)
    return store, graph


class TestRealCollaborators:
    """Real JSONGraphStore + real EventStore — no MagicMock for store/bus."""

    def test_tool_call_publishes_a_real_ambient_trace_event(self, real_store):
        store, graph = real_store
        session_id = "sess-ambient-real-1"
        session_activity.clear_session(session_id)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "depthfusion_status", "arguments": {}},
        }

        response = _process_request(
            request, DepthFusionConfig(), principal=_owner_principal(), session_id=session_id,
        )

        # The response path is not blocked by the ambient-trace publish.
        assert response["result"]["isError"] is False

        def _ambient_event_landed() -> bool:
            return any(
                e.type == "event"
                and e.metadata.get("event_type") == "ambient_trace"
                and e.metadata.get("session_id") == session_id
                for e in graph.all_entities()
            )

        assert _wait_for(_ambient_event_landed), (
            "no event_type='ambient_trace' entity was published for the "
            "tools/call request — ambient-trace wiring is missing or broken"
        )

        session_activity.clear_session(session_id)

    def test_standard_recall_excludes_it_include_ambient_trace_returns_it(self):
        """Shared-tag convention check: real (not mocked) filter function."""

        def _block(chunk_id: str, type_line: str) -> dict:
            content = f"---\nproject: depthfusion\ntype: {type_line}\n---\n\nbody"
            return {"chunk_id": chunk_id, "content": content, "source": "discovery"}

        blocks = [_block("ambient", "ambient_trace"), _block("decision", "decisions")]

        standard = filter_blocks_by_ambient_trace(blocks)
        assert {b["chunk_id"] for b in standard} == {"decision"}

        with_ambient = filter_blocks_by_ambient_trace(blocks, include_ambient_trace=True)
        assert {b["chunk_id"] for b in with_ambient} == {"ambient", "decision"}

    @pytest.mark.asyncio
    async def test_delete_mcp_checkpoint_carries_tracked_activity(self, real_store):
        """S-250 AC-1: a DELETE /mcp checkpoint after tracked activity is real."""
        store, graph = real_store
        from depthfusion.mcp import http_server as hs

        session_id = "sess-checkpoint-real-1"
        session_activity.clear_session(session_id)

        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "depthfusion_status",
                "arguments": {"files_modified": ["src/depthfusion/mcp/server.py"]},
            },
        }
        _process_request(
            request, DepthFusionConfig(), principal=_owner_principal(), session_id=session_id,
        )

        await hs._publish_session_checkpoint(session_id)

        latest = store.get_latest_checkpoint("depthfusion", session_id=session_id)
        assert latest is not None
        assert latest.plan_state != "", "plan_state is still the pre-T-849 placeholder"
        assert latest.files_modified == ["src/depthfusion/mcp/server.py"], (
            f"files_modified is still the pre-T-849 placeholder: {latest.files_modified!r}"
        )

        # DELETE /mcp checkpointing clears the tracker for this session id.
        assert session_activity.get_activity(session_id) is None

    @pytest.mark.asyncio
    async def test_checkpoint_stays_placeholder_when_no_activity_occurred(self, real_store):
        """No fabrication: a session with zero tool calls still gets placeholders."""
        store, graph = real_store
        from depthfusion.mcp import http_server as hs

        session_id = "sess-no-activity-1"
        session_activity.clear_session(session_id)

        await hs._publish_session_checkpoint(session_id)

        latest = store.get_latest_checkpoint("depthfusion", session_id=session_id)
        assert latest is not None
        assert latest.plan_state == ""
        assert latest.files_modified == []
        assert latest.context_pct_at_checkpoint is None
