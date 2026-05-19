"""Tests for tools/bench_cat_d.py — CIQS Category D benchmark harness (E-40 / S-125).

AC-4: ≥ 5 tests covering fixture loading, score computation, edges=off vs on
comparison, report serialisation, and back-compat with fixtures that have no
PRECEDED_BY edges.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the harness module from tools/bench_cat_d.py via importlib
# ---------------------------------------------------------------------------
_HARNESS_PATH = Path(__file__).parents[2] / "tools" / "bench_cat_d.py"
spec = importlib.util.spec_from_file_location("bench_cat_d", _HARNESS_PATH)
assert spec is not None and spec.loader is not None
_mod = importlib.util.module_from_spec(spec)
sys.modules["bench_cat_d"] = _mod
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

load_fixtures = _mod.load_fixtures
run_benchmark = _mod.run_benchmark
score_results = _mod.score_results
aggregate = _mod.aggregate
recall = _mod.recall
_build_graph = _mod._build_graph
_InMemoryGraphStore = _mod._InMemoryGraphStore
FixtureScore = _mod.FixtureScore


# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

# Two-session temporal chain: query matches sess-A well, sess-B poorly (BM25).
# PRECEDED_BY edge from sess-A → sess-B lets the traversal rescue sess-B.
#
# The distractor is intentionally keyword-rich for "authentication" so that
# BM25 alone puts the distractor above sess-B; traversal from sess-A boosts
# sess-B above the distractor.
_EDGE_CASE_FIXTURE = {
    "query": "recent changes to authentication",
    "description": "traversal-rescue test — BM25 alone misses sess-B",
    "session_graph": {
        "entities": [
            {
                "session_id": "sess-A",
                "project": "test",
                "summary": "Auth module refactor",
            },
            {
                "session_id": "sess-B",
                "project": "test",
                "summary": "Login handler followup",
            },
        ],
        "edges": [
            {
                "source": "sess-A",
                "target": "sess-B",
                "relationship": "PRECEDED_BY",
                "delta_hours": 24.0,
            },
        ],
    },
    "corpus": [
        # High-BM25 distractor for "authentication" / "recent" / "changes"
        {
            "chunk_id": "distr#0",
            "source": "memory",
            "content": (
                "Authentication best practices guide. "
                "Recent changes to authentication systems require careful review. "
                "Authentication standards continue to evolve. "
                "Changes to authentication libraries are frequent."
            ),
        },
        # Relevant: strong BM25 match ("authentication", "changes")
        {
            "chunk_id": "sess-A#0",
            "source": "session",
            "content": (
                "## Auth Module Refactor\n\n"
                "Refactored the authentication layer. "
                "Moved authentication logic to a dedicated module. "
                "Applied changes throughout the codebase."
            ),
        },
        # Relevant: WEAK BM25 match (no "authentication", "recent", or "changes")
        {
            "chunk_id": "sess-B#0",
            "source": "session",
            "content": (
                "## Login Handler Update\n\n"
                "Updated login handler to use new auth module. "
                "Fixed session cookie expiry bug. "
                "Rewrote token validation logic."
            ),
        },
    ],
    "relevant_chunk_ids": ["sess-A#0", "sess-B#0"],
}

# Simple fixture with no edges (back-compat test)
_NO_EDGES_FIXTURE = {
    "query": "what was recently shipped",
    "description": "no-edges fixture — harness must not crash",
    "session_graph": {
        "entities": [
            {"session_id": "sess-X", "project": "test", "summary": "Feature X"},
        ],
        "edges": [],
    },
    "corpus": [
        {"chunk_id": "sess-X#0", "source": "session", "content": "Shipped feature X."},
        {"chunk_id": "other#0", "source": "memory", "content": "Unrelated document."},
    ],
    "relevant_chunk_ids": ["sess-X#0"],
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFixtureLoading:
    """AC-4 (fixture loading): harness loads JSONL, returns well-formed dicts."""

    def test_loads_real_fixture_file(self) -> None:
        fixture_dir = Path(__file__).parents[2] / "tests" / "fixtures" / "ciqs_cat_d"
        fixtures = load_fixtures(fixture_dir)
        assert len(fixtures) >= 10, "Expected ≥ 10 fixtures (ADR AC-3)"
        for fix in fixtures:
            assert "query" in fix
            assert "corpus" in fix
            assert "relevant_chunk_ids" in fix
            assert "session_graph" in fix

    def test_skips_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        fixtures = load_fixtures(tmp_path / "nonexistent")
        assert fixtures == []

    def test_loads_jsonl_with_multiple_lines(self, tmp_path: Path) -> None:
        fixture_file = tmp_path / "test.jsonl"
        lines = [
            json.dumps(_NO_EDGES_FIXTURE),
            json.dumps(_EDGE_CASE_FIXTURE),
        ]
        fixture_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        fixtures = load_fixtures(tmp_path)
        assert len(fixtures) == 2


class TestScoreComputation:
    """AC-4 (score computation): MRR and hit@k computed correctly."""

    def test_mrr_is_one_when_first_result_is_relevant(self) -> None:
        ranked = [
            {"chunk_id": "a#0", "score": 3.0},
            {"chunk_id": "b#0", "score": 2.0},
            {"chunk_id": "c#0", "score": 1.0},
        ]
        s = score_results(ranked, ["a#0", "b#0"])
        assert s.mrr == pytest.approx(1.0)
        assert s.hit_at_1 == 1

    def test_mrr_when_first_relevant_at_rank_two(self) -> None:
        ranked = [
            {"chunk_id": "distractor#0", "score": 5.0},
            {"chunk_id": "relevant#0", "score": 3.0},
            {"chunk_id": "other#0", "score": 1.0},
        ]
        s = score_results(ranked, ["relevant#0"])
        assert s.mrr == pytest.approx(0.5)
        assert s.hit_at_1 == 0
        assert s.hit_at_3 == 1

    def test_mrr_is_zero_when_no_relevant_in_results(self) -> None:
        ranked = [{"chunk_id": "x#0", "score": 1.0}]
        s = score_results(ranked, ["missing#0"])
        assert s.mrr == pytest.approx(0.0)
        assert s.hit_at_1 == 0
        assert s.hit_at_5 == 0

    def test_aggregate_averages_mrr_across_fixtures(self) -> None:
        scores = [FixtureScore(mrr=1.0, hit_at_1=1, hit_at_3=1, hit_at_5=1),
                  FixtureScore(mrr=0.5, hit_at_1=0, hit_at_3=1, hit_at_5=1)]
        agg = aggregate(scores)
        assert agg["mrr"] == pytest.approx(0.75)
        assert agg["hit_at_1"] == pytest.approx(0.5)
        assert agg["hit_at_3"] == pytest.approx(1.0)

    def test_aggregate_empty_list_returns_zeros(self) -> None:
        agg = aggregate([])
        assert agg["mrr"] == pytest.approx(0.0)


class TestEdgesOffVsOn:
    """AC-4 (edges off/on): PRECEDED_BY traversal rescues a BM25-missed session."""

    def test_traversal_boosts_low_bm25_neighbour(self) -> None:
        # edges=off: BM25 distractor beats sess-B → first relevant at rank 2 → MRR=0.5
        # edges=on:  traversal from sess-A boosts sess-B above distractor → MRR=1.0
        fixture = _EDGE_CASE_FIXTURE
        corpus = fixture["corpus"]
        session_graph = fixture["session_graph"]
        query = fixture["query"]
        relevant_ids = fixture["relevant_chunk_ids"]

        store_off = _build_graph(session_graph, include_edges=False)
        store_on = _build_graph(session_graph, include_edges=True)

        ranked_off = recall(query, corpus, store_off, use_graph=True)
        ranked_on = recall(query, corpus, store_on, use_graph=True)

        score_off = score_results(ranked_off, relevant_ids)
        score_on = score_results(ranked_on, relevant_ids)

        # edges=off: distractor at rank 1 → first relevant at rank 2
        assert score_off.mrr == pytest.approx(0.5), (
            "BM25-only should put distractor at rank 1 (MRR=0.5)"
        )
        # edges=on: sess-B boosted above distractor → first relevant at rank 1
        assert score_on.mrr == pytest.approx(1.0), (
            "Traversal should rescue sess-B to rank 1 (MRR=1.0)"
        )
        assert score_on.mrr >= score_off.mrr

    def test_delta_mrr_pp_is_positive(self) -> None:
        report = run_benchmark([_EDGE_CASE_FIXTURE])
        assert report["delta_mrr_pp"] > 0.0

    def test_s50_ac3_met_when_delta_gte_2pp(self) -> None:
        report = run_benchmark([_EDGE_CASE_FIXTURE])
        # delta for this purpose-built fixture is 50pp (0.5 → 1.0)
        assert report["s50_ac3_met"] is True

    def test_no_degradation_when_edges_already_rank_correctly(self) -> None:
        # When BM25 already finds both relevant items, edges=on MRR >= edges=off MRR
        simple_fixture = {
            "query": "JWT authentication token",
            "session_graph": {
                "entities": [
                    {"session_id": "s1", "project": "t", "summary": "JWT impl"},
                    {"session_id": "s2", "project": "t", "summary": "JWT followup"},
                ],
                "edges": [
                    {"source": "s1", "target": "s2", "relationship": "PRECEDED_BY", "delta_hours": 12.0},
                ],
            },
            "corpus": [
                {"chunk_id": "s1#0", "source": "session",
                 "content": "Implemented JWT token rotation and refresh logic."},
                {"chunk_id": "s2#0", "source": "session",
                 "content": "Added JWT authentication middleware. Wired to prior session."},
                {"chunk_id": "unrela#0", "source": "memory",
                 "content": "CSS variables guide. Primary colour: --color-primary."},
            ],
            "relevant_chunk_ids": ["s1#0", "s2#0"],
        }
        report = run_benchmark([simple_fixture])
        assert report["edges_on"]["mrr"] >= report["edges_off"]["mrr"]


class TestReportSerialisation:
    """AC-4 (report serialisation): JSON report written with the ADR schema."""

    def test_report_has_required_keys(self) -> None:
        report = run_benchmark([_EDGE_CASE_FIXTURE])
        for key in (
            "run_date", "fixture_count", "edges_off", "edges_on",
            "delta_mrr_pp", "s50_ac3_met", "per_fixture",
        ):
            assert key in report, f"Missing key: {key}"

    def test_report_written_to_output_path(self, tmp_path: Path) -> None:
        out = tmp_path / "report.json"
        # Simulate what main() does: run benchmark, write to path
        report = run_benchmark([_NO_EDGES_FIXTURE, _EDGE_CASE_FIXTURE])
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")

        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["fixture_count"] == 2
        assert len(loaded["per_fixture"]) == 2

    def test_per_fixture_has_delta_field(self) -> None:
        report = run_benchmark([_EDGE_CASE_FIXTURE])
        pf = report["per_fixture"][0]
        assert "delta_mrr_pp" in pf
        assert pf["delta_mrr_pp"] == pytest.approx(50.0, abs=1e-3)

    def test_report_fixture_count_matches_input(self) -> None:
        fixtures = [_NO_EDGES_FIXTURE, _EDGE_CASE_FIXTURE, _NO_EDGES_FIXTURE]
        report = run_benchmark(fixtures)
        assert report["fixture_count"] == 3


class TestBackCompatNoEdges:
    """AC-4 (back-compat): fixtures with no PRECEDED_BY edges run without errors."""

    def test_no_edges_fixture_does_not_crash(self) -> None:
        report = run_benchmark([_NO_EDGES_FIXTURE])
        assert report["fixture_count"] == 1
        assert report["delta_mrr_pp"] == pytest.approx(0.0)
        assert report["s50_ac3_met"] is False

    def test_empty_session_graph_handled(self) -> None:
        fixture = {
            "query": "recent work",
            "session_graph": {"entities": [], "edges": []},
            "corpus": [
                {"chunk_id": "a#0", "source": "session", "content": "Did some recent work."},
            ],
            "relevant_chunk_ids": ["a#0"],
        }
        report = run_benchmark([fixture])
        assert report["edges_on"]["mrr"] == pytest.approx(1.0)

    def test_missing_session_graph_key_handled(self) -> None:
        fixture = {
            "query": "recent work",
            "corpus": [
                {"chunk_id": "a#0", "source": "session", "content": "Did recent work."},
            ],
            "relevant_chunk_ids": ["a#0"],
        }
        report = run_benchmark([fixture])
        assert report["fixture_count"] == 1
