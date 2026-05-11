# tests/test_metrics/test_fallback_canonical.py
"""S-83 / T-279 — populated `backend_fallback_chain` in recall events.

Background:
  Over the 13-day dogfood window, 30/30 observed recall events wrote
  ``backend_fallback_chain: {}`` because nothing populated it. The
  legacy simple-stream events (``backend.fallback`` factory-time and
  ``backend.runtime_fallback`` chain-time) carried 981 aggregate-count
  rows over the same window. S-83 keeps both paths as **complementary
  contracts** with distinct semantics:

    * Legacy simple-stream  = aggregate count per (capability, error_type).
    * Structured recall stream field = per-query cascade trace.

  This test file covers AC-4 (≥ 3 tests) on the structured path and
  also verifies the legacy path still fires (no regression).

AC-4 coverage (4 tests):
  1. Single-backend resolution writes ``[name]`` for each capability.
  2. ``FallbackChain`` resolution writes the cascade
     (e.g. ``["gemma", "haiku", "null"]``).
  3. Legacy ``backend.fallback*`` simple-stream events still fire on a
     real fallback (no regression of the complementary path).
  4. Aggregator's ``backend_summary()`` reads the structured field
     correctly into its ``per_capability_fallback`` view.

All tests are hermetic — they patch ``Path.home()`` to a tmp dir and
substitute fake backends so no real Haiku / Gemma / network calls
happen.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from depthfusion.backends.base import (
    BackendOverloadError,
)

SIX_CAPS = (
    "reranker",
    "extractor",
    "linker",
    "summariser",
    "embedding",
    "decision_extractor",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _metrics_dir(tmp_path: Path) -> Path:
    return tmp_path / ".claude" / "depthfusion-metrics"


def _read_recall_events(tmp_path: Path) -> list[dict]:
    files = list(_metrics_dir(tmp_path).glob("*-recall.jsonl"))
    if not files:
        return []
    return [
        json.loads(line)
        for line in files[0].read_text().splitlines()
        if line.strip()
    ]


def _read_simple_stream(tmp_path: Path) -> list[dict]:
    """Read the daily simple-stream JSONL file.

    Format: ``YYYY-MM-DD.jsonl`` (no suffix). Used for legacy
    ``backend.fallback`` and ``backend.runtime_fallback`` events.
    """
    today = date.today().isoformat()
    file_path = _metrics_dir(tmp_path) / f"{today}.jsonl"
    if not file_path.exists():
        return []
    return [
        json.loads(line)
        for line in file_path.read_text().splitlines()
        if line.strip()
    ]


def _minimal_corpus(tmp_path: Path) -> None:
    """Write one discovery file so _tool_recall has a non-empty corpus."""
    disc = tmp_path / ".claude" / "shared" / "discoveries"
    disc.mkdir(parents=True, exist_ok=True)
    (disc / "sample.md").write_text(
        "# Test\n\nfallback chain canonical-emission verification.\n",
        encoding="utf-8",
    )


class _FakeBackend:
    """Minimal ``LLMBackend`` stand-in with a known ``name`` attribute.

    Health is configurable so tests can drive ``FallbackChain`` cascades.
    """

    def __init__(self, name: str, *, healthy: bool = True) -> None:
        self.name = name
        self._healthy = healthy

    def healthy(self) -> bool:
        return self._healthy

    def complete(self, prompt: str, *, max_tokens: int, system: str | None = None) -> str:
        return ""

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None

    def rerank(self, query: str, docs: list[str], top_k: int):
        return []

    def extract_structured(self, prompt: str, schema: dict) -> dict | None:
        return None


# ---------------------------------------------------------------------------
# Test 1: single-backend resolution -> [name]
# ---------------------------------------------------------------------------

class TestSingleBackendResolution:
    """When ``get_backend(cap)`` returns a single backend (no chain),
    the structured field must record ``{cap: [name]}`` for each cap.

    This is the common case during local-only mode where every
    capability resolves to ``null``.
    """

    def test_single_backend_writes_list_of_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _minimal_corpus(tmp_path)

        from depthfusion.mcp import server as srv_mod

        # Patch _detect_current_backends to simulate the new S-83 hook:
        # when a fallback_chain dict is passed, populate it with [name]
        # for each capability (single-backend resolution).
        def fake_detect(perf_ms=None, fallback_chain=None):
            backends = {c: "null" for c in SIX_CAPS}
            if perf_ms is not None:
                for cap in SIX_CAPS:
                    perf_ms[cap] = 0.1
            if fallback_chain is not None:
                for cap in SIX_CAPS:
                    fallback_chain[cap] = ["null"]
            return backends

        monkeypatch.setattr(srv_mod, "_detect_current_backends", fake_detect)

        srv_mod._tool_recall({"query": "fallback chain", "top_k": 2})

        events = _read_recall_events(tmp_path)
        assert events, "Expected at least one recall event on disk"
        chain = events[0]["backend_fallback_chain"]

        # Field must be populated (not the historical empty {})
        assert chain, (
            "backend_fallback_chain unexpectedly empty — S-83 regression"
        )
        # Every capability in backend_used has a corresponding chain entry
        for cap in SIX_CAPS:
            assert cap in chain, f"missing chain entry for {cap!r}: {chain}"
            assert chain[cap] == ["null"], (
                f"single-backend resolution should write [name]; "
                f"got chain[{cap!r}] = {chain[cap]!r}"
            )


# ---------------------------------------------------------------------------
# Test 2: FallbackChain resolution -> [name1, name2, ...]
# ---------------------------------------------------------------------------

class TestFallbackChainResolution:
    """When ``get_backend(cap)`` returns a ``FallbackChain``, the
    structured field must record the full cascade (split from
    ``backend.name`` on ``+``) in declared order.
    """

    def test_chain_name_split_writes_full_cascade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _minimal_corpus(tmp_path)

        from depthfusion.backends.chain import FallbackChain
        from depthfusion.mcp import server as srv_mod

        # Build a real FallbackChain so the test exercises the actual
        # name-joining logic, not just a stubbed string.
        chain = FallbackChain([
            _FakeBackend("gemma"),
            _FakeBackend("haiku"),
            _FakeBackend("null"),
        ])
        assert chain.name == "gemma+haiku+null", (
            "Pre-condition: FallbackChain.name composes as expected"
        )

        def fake_get_backend(cap: str):
            # Reranker resolves to a real chain; others to single backends.
            if cap == "reranker":
                return chain
            return _FakeBackend("null")

        # Patch get_backend at the module the server imports it from.
        monkeypatch.setattr(
            "depthfusion.backends.factory.get_backend",
            fake_get_backend,
        )

        srv_mod._tool_recall({"query": "fallback chain split", "top_k": 2})

        events = _read_recall_events(tmp_path)
        assert events, "Expected at least one recall event on disk"
        chain_field = events[0]["backend_fallback_chain"]

        # Reranker must record the full cascade (split on '+')
        assert chain_field.get("reranker") == ["gemma", "haiku", "null"], (
            f"FallbackChain resolution should split name on '+'; "
            f"got reranker chain = {chain_field.get('reranker')!r}"
        )
        # Other capabilities are single-backend so they record [name]
        for cap in ("extractor", "linker", "summariser", "embedding",
                    "decision_extractor"):
            assert chain_field.get(cap) == ["null"], (
                f"single-backend resolution for {cap!r} should write "
                f"['null']; got {chain_field.get(cap)!r}"
            )


# ---------------------------------------------------------------------------
# Test 3: Legacy `backend.fallback*` simple-stream events still fire
# ---------------------------------------------------------------------------

class TestLegacySimpleStreamStillFires:
    """The legacy aggregate-count emission path is **complementary** to
    the new structured field — not deprecated. A real fallback (typed
    error in a chain) must still produce a ``backend.runtime_fallback``
    record in the simple stream so rate-dashboard consumers don't
    silently lose data.

    Regression guard: if a future change collapses the two paths into
    one, this test fails loudly.
    """

    def test_runtime_fallback_event_emitted_on_chain_transition(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        # Reset the module-level cached collector so it picks up the new
        # patched home directory for this test's writes.
        from depthfusion.backends import chain as chain_mod
        chain_mod._reset_metrics_collector()
        try:
            # Build a chain whose primary raises BackendOverloadError so
            # the fallback path fires once.
            primary = _FakeBackend("gemma")
            secondary = _FakeBackend("null")
            cascade = chain_mod.FallbackChain([primary, secondary])

            # Drive the chain through `complete` so the typed-error path
            # is hit on `gemma` and falls through to `null`.
            def primary_complete(*args, **kwargs):
                raise BackendOverloadError("simulated overload")

            primary.complete = primary_complete  # type: ignore[assignment]

            cascade.complete("hello", max_tokens=8)

            # The simple-stream daily JSONL must contain a
            # ``backend.runtime_fallback`` record for this transition.
            simple_events = _read_simple_stream(tmp_path)
            runtime_events = [
                e for e in simple_events
                if e.get("metric") == "backend.runtime_fallback"
            ]
            assert runtime_events, (
                "Legacy `backend.runtime_fallback` simple-stream event "
                "did not fire after a chain transition — "
                "S-83 must NOT regress the complementary aggregate-count "
                "path. Simple-stream events seen: "
                f"{[e.get('metric') for e in simple_events]}"
            )
            event = runtime_events[0]
            labels = event.get("labels", {})
            assert labels.get("from") == "gemma", labels
            assert labels.get("to") == "null", labels
            assert labels.get("error_type") == "BackendOverloadError", labels
            assert labels.get("capability") == "complete", labels
        finally:
            # Cleanup so the cached collector doesn't leak across tests.
            chain_mod._reset_metrics_collector()


# ---------------------------------------------------------------------------
# Test 4: aggregator reads both paths correctly
# ---------------------------------------------------------------------------

class TestAggregatorReadsStructuredField:
    """``backend_summary().per_capability_fallback`` must reflect the
    structured field populated by S-83. Prior to S-83 the field was
    always empty, so this view was always empty — now it carries
    real per-capability cascade names.
    """

    def test_backend_summary_per_capability_fallback_populated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _minimal_corpus(tmp_path)

        from depthfusion.mcp import server as srv_mod
        from depthfusion.metrics.aggregator import MetricsAggregator
        from depthfusion.metrics.collector import MetricsCollector

        # Stub _detect_current_backends so reranker reports a chain and
        # other caps report single backends.
        def fake_detect(perf_ms=None, fallback_chain=None):
            backends = {
                "reranker": "gemma+haiku+null",
                "extractor": "null",
                "linker": "null",
                "summariser": "null",
                "embedding": "null",
                "decision_extractor": "null",
            }
            if perf_ms is not None:
                for cap in SIX_CAPS:
                    perf_ms[cap] = 0.05
            if fallback_chain is not None:
                fallback_chain["reranker"] = ["gemma", "haiku", "null"]
                for cap in ("extractor", "linker", "summariser", "embedding",
                            "decision_extractor"):
                    fallback_chain[cap] = ["null"]
            return backends

        monkeypatch.setattr(srv_mod, "_detect_current_backends", fake_detect)

        # Run two recall queries so per_capability_fallback has a non-trivial
        # set of names (validates the set-merge logic).
        srv_mod._tool_recall({"query": "first query", "top_k": 2})
        srv_mod._tool_recall({"query": "second query", "top_k": 2})

        # Aggregate today's recall stream
        collector = MetricsCollector()
        aggregator = MetricsAggregator(collector)
        summary = aggregator.backend_summary()

        assert summary, "Expected non-empty backend_summary"
        per_cap_fallback = summary.get("per_capability_fallback", {})
        assert per_cap_fallback, (
            "per_capability_fallback was empty — aggregator did not see "
            "the populated structured field"
        )

        # reranker's set should contain all three cascade members
        reranker_chains = set(per_cap_fallback.get("reranker", []))
        assert {"gemma", "haiku", "null"}.issubset(reranker_chains), (
            f"reranker per_capability_fallback should contain the full "
            f"cascade; got {reranker_chains!r}"
        )
        # Single-backend caps record only ['null']
        for cap in ("extractor", "linker", "summariser", "embedding",
                    "decision_extractor"):
            assert set(per_cap_fallback.get(cap, [])) == {"null"}, (
                f"{cap} per_capability_fallback should be {{'null'}}; "
                f"got {per_cap_fallback.get(cap)!r}"
            )
