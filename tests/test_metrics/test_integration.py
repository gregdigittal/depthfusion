# tests/test_metrics/test_integration.py
"""End-to-end wiring tests for the v0.5.2 metrics streams — S-60 / T-191.

AC-4: ≥ 5 integration tests, one per call site:
  * `_tool_recall` emits `recall_query` events per call
  * `decision_extractor.write_decisions` emits `capture` events
  * `negative_extractor.write_negatives` emits `capture` events
  * `dedup.dedup_against_corpus` emits `capture` events per supersede
  * `hooks/git_post_commit.write_commit_discovery` emits `capture` events
  * `_tool_confirm_discovery` emits under mechanism="confirm_discovery"

Each test redirects `Path.home` to `tmp_path` so the metrics stream lands
in an isolated directory and the assertions check the file on disk.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock


def _read_jsonl(path: Path) -> list[dict]:
    """Read + parse all JSON lines from `path`."""
    if not path.exists():
        return []
    return [
        json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()
    ]


def _metrics_dir(tmp_path: Path) -> Path:
    return tmp_path / ".claude" / "depthfusion-metrics"


# ---------------------------------------------------------------------------
# T-186: _tool_recall → recall_query stream
# ---------------------------------------------------------------------------

class TestRecallQueryEmission:
    def test_recall_emits_event_per_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        # Populate a tiny corpus so recall has something to return
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)
        (disc / "sample.md").write_text(
            "# Sample\n\nauthentication token refresh flow details.\n",
            encoding="utf-8",
        )

        from depthfusion.mcp.server import _tool_recall
        result_json = _tool_recall({"query": "authentication token", "top_k": 5})
        # Verify the recall itself worked
        assert json.loads(result_json).get("blocks") is not None

        # Verify the recall_query event landed on disk
        files = list(_metrics_dir(tmp_path).glob("*-recall.jsonl"))
        assert len(files) == 1
        events = _read_jsonl(files[0])
        assert len(events) == 1
        event = events[0]
        assert event["event"] == "recall_query"
        assert event["event_subtype"] == "ok"
        assert event["query_hash"]  # non-empty sha256[:12]
        assert "authentication" not in json.dumps(event)  # raw query not logged
        assert event["result_count"] >= 0
        assert event["total_latency_ms"] is not None
        assert event["total_latency_ms"] >= 0.0

    def test_recall_error_emits_error_subtype(self, tmp_path, monkeypatch):
        """When the recall path raises internally, the event_subtype is 'error'."""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        # Force the impl to raise
        from depthfusion.mcp import server as srv_mod

        def broken_impl(args):
            raise RuntimeError("simulated recall failure")

        monkeypatch.setattr(srv_mod, "_tool_recall_impl", broken_impl)
        result_json = srv_mod._tool_recall({"query": "anything"})
        # The wrapper returns the error JSON
        response = json.loads(result_json)
        assert "error" in response

        files = list(_metrics_dir(tmp_path).glob("*-recall.jsonl"))
        assert len(files) == 1
        events = _read_jsonl(files[0])
        assert events[0]["event_subtype"] == "error"


# ---------------------------------------------------------------------------
# T-187: extractor streams
# ---------------------------------------------------------------------------

class TestExtractorEmission:
    def test_decision_extractor_emits_capture_event(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        (tmp_path / ".claude" / "depthfusion-metrics").mkdir(parents=True)
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)

        from depthfusion.capture.decision_extractor import (
            DecisionEntry,
            write_decisions,
        )
        entry = DecisionEntry(
            text="Use redis for caching", confidence=0.9, category="decision",
            source_session="t",
        )
        out = write_decisions([entry], project="testproj", session_id="t",
                              output_dir=disc)
        assert out is not None

        events = _read_jsonl(next(_metrics_dir(tmp_path).glob("*-capture.jsonl")))
        assert len(events) == 1
        assert events[0]["capture_mechanism"] == "decision_extractor"
        assert events[0]["write_success"] is True
        assert events[0]["entries_written"] == 1
        assert events[0]["project"] == "testproj"

    def test_decision_extractor_emits_skip_event_when_file_exists(
        self, tmp_path, monkeypatch,
    ):
        """Second write (same date, same project) skips and emits a
        `write_success=False` event — not a silent no-op.
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)

        from depthfusion.capture.decision_extractor import (
            DecisionEntry,
            write_decisions,
        )
        entry = DecisionEntry(text="X" * 20, confidence=0.9, category="decision",
                              source_session="t")
        # First write succeeds
        write_decisions([entry], project="p", session_id="s", output_dir=disc)
        # Second write skips
        out = write_decisions([entry], project="p", session_id="s", output_dir=disc)
        assert out is None

        events = _read_jsonl(next(_metrics_dir(tmp_path).glob("*-capture.jsonl")))
        assert len(events) == 2
        assert events[0]["write_success"] is True
        assert events[1]["write_success"] is False

    def test_negative_extractor_emits_capture_event(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)

        from depthfusion.capture.negative_extractor import (
            NegativeEntry,
            write_negatives,
        )
        entry = NegativeEntry(
            what="pytest-asyncio decorator", why="race conditions", confidence=0.8,
            source_session="t",
        )
        out = write_negatives([entry], project="testproj", session_id="t",
                              output_dir=disc)
        assert out is not None

        events = _read_jsonl(next(_metrics_dir(tmp_path).glob("*-capture.jsonl")))
        assert len(events) == 1
        assert events[0]["capture_mechanism"] == "negative_extractor"
        assert events[0]["entries_written"] == 1


