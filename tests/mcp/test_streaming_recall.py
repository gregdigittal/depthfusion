"""T-857 / T-858: SSE streaming recall wiring and schema (S-252).

TestRecallSchemaAndCallback: schema-presence assertions (T-858) verifying that
the `stream` boolean parameter is declared in TOOL_SCHEMAS['depthfusion_recall_relevant']
with the correct type and default.

TestRecallStreamCallbackWiring: real-collaborator wiring tests (T-857) verifying that
stream_push_cb is correctly threaded from _tool_recall through to _tool_recall_impl.
Uses a real on-disk discovery file (no mocking of the new wiring itself) so the
callback pathway exercises the live BM25 threshold gate and apply_reranker code.

Regression proof (T-857):
  Remove `stream_push_cb=stream_push_cb` from recall.py's call to _tool_recall_impl
  and `test_stream_push_cb_is_called_through_tool_recall` fails (collected stays empty).
  Verified manually: comment out the kwarg → FAIL; restore → PASS.

Regression proof (T-858):
  Remove the 'stream' entry from TOOL_SCHEMAS['depthfusion_recall_relevant']['properties']
  and all three assertions below fail immediately, making the silent absence of the
  parameter visible in CI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from depthfusion.mcp.tools._registry import TOOL_SCHEMAS
from depthfusion.mcp.tools.recall import _tool_recall


class TestRecallSchemaAndCallback:
    """Schema-presence assertions for the `stream` parameter (T-858)."""

    def test_stream_parameter_present_in_schema(self) -> None:
        props = TOOL_SCHEMAS["depthfusion_recall_relevant"]["properties"]
        assert "stream" in props, (
            "'stream' is missing from TOOL_SCHEMAS['depthfusion_recall_relevant']['properties'] — "
            "T-858 schema addition was not applied or was reverted"
        )

    def test_stream_parameter_type_is_boolean(self) -> None:
        props = TOOL_SCHEMAS["depthfusion_recall_relevant"]["properties"]
        assert props["stream"]["type"] == "boolean", (
            f"Expected stream type 'boolean', got {props['stream'].get('type')!r}"
        )

    def test_stream_parameter_default_is_false(self) -> None:
        props = TOOL_SCHEMAS["depthfusion_recall_relevant"]["properties"]
        assert props["stream"]["default"] is False, (
            f"Expected stream default False, got {props['stream'].get('default')!r}"
        )

    def test_stream_not_in_required_list(self) -> None:
        """stream is optional — must not appear in the required array."""
        required = TOOL_SCHEMAS["depthfusion_recall_relevant"].get("required", [])
        assert "stream" not in required, (
            "'stream' must remain optional (default=false) — it must not be listed in 'required'"
        )


class TestRecallStreamCallbackWiring:
    """Real-collaborator wiring tests for the stream_push_cb pathway (T-857).

    Arrangement: a real on-disk shared/discoveries file containing the word
    'memory' multiple times is placed under tmp_path so the BM25 engine scores
    it above the 0.0 threshold for the query 'session memory'.  Path.home is
    monkeypatched to return tmp_path so _tool_recall_impl resolves discovery
    paths into the test corpus.  No mock stands in for the new wiring itself —
    the callback must propagate through the real call chain.
    """

    @pytest.fixture()
    def corpus(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Create a minimal discovery corpus under tmp_path and redirect home.

        _tool_recall_impl resolves discoveries at Path.home() / '.claude' /
        'shared' / 'discoveries', so the fixture must mirror that layout.
        """
        discoveries = tmp_path / ".claude" / "shared" / "discoveries"
        discoveries.mkdir(parents=True)
        doc = discoveries / "session_test.md"
        doc.write_text(
            "# Session Memory\n\n"
            "This document discusses session memory management.\n"
            "Session memory persists across context resets.\n"
            "Understanding session memory is essential for agents.\n"
            "Memory retrieval depends on BM25 scoring of session terms.\n"
            "Good memory hygiene keeps session context clean.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        return tmp_path

    def test_stream_push_cb_is_called_through_tool_recall(
        self, corpus: Path
    ) -> None:
        """FAIL-WITH-BUG: removing stream_push_cb=stream_push_cb from recall.py
        keeps collected empty; this assertion on len(collected) then fails.
        """
        collected: list[str] = []
        _tool_recall(
            {"query": "session memory", "stream": True},
            stream_push_cb=collected.append,
        )
        assert len(collected) >= 1, (
            "stream_push_cb was never called — check that recall.py threads "
            "stream_push_cb=stream_push_cb into _tool_recall_impl"
        )
        for item in collected:
            parsed = json.loads(item)  # must be valid JSON
            assert "event_type" in parsed
        event_types = {json.loads(item)["event_type"] for item in collected}
        assert event_types & {"interim_result", "ranking_complete"}, (
            f"Expected at least one interim_result or ranking_complete event, got: {event_types}"
        )

    def test_stream_push_cb_not_called_when_stream_is_false(
        self, corpus: Path
    ) -> None:
        """Guards AC-4 regression: full JSON return always produced; no events
        when stream=False regardless of callback presence.
        """
        collected: list[str] = []
        result = _tool_recall(
            {"query": "session memory", "stream": False},
            stream_push_cb=collected.append,
        )
        assert len(collected) == 0, (
            "stream_push_cb must NOT be called when stream=False"
        )
        # Full JSON return value must still be produced (AC-4)
        parsed = json.loads(result)
        assert "blocks" in parsed

    def test_ranking_complete_is_last_event(self, corpus: Path) -> None:
        """ranking_complete must be the final event emitted."""
        collected: list[str] = []
        _tool_recall(
            {"query": "session memory", "stream": True},
            stream_push_cb=collected.append,
        )
        assert len(collected) >= 1, "Expected at least one streaming event"
        last = json.loads(collected[-1])
        assert last["event_type"] == "ranking_complete", (
            f"Last event must be ranking_complete, got: {last['event_type']!r}"
        )
