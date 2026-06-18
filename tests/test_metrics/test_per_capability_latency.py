# tests/test_metrics/test_per_capability_latency.py
"""S-80 / T-270 — per-capability latency in latency_ms_per_capability.

AC-5: ≥ 4 tests covering:
  1. happy-path multi-capability recall — all six capability keys present
  2. single-capability fallback — embedding-only via vector search
  3. error-path latency capture — reranker throws; latency still recorded
  4. dict-shape contract — values are non-negative floats, keys are strings

All tests patch Path.home() and _detect_current_backends so they run
hermetically in CI without any real backends or file system state.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

SIX_CAPS = frozenset(
    {"reranker", "extractor", "linker", "summariser", "embedding", "decision_extractor"}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stub_backend(name: str) -> MagicMock:
    """Return a minimal backend mock with a .name attribute."""
    m = MagicMock()
    m.name = name
    return m


def _metrics_dir(tmp_path: Path) -> Path:
    return tmp_path / ".claude" / "depthfusion-metrics"


def _read_recall_events(tmp_path: Path) -> list[dict]:
    files = list(_metrics_dir(tmp_path).glob("*-recall.jsonl"))
    if not files:
        return []
    return [
        json.loads(ln)
        for ln in files[0].read_text().splitlines()
        if ln.strip()
    ]


def _minimal_corpus(tmp_path: Path) -> None:
    """Write one discovery file so _tool_recall has a non-empty corpus."""
    disc = tmp_path / ".claude" / "shared" / "discoveries"
    disc.mkdir(parents=True, exist_ok=True)
    (disc / "sample.md").write_text(
        "# Test\n\nauthentication token refresh flow.\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Test 1: happy-path multi-capability recall
# ---------------------------------------------------------------------------

class TestHappyPathAllSixCapabilities:
    """All six capability keys must appear in latency_ms_per_capability
    for a successful recall event (AC-1).
    """

    def test_all_six_capabilities_present_after_recall(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _minimal_corpus(tmp_path)

        from depthfusion.mcp import server as srv_mod

        # Patch _detect_current_backends to return all six caps with probe
        # times written into perf_ms (simulates the S-80 implementation).
        # S-83 added a `fallback_chain` kwarg; accept it for forward-compat.
        def fake_detect(perf_ms=None, fallback_chain=None):
            backends = {c: "null" for c in SIX_CAPS}
            if perf_ms is not None:
                for cap in SIX_CAPS:
                    perf_ms[cap] = 0.1  # tiny probe time
            if fallback_chain is not None:
                for cap in SIX_CAPS:
                    fallback_chain[cap] = ["null"]
            return backends

        monkeypatch.setattr(srv_mod, "_detect_current_backends", fake_detect)

        srv_mod._tool_recall({"query": "authentication token", "top_k": 3})

        events = _read_recall_events(tmp_path)
        assert events, "Expected at least one recall event on disk"
        latencies = events[0]["latency_ms_per_capability"]

        missing = SIX_CAPS - set(latencies.keys())
        assert not missing, (
            f"latency_ms_per_capability missing keys: {missing}"
        )


# ---------------------------------------------------------------------------
# Test 2: single-capability fallback (embedding only via vector search)
# ---------------------------------------------------------------------------

class TestSingleCapabilityFallback:
    """When only vector search runs (no reranker), `embedding` must still
    appear in latency_ms_per_capability (AC-1, AC-2).
    """

    def test_embedding_key_present_when_vector_search_enabled(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _minimal_corpus(tmp_path)

        # Enable vector search
        monkeypatch.setenv("DEPTHFUSION_VECTOR_SEARCH_ENABLED", "true")

        from depthfusion.mcp import server as srv_mod
        from depthfusion.retrieval.hybrid import RecallPipeline

        # Stub _detect_current_backends (S-83 added fallback_chain kwarg)
        def fake_detect(perf_ms=None, fallback_chain=None):
            if perf_ms is not None:
                for cap in SIX_CAPS:
                    perf_ms[cap] = 0.05
            if fallback_chain is not None:
                for cap in SIX_CAPS:
                    fallback_chain[cap] = ["null"]
            return {c: "null" for c in SIX_CAPS}

        monkeypatch.setattr(srv_mod, "_detect_current_backends", fake_detect)

        # Stub apply_vector_search so it returns [] quickly (no real backend)
        pipeline_mock = MagicMock(spec=RecallPipeline)
        pipeline_mock.mode = MagicMock()
        pipeline_mock.mode.value = "local"
        pipeline_mock.apply_vector_search.return_value = []
        pipeline_mock.rrf_fuse.return_value = []
        pipeline_mock.apply_reranker.return_value = []

        monkeypatch.setattr(
            "depthfusion.retrieval.hybrid.RecallPipeline.from_env",
            classmethod(lambda cls: pipeline_mock),
        )

        srv_mod._tool_recall({"query": "authentication", "top_k": 2})

        events = _read_recall_events(tmp_path)
        assert events, "Expected recall event on disk"
        latencies = events[0]["latency_ms_per_capability"]
        # embedding must appear (either from vector_search phase or probe)
        assert "embedding" in latencies, (
            f"'embedding' not in latency_ms_per_capability: {latencies}"
        )
        # value must be non-negative
        assert latencies["embedding"] >= 0.0


# ---------------------------------------------------------------------------
# Test 3: error-path latency capture
# ---------------------------------------------------------------------------

class TestErrorPathLatencyCapture:
    """When the reranker backend raises, the latency up to the error
    must still appear in latency_ms_per_capability (AC-3).
    """

    def test_reranker_latency_recorded_when_reranker_raises(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _minimal_corpus(tmp_path)

        from depthfusion.mcp import server as srv_mod
        from depthfusion.retrieval.hybrid import RecallPipeline

        # Stub _detect_current_backends (S-83 added fallback_chain kwarg)
        def fake_detect(perf_ms=None, fallback_chain=None):
            if perf_ms is not None:
                for cap in SIX_CAPS:
                    perf_ms[cap] = 0.05
            if fallback_chain is not None:
                for cap in SIX_CAPS:
                    fallback_chain[cap] = ["null"]
            return {c: "null" for c in SIX_CAPS}

        monkeypatch.setattr(srv_mod, "_detect_current_backends", fake_detect)

        # Build a pipeline where apply_reranker raises
        pipeline_mock = MagicMock(spec=RecallPipeline)
        pipeline_mock.mode = MagicMock()
        pipeline_mock.mode.value = "vps-cpu"  # non-local → reranker is timed
        pipeline_mock.apply_vector_search.return_value = []
        pipeline_mock.apply_fusion_gates.return_value = []
        pipeline_mock.apply_reranker.side_effect = RuntimeError("backend timeout")

        monkeypatch.setattr(
            "depthfusion.retrieval.hybrid.RecallPipeline.from_env",
            classmethod(lambda cls: pipeline_mock),
        )

        # The call must NOT raise — errors are caught inside _tool_recall
        result_json = srv_mod._tool_recall({"query": "auth", "top_k": 3})
        response = json.loads(result_json)
        # The outer wrapper surfaces the error
        assert "error" in response

        # Metrics event must still be emitted
        events = _read_recall_events(tmp_path)
        assert events, "Expected a recall event even on error path"
        event = events[0]
        # event_subtype should be "error"
        assert event["event_subtype"] == "error"
        # latency_ms_per_capability should contain what was recorded
        # before/during the error — reranker latency was measured in finally
        latencies = event["latency_ms_per_capability"]
        assert "reranker" in latencies, (
            f"reranker latency missing from error event: {latencies}"
        )
        assert latencies["reranker"] >= 0.0


# ---------------------------------------------------------------------------
# Test 4: dict-shape contract
# ---------------------------------------------------------------------------

class TestDictShapeContract:
    """latency_ms_per_capability must be a dict[str, float] with
    non-negative values (AC-2).
    """

    def test_latency_values_are_non_negative_floats(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _minimal_corpus(tmp_path)

        from depthfusion.mcp import server as srv_mod

        def fake_detect(perf_ms=None, fallback_chain=None):
            if perf_ms is not None:
                for cap in SIX_CAPS:
                    perf_ms[cap] = 0.25
            if fallback_chain is not None:
                for cap in SIX_CAPS:
                    fallback_chain[cap] = ["null"]
            return {c: "null" for c in SIX_CAPS}

        monkeypatch.setattr(srv_mod, "_detect_current_backends", fake_detect)

        srv_mod._tool_recall({"query": "schema migration", "top_k": 2})

        events = _read_recall_events(tmp_path)
        assert events
        latencies = events[0]["latency_ms_per_capability"]

        # Must be a dict
        assert isinstance(latencies, dict), (
            f"latency_ms_per_capability must be dict, got {type(latencies)}"
        )
        # All keys must be strings
        for key in latencies:
            assert isinstance(key, str), f"Non-string key: {key!r}"
        # All values must be non-negative numbers
        for key, val in latencies.items():
            assert isinstance(val, (int, float)), (
                f"latency[{key!r}] is not a number: {val!r}"
            )
            assert val >= 0.0, (
                f"latency[{key!r}] is negative: {val}"
            )

    def test_probe_latency_does_not_overwrite_pipeline_measurement(
        self, tmp_path, monkeypatch
    ):
        """Pipeline measurements (reranker) take precedence over probe times.

        The probe seeds all six keys; later the pipeline writes a more
        accurate reranker measurement.  The final value in the event must
        be the pipeline measurement, not the probe stub value (AC-2).
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _minimal_corpus(tmp_path)

        import depthfusion.mcp.tools.recall as _recall_module
        from depthfusion.mcp import server as srv_mod
        from depthfusion.retrieval.hybrid import RecallPipeline

        PROBE_STUB = 999.0  # deliberately large so the merge is visible

        def fake_detect(perf_ms=None, fallback_chain=None):
            if perf_ms is not None:
                for cap in SIX_CAPS:
                    # Seed everything with the probe stub
                    perf_ms[cap] = PROBE_STUB
            if fallback_chain is not None:
                for cap in SIX_CAPS:
                    fallback_chain[cap] = ["null"]
            return {c: "null" for c in SIX_CAPS}

        # After T-535a, _tool_recall lives in recall.py — patch there.
        monkeypatch.setattr(_recall_module, "_detect_current_backends", fake_detect)

        # Pipeline mock: non-local so reranker is timed, but apply_reranker
        # is instant (the finally block records a real elapsed ≈ 0ms).
        pipeline_mock = MagicMock(spec=RecallPipeline)
        pipeline_mock.mode = MagicMock()
        pipeline_mock.mode.value = "vps-cpu"  # non-local → reranker timed
        pipeline_mock.apply_vector_search.return_value = []
        pipeline_mock.apply_reranker.return_value = []

        monkeypatch.setattr(
            "depthfusion.retrieval.hybrid.RecallPipeline.from_env",
            classmethod(lambda cls: pipeline_mock),
        )

        srv_mod._tool_recall({"query": "migration plan", "top_k": 2})

        events = _read_recall_events(tmp_path)
        assert events
        latencies = events[0]["latency_ms_per_capability"]

        # reranker latency recorded by the pipeline must NOT be the probe stub
        # (the pipeline ran and replaced the probe value for "reranker")
        assert "reranker" in latencies
        assert latencies["reranker"] != PROBE_STUB, (
            "Pipeline reranker measurement should overwrite the probe stub"
        )
        # Other caps (not invoked by pipeline) should retain the probe value
        for cap in ("extractor", "linker", "summariser", "decision_extractor"):
            assert cap in latencies
            assert latencies[cap] == PROBE_STUB, (
                f"{cap} should retain probe stub when not invoked by pipeline"
            )