# ---------------------------------------------------------------------------
# T-188: dedup stream
# ---------------------------------------------------------------------------

class TestDedupEmission:
    def test_supersede_emits_capture_event(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)

        # Two files with distinguishable content — mock backend returns
        # near-identical vectors so dedup supersedes the older.
        old = disc / "old.md"
        old.write_text(
            "---\nproject: p1\ntype: decisions\n---\n\nUse redis OLDMARKER\n",
            encoding="utf-8",
        )
        new = disc / "new.md"
        new.write_text(
            "---\nproject: p1\ntype: decisions\n---\n\nUse redis NEWMARKER\n",
            encoding="utf-8",
        )

        backend = MagicMock()
        def embed(texts):
            out = []
            for t in texts:
                if "NEWMARKER" in t:
                    out.append([1.0, 0.0, 0.0])
                else:
                    out.append([0.99, 0.01, 0.0])
            return out
        backend.embed.side_effect = embed

        from depthfusion.capture.dedup import dedup_against_corpus
        superseded = dedup_against_corpus(
            new, backend=backend, output_dir=disc, threshold=0.90,
        )
        assert len(superseded) == 1

        events = _read_jsonl(next(_metrics_dir(tmp_path).glob("*-capture.jsonl")))
        assert len(events) == 1
        assert events[0]["capture_mechanism"] == "dedup"
        assert events[0]["write_success"] is True
        assert events[0]["project"] == "p1"


# ---------------------------------------------------------------------------
# T-189: git post-commit stream
# ---------------------------------------------------------------------------

class TestGitPostCommitEmission:
    def test_commit_discovery_emits_capture_event(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        (tmp_path / ".claude" / "depthfusion-metrics").mkdir(parents=True)
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)

        from depthfusion.hooks.git_post_commit import write_commit_discovery
        commit = {
            "sha": "abc1234567890",
            "sha7": "abc1234",
            "message": "feat: test",
            "author": "Dev",
            "files_changed": "1 file changed",
            "diff_summary": "src/x.py | 1 +",
        }
        out = write_commit_discovery(commit, project="myapp", output_dir=disc)
        assert out is not None

        events = _read_jsonl(next(_metrics_dir(tmp_path).glob("*-capture.jsonl")))
        assert len(events) == 1
        assert events[0]["capture_mechanism"] == "git_post_commit"
        assert events[0]["session_id"] == "abc1234"  # SHA7
        assert events[0]["project"] == "myapp"


# ---------------------------------------------------------------------------
# T-190: _tool_confirm_discovery stream
# ---------------------------------------------------------------------------

class TestConfirmDiscoveryEmission:
    def test_confirm_discovery_emits_under_confirm_mechanism(
        self, tmp_path, monkeypatch,
    ):
        """The metrics bucket for this path is `confirm_discovery`, not
        the underlying `decision_extractor` (S-60 T-190).
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)

        from depthfusion.mcp.server import _tool_confirm_discovery
        result_json = _tool_confirm_discovery({
            "text": "Use postgres for transactional writes",
            "project": "myapp",
        })
        result = json.loads(result_json)
        assert result["ok"] is True

        events = _read_jsonl(next(_metrics_dir(tmp_path).glob("*-capture.jsonl")))
        # Exactly one event — the override kwarg prevents double-counting
        # when decision_extractor's internal emit runs under the
        # confirm_discovery label.
        assert len(events) == 1
        assert events[0]["capture_mechanism"] == "confirm_discovery"
        assert events[0]["capture_mechanism_known"] is True

    def test_confirm_discovery_on_existing_file_still_emits(
        self, tmp_path, monkeypatch,
    ):
        """Second call for today produces a `write_success=False` event
        under `confirm_discovery` — idempotent-skip is still observable.
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)

        from depthfusion.mcp.server import _tool_confirm_discovery
        # First call writes the file
        _tool_confirm_discovery({"text": "X" * 20, "project": "proj1"})
        # Second call idempotent-skips
        _tool_confirm_discovery({"text": "Y" * 20, "project": "proj1"})

        events = _read_jsonl(next(_metrics_dir(tmp_path).glob("*-capture.jsonl")))
        assert len(events) == 2
        assert all(e["capture_mechanism"] == "confirm_discovery" for e in events)
        assert events[0]["write_success"] is True
        assert events[1]["write_success"] is False


