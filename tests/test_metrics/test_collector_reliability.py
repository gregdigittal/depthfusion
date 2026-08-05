# tests/test_metrics/test_collector_reliability.py
"""E-41 / S-126 — Metrics stream reliability tests.

AC-3: ≥ 4 new tests covering:
  - record() flock guard (routes through _append_jsonl, not raw open)
  - concurrent writers do not interleave lines
  - skipped_lines=0 on clean JSONL file
  - skipped_lines>0 on corrupt JSONL file
  - backend_summary back-compat: summaries from old JSONL without skipped_lines
  - chunk_ids field written by record_recall_query
  - chunk_ids=None omits the field (back-compat)
"""
from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest

from depthfusion.metrics.aggregator import MetricsAggregator, _iter_jsonl_counted
from depthfusion.metrics.collector import MetricsCollector

# ---------------------------------------------------------------------------
# Deterministic bounds for the cross-process concurrency test (S-254 Gate-3)
# ---------------------------------------------------------------------------
# test_concurrent_writers_do_not_interleave was observed to hang for >120s in a
# full-suite run. The mechanism is pre-existing and has nothing to do with the
# code under test: pytest makes the parent process multi-threaded, and the
# default `multiprocessing` start method on Linux is `fork`, so each pool worker
# is forked from a multi-threaded parent. CPython warns about exactly this at
# multiprocessing/popen_fork.py:66 ("This process is multi-threaded, use of
# fork() may lead to deadlocks in the child"), and a child that inherits a
# held lock never returns, so `pool.map` blocks forever.
#
# Two independent bounds are applied, both constant-driven so the cost of the
# test is visible in one place:
#
#   1. `_MP_CONTEXT` forces the `spawn` start method. A spawned child is a fresh
#      interpreter that inherits none of the parent's threads or locks, which
#      removes the deadlock class at its root rather than papering over it.
#   2. `@pytest.mark.timeout(_CONCURRENCY_TIMEOUT_SECONDS)` is an explicit
#      per-test bound. A marker beats pyproject's global `--timeout=30` and, more
#      importantly, survives a caller that overrides `addopts` — so even if the
#      spawn fix were ever reverted, this test can no longer hang a whole run.
#
# Worker/iteration counts are also constants: the assertion is about *whether*
# concurrent flock-guarded writes interleave, which 4 writers demonstrate as
# well as 40, so the numbers stay small enough that `spawn`'s higher per-worker
# start-up cost is irrelevant.
_CONCURRENT_WRITERS = 4
_WRITES_PER_WORKER = 20
_CONCURRENCY_TIMEOUT_SECONDS = 60
_MP_CONTEXT = multiprocessing.get_context("spawn")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_collector(tmp_path: Path) -> MetricsCollector:
    return MetricsCollector(metrics_dir=tmp_path)


def _write_records(args: tuple[Path, int]) -> None:
    """Worker: write `count` metric records from a subprocess."""
    metrics_dir, count = args
    c = MetricsCollector(metrics_dir=metrics_dir)
    for i in range(count):
        c.record("test.counter", float(i), {"worker": str(multiprocessing.current_process().pid)})


# ---------------------------------------------------------------------------
# E-41 AC-1: record() routes through _append_jsonl (flock guard)
# ---------------------------------------------------------------------------

class TestRecordFlockGuard:
    def test_record_writes_valid_jsonl(self, tmp_path: Path) -> None:
        """record() must write one parseable JSON line per call."""
        c = _make_collector(tmp_path)
        c.record("my.metric", 1.5, {"label": "a"})

        lines = list(_iter_jsonl_counted(c.today_path())[0])
        assert len(lines) == 1
        assert lines[0]["metric"] == "my.metric"
        assert lines[0]["value"] == 1.5

    @pytest.mark.timeout(_CONCURRENCY_TIMEOUT_SECONDS)
    def test_concurrent_writers_do_not_interleave(self, tmp_path: Path) -> None:
        """Multiple processes writing via record() must produce valid JSONL.

        This test verifies no line corruption under concurrent writes.
        Interleaved writes would produce unparseable lines, caught by
        _iter_jsonl_counted returning skipped_lines > 0.

        Bounded deliberately — see the ``_MP_CONTEXT`` /
        ``_CONCURRENCY_TIMEOUT_SECONDS`` comment at the top of this module for
        why a `spawn` context and an explicit per-test timeout are both used.
        """
        writes_per_worker = _WRITES_PER_WORKER
        num_workers = _CONCURRENT_WRITERS

        with _MP_CONTEXT.Pool(processes=num_workers) as pool:
            pool.map(
                _write_records,
                [(tmp_path, writes_per_worker)] * num_workers,
            )

        # Give OS a moment to flush
        time.sleep(0.05)

        c = _make_collector(tmp_path)
        entries, skipped = _iter_jsonl_counted(c.today_path())

        assert skipped == 0, f"Interleaved writes produced {skipped} malformed lines"
        assert len(entries) == writes_per_worker * num_workers


