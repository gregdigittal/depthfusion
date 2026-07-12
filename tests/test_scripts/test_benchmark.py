"""Tests for scripts/benchmark.py and tests/fixtures/recall_goldset.jsonl.

All tests run without API keys or real user files (~/.claude/).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDSET_PATH = REPO_ROOT / "tests" / "fixtures" / "recall_goldset.jsonl"
BENCHMARK_SCRIPT = REPO_ROOT / "scripts" / "benchmark.py"

REQUIRED_GOLDSET_KEYS = {"query", "relevant_chunk_ids", "corpus", "description"}
REQUIRED_CORPUS_DOC_KEYS = {"chunk_id", "source", "content"}
REQUIRED_METRIC_KEYS = {
    "p50_latency_ms",
    "p95_latency_ms",
    "precision_at_1",
    "precision_at_5",
    "hit_rate_at_5",
    "mrr_at_10",
    "ndcg_at_5",
    "fallback_rate",
    "cost_estimate_usd",
}


# ---------------------------------------------------------------------------
# Goldset fixture tests
# ---------------------------------------------------------------------------

class TestGoldsetFixture:
    """Validate the goldset JSONL file structure."""

    def test_goldset_file_exists(self):
        assert GOLDSET_PATH.exists(), f"Goldset not found: {GOLDSET_PATH}"

    def test_goldset_loads_without_error(self):
        entries = []
        with GOLDSET_PATH.open() as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)  # raises on bad JSON
                entries.append(entry)
        assert len(entries) > 0

    def test_goldset_has_at_least_8_entries(self):
        entries = []
        with GOLDSET_PATH.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        assert len(entries) >= 8, f"Expected >= 8 entries, got {len(entries)}"

    def test_all_entries_have_required_keys(self):
        with GOLDSET_PATH.open() as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                missing = REQUIRED_GOLDSET_KEYS - set(entry.keys())
                assert not missing, (
                    f"Line {lineno} missing keys: {missing!r}"
                )

    def test_relevant_chunk_ids_is_list_of_strings(self):
        with GOLDSET_PATH.open() as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                ids = entry["relevant_chunk_ids"]
                assert isinstance(ids, list), (
                    f"Line {lineno}: relevant_chunk_ids must be a list"
                )
                assert len(ids) >= 1, (
                    f"Line {lineno}: relevant_chunk_ids must be non-empty"
                )
                for chunk_id in ids:
                    assert isinstance(chunk_id, str), (
                        f"Line {lineno}: chunk_id {chunk_id!r} must be a string"
                    )

    def test_corpus_docs_have_required_keys(self):
        with GOLDSET_PATH.open() as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                corpus = entry["corpus"]
                assert isinstance(corpus, list), (
                    f"Line {lineno}: 'corpus' must be a list"
                )
                assert len(corpus) >= 1, (
                    f"Line {lineno}: corpus must have at least 1 document"
                )
                for doc_idx, doc in enumerate(corpus):
                    missing = REQUIRED_CORPUS_DOC_KEYS - set(doc.keys())
                    assert not missing, (
                        f"Line {lineno}, corpus[{doc_idx}] missing keys: {missing!r}"
                    )

    def test_relevant_chunk_ids_exist_in_corpus(self):
        """Each relevant_chunk_id must correspond to a corpus document in the same entry."""
        with GOLDSET_PATH.open() as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                corpus_ids = {doc["chunk_id"] for doc in entry["corpus"]}
                for chunk_id in entry["relevant_chunk_ids"]:
                    assert chunk_id in corpus_ids, (
                        f"Line {lineno}: relevant_chunk_id {chunk_id!r} "
                        f"not found in corpus chunk_ids: {corpus_ids!r}"
                    )


# ---------------------------------------------------------------------------
# Script subprocess tests
# ---------------------------------------------------------------------------

class TestBenchmarkScript:
    """Run scripts/benchmark.py via subprocess and validate the output."""

    def _run_benchmark(self, extra_args: list[str] | None = None) -> dict:
        """Run benchmark.py and return parsed JSON output."""
        cmd = [
            sys.executable,
            str(BENCHMARK_SCRIPT),
            "--goldset", str(GOLDSET_PATH),
            "--quiet",
        ]
        if extra_args:
            cmd.extend(extra_args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, (
            f"benchmark.py exited with {result.returncode}\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )
        return json.loads(result.stdout)

    def test_script_exists(self):
        assert BENCHMARK_SCRIPT.exists(), f"Missing: {BENCHMARK_SCRIPT}"

    def test_script_produces_valid_json(self):
        output = self._run_benchmark()
        assert isinstance(output, dict)

    def test_output_has_top_level_fields(self):
        output = self._run_benchmark()
        required_keys = {
            "generated_at", "git_hash", "mode", "top_k",
            "goldset_path", "query_count", "metrics", "per_query",
        }
        missing = required_keys - set(output.keys())
        assert not missing, f"Missing top-level keys: {missing!r}"

    def test_output_has_required_metric_keys(self):
        output = self._run_benchmark()
        metrics = output["metrics"]
        missing = REQUIRED_METRIC_KEYS - set(metrics.keys())
        assert not missing, f"Missing metric keys: {missing!r}"

    def test_metrics_have_value_and_basis_fields(self):
        output = self._run_benchmark()
        for key, metric in output["metrics"].items():
            assert "value" in metric, f"metrics.{key} missing 'value'"
            assert "basis" in metric, f"metrics.{key} missing 'basis'"

    def test_precision_at_1_in_valid_range(self):
        output = self._run_benchmark()
        val = output["metrics"]["precision_at_1"]["value"]
        assert 0.0 <= val <= 1.0, f"precision_at_1={val} out of [0, 1]"

    def test_precision_at_5_in_valid_range(self):
        output = self._run_benchmark()
        val = output["metrics"]["precision_at_5"]["value"]
        assert 0.0 <= val <= 1.0, f"precision_at_5={val} out of [0, 1]"

    def test_hit_rate_at_5_equals_precision_at_5(self):
        output = self._run_benchmark()
        p5 = output["metrics"]["precision_at_5"]["value"]
        hr5 = output["metrics"]["hit_rate_at_5"]["value"]
        assert p5 == hr5, f"precision_at_5={p5} != hit_rate_at_5={hr5}"

    def test_p50_latency_is_positive(self):
        output = self._run_benchmark()
        val = output["metrics"]["p50_latency_ms"]["value"]
        assert val > 0, f"p50_latency_ms={val} should be positive"

    def test_p95_latency_gte_p50_latency(self):
        output = self._run_benchmark()
        p50 = output["metrics"]["p50_latency_ms"]["value"]
        p95 = output["metrics"]["p95_latency_ms"]["value"]
        assert p95 >= p50, f"p95={p95} should be >= p50={p50}"

    def test_fallback_rate_in_valid_range(self):
        output = self._run_benchmark()
        val = output["metrics"]["fallback_rate"]["value"]
        assert 0.0 <= val <= 1.0, f"fallback_rate={val} out of [0, 1]"

    def test_cost_estimate_is_zero_in_local_mode(self):
        output = self._run_benchmark()
        val = output["metrics"]["cost_estimate_usd"]["value"]
        assert val == 0.0, f"cost_estimate_usd={val} should be 0.0 in local mode"

    def test_cost_estimate_basis_is_estimated(self):
        output = self._run_benchmark()
        basis = output["metrics"]["cost_estimate_usd"]["basis"]
        assert basis == "estimated", f"cost_estimate_usd basis={basis!r} expected 'estimated'"

    def test_latency_basis_is_measured(self):
        output = self._run_benchmark()
        for key in ("p50_latency_ms", "p95_latency_ms"):
            basis = output["metrics"][key]["basis"]
            assert basis == "measured", f"{key} basis={basis!r} expected 'measured'"

    def test_query_count_matches_goldset_size(self):
        output = self._run_benchmark()
        # Count goldset entries
        entries = []
        with GOLDSET_PATH.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(line)
        assert output["query_count"] == len(entries), (
            f"query_count={output['query_count']} != goldset size={len(entries)}"
        )

    def test_per_query_length_matches_query_count(self):
        output = self._run_benchmark()
        assert len(output["per_query"]) == output["query_count"]

    def test_per_query_entries_have_required_fields(self):
        output = self._run_benchmark()
        required = {"query", "relevant_chunk_ids", "retrieved_chunk_ids",
                    "top_1_hit", "top_k_hit", "latency_ms"}
        for i, entry in enumerate(output["per_query"]):
            missing = required - set(entry.keys())
            assert not missing, f"per_query[{i}] missing: {missing!r}"

    def test_top_k_flag_respected(self):
        output = self._run_benchmark(["--top-k", "3"])
        assert output["top_k"] == 3
        for entry in output["per_query"]:
            assert len(entry["retrieved_chunk_ids"]) <= 3

    def test_mode_flag_reflected_in_output(self):
        output = self._run_benchmark(["--mode", "local"])
        assert output["mode"] == "local"

    def test_output_to_file(self, tmp_path):
        out_file = tmp_path / "bench_output.json"
        cmd = [
            sys.executable,
            str(BENCHMARK_SCRIPT),
            "--goldset", str(GOLDSET_PATH),
            "--output", str(out_file),
            "--quiet",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, cwd=REPO_ROOT,
        )
        assert result.returncode == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "metrics" in data