# ---------------------------------------------------------------------------
# Observability never breaks the hot path (S-60 AC-3)
# ---------------------------------------------------------------------------

class TestReviewGateRegressions:
    def test_backend_probe_skipped_on_error_path(self, tmp_path, monkeypatch):
        """IMP-1 fix: when recall raises, `_detect_current_backends` is NOT
        called — the error path is already degraded and the 6× probe
        would add overhead without adding observability value. The
        backend_used dict in the emitted event is empty on error.
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        (tmp_path / ".claude" / "shared" / "discoveries").mkdir(parents=True)

        # Force the impl to raise so event_subtype becomes "error"
        from depthfusion.mcp import server as srv_mod
        monkeypatch.setattr(
            srv_mod, "_tool_recall_impl",
            lambda args: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        # Track whether _detect_current_backends was called
        calls = {"count": 0}
        original = srv_mod._detect_current_backends

        def spy():
            calls["count"] += 1
            return original()

        monkeypatch.setattr(srv_mod, "_detect_current_backends", spy)

        srv_mod._tool_recall({"query": "anything"})
        # The probe must NOT have been called on the error path
        assert calls["count"] == 0

        # And the emitted event has empty backend_used
        files = list(_metrics_dir(tmp_path).glob("*-recall.jsonl"))
        event = _read_jsonl(files[0])[0]
        assert event["event_subtype"] == "error"
        assert event["backend_used"] == {}

    def test_dedup_emits_event_when_no_duplicates_found(
        self, tmp_path, monkeypatch,
    ):
        """IMP-2 fix: when dedup runs but finds no duplicates, still emit
        an event so the metrics stream distinguishes "ran, found nothing"
        (common case) from "never ran".
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)

        # Two files with totally different vocabulary → no duplicates
        old = disc / "old.md"
        old.write_text(
            "---\nproject: p1\n---\n\nUse redis for caching\n", encoding="utf-8",
        )
        new = disc / "new.md"
        new.write_text(
            "---\nproject: p1\n---\n\nUse postgres for transactional writes\n",
            encoding="utf-8",
        )

        backend = MagicMock()
        # Orthogonal embeddings → cos-sim = 0, below any reasonable threshold
        backend.embed.return_value = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]

        from depthfusion.capture.dedup import dedup_against_corpus
        superseded = dedup_against_corpus(
            new, backend=backend, output_dir=disc, threshold=0.92,
        )
        assert superseded == []

        # Event MUST still be emitted — the stream should show dedup ran
        events = _read_jsonl(next(_metrics_dir(tmp_path).glob("*-capture.jsonl")))
        assert len(events) == 1
        assert events[0]["capture_mechanism"] == "dedup"
        assert events[0]["write_success"] is True   # dedup completed
        assert events[0]["entries_written"] == 0    # but no supersessions
        assert events[0]["project"] == "p1"


class TestObservabilityIsBestEffort:
    def test_broken_metrics_collector_doesnt_break_recall(
        self, tmp_path, monkeypatch,
    ):
        """A broken MetricsCollector must not prevent _tool_recall returning
        a valid response. S-60 AC-3.
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        (tmp_path / ".claude" / "shared" / "discoveries").mkdir(parents=True)

        # Break the collector import site
        import depthfusion.metrics.collector as collector_mod

        class BrokenCollector:
            def __init__(self, *a, **kw):
                raise RuntimeError("metrics disk full")

        monkeypatch.setattr(collector_mod, "MetricsCollector", BrokenCollector)

        from depthfusion.mcp.server import _tool_recall
        # Must not raise
        result_json = _tool_recall({"query": "anything"})
        assert "blocks" in json.loads(result_json)

    def test_broken_metrics_collector_doesnt_break_capture(
        self, tmp_path, monkeypatch,
    ):
        """A broken MetricsCollector must not prevent write_decisions
        returning the output path. Critical for the git post-commit
        hook — a metrics failure can't block a commit.

        This tests the realistic failure mode: the underlying collector
        implementation raises. The shared `emit_capture_event` helper
        catches any exception from the collector and logs at DEBUG.
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        disc = tmp_path / ".claude" / "shared" / "discoveries"
        disc.mkdir(parents=True)

        # Break MetricsCollector at its constructor — the helper's
        # try/except around the `from ... import MetricsCollector` +
        # instantiation catches this.
        import depthfusion.metrics.collector as collector_mod

        class BrokenCollector:
            def __init__(self, *a, **kw):
                raise RuntimeError("metrics disk full")

        monkeypatch.setattr(collector_mod, "MetricsCollector", BrokenCollector)

        from depthfusion.capture.decision_extractor import (
            DecisionEntry,
            write_decisions,
        )
        entry = DecisionEntry(text="X" * 20, confidence=0.9, category="decision",
                              source_session="t")
        # Must not raise — the write completes even though metrics fail
        out = write_decisions([entry], project="p", session_id="s", output_dir=disc)
        assert out is not None
        assert out.exists()