# ---------------------------------------------------------------------------
# E-41 AC-2: skipped_lines counter in _iter_jsonl_counted
# ---------------------------------------------------------------------------

class TestSkippedLinesCounter:
    def test_clean_file_returns_zero_skipped(self, tmp_path: Path) -> None:
        """No malformed lines → skipped_lines == 0."""
        p = tmp_path / "test.jsonl"
        p.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
        entries, skipped = _iter_jsonl_counted(p)
        assert len(entries) == 2
        assert skipped == 0

    def test_corrupt_file_counts_bad_lines(self, tmp_path: Path) -> None:
        """Malformed JSON lines are counted, valid lines still parsed."""
        p = tmp_path / "test.jsonl"
        p.write_text(
            '{"good": 1}\nNOT_JSON\n{"good": 2}\n{broken\n',
            encoding="utf-8",
        )
        entries, skipped = _iter_jsonl_counted(p)
        assert len(entries) == 2
        assert skipped == 2

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        entries, skipped = _iter_jsonl_counted(tmp_path / "nonexistent.jsonl")
        assert entries == []
        assert skipped == 0

    def test_backend_summary_includes_skipped_lines(self, tmp_path: Path) -> None:
        """backend_summary() must include skipped_lines key in result."""
        c = _make_collector(tmp_path)
        agg = MetricsAggregator(c)

        # Write one valid recall record
        c.record_recall_query(
            query_hash="abc123",
            mode="local",
            backend_used={"reranker": "haiku"},
            backend_fallback_chain={"reranker": ["haiku"]},
            total_latency_ms=42.0,
            result_count=3,
        )

        summary = agg.backend_summary()
        assert "skipped_lines" in summary
        assert summary["skipped_lines"] == 0

    def test_backend_summary_skipped_lines_nonzero_on_corrupt(
        self, tmp_path: Path
    ) -> None:
        """backend_summary() skipped_lines reflects actual malformed lines."""
        from datetime import date

        c = _make_collector(tmp_path)

        # Inject one corrupt line directly into the recall JSONL
        recall_path = c.metrics_dir / f"{date.today().isoformat()}-recall.jsonl"
        recall_path.parent.mkdir(parents=True, exist_ok=True)
        with open(recall_path, "a", encoding="utf-8") as f:
            f.write('{"event":"recall_query","event_subtype":"ok","query_hash":"x",'
                    '"mode":"local","backend_used":{"r":"h"},"backend_fallback_chain":{},'
                    '"latency_ms_per_capability":{},"total_latency_ms":1.0,'
                    '"result_count":1,"config_version_id":"none"}\n')
            f.write("NOT_JSON\n")

        agg = MetricsAggregator(c)
        summary = agg.backend_summary()
        assert summary.get("skipped_lines") == 1


# ---------------------------------------------------------------------------
# E-42 AC-2: chunk_ids field in record_recall_query
# ---------------------------------------------------------------------------

class TestChunkIdsField:
    def test_chunk_ids_written_when_provided(self, tmp_path: Path) -> None:
        """chunk_ids list must appear in the written JSONL record."""
        c = _make_collector(tmp_path)
        c.record_recall_query(
            query_hash="qhash01",
            mode="local",
            chunk_ids=["stem-a", "stem-b#1"],
        )
        entries, _ = _iter_jsonl_counted(c.today_recall_path())
        assert len(entries) == 1
        assert entries[0]["chunk_ids"] == ["stem-a", "stem-b#1"]

    def test_chunk_ids_none_omits_field(self, tmp_path: Path) -> None:
        """chunk_ids=None must NOT write the key (back-compat with old records)."""
        c = _make_collector(tmp_path)
        c.record_recall_query(
            query_hash="qhash02",
            mode="local",
            chunk_ids=None,
        )
        entries, _ = _iter_jsonl_counted(c.today_recall_path())
        assert len(entries) == 1
        assert "chunk_ids" not in entries[0]

    def test_chunk_ids_empty_list_written(self, tmp_path: Path) -> None:
        """chunk_ids=[] must be written as an explicit empty list."""
        c = _make_collector(tmp_path)
        c.record_recall_query(
            query_hash="qhash03",
            mode="local",
            chunk_ids=[],
        )
        entries, _ = _iter_jsonl_counted(c.today_recall_path())
        assert entries[0]["chunk_ids"] == []
