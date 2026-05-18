"""Tests for core/hit_tracker.py — HitTracker persistent hit log."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from depthfusion.core.hit_tracker import HitTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tracker(tmp_path: Path) -> HitTracker:
    return HitTracker(log_path=tmp_path / "hits.jsonl")


# ---------------------------------------------------------------------------
# TestHitTrackerSingleton
# ---------------------------------------------------------------------------

class TestHitTrackerSingleton:
    def setup_method(self) -> None:
        HitTracker.reset_singleton()

    def teardown_method(self) -> None:
        HitTracker.reset_singleton()

    def test_singleton_returns_same_instance(self) -> None:
        """Two calls to singleton() return the same object."""
        a = HitTracker.singleton()
        b = HitTracker.singleton()
        assert a is b

    def test_reset_singleton_allows_new_instance(self) -> None:
        """After reset_singleton(), next call creates a fresh instance."""
        a = HitTracker.singleton()
        HitTracker.reset_singleton()
        b = HitTracker.singleton()
        assert a is not b


# ---------------------------------------------------------------------------
# TestRegisterHits
# ---------------------------------------------------------------------------

class TestRegisterHits:
    def test_register_creates_file(self, tmp_path: Path) -> None:
        """File doesn't exist → register_hits → file created."""
        tracker = _make_tracker(tmp_path)
        log_path = tmp_path / "hits.jsonl"
        assert not log_path.exists()
        tracker.register_hits(["chunk-1"])
        assert log_path.exists()

    def test_register_writes_one_line_per_chunk(self, tmp_path: Path) -> None:
        """3 chunk_ids → 3 JSONL lines."""
        tracker = _make_tracker(tmp_path)
        tracker.register_hits(["a", "b", "c"])
        lines = (tmp_path / "hits.jsonl").read_text().splitlines()
        assert len(lines) == 3
        for line in lines:
            json.loads(line)  # must be valid JSON

    def test_register_empty_list_is_noop(self, tmp_path: Path) -> None:
        """Empty list → no file created / no write."""
        tracker = _make_tracker(tmp_path)
        tracker.register_hits([])
        assert not (tmp_path / "hits.jsonl").exists()

    def test_register_stores_query(self, tmp_path: Path) -> None:
        """'q' field in JSONL line matches query arg."""
        tracker = _make_tracker(tmp_path)
        tracker.register_hits(["chunk-x"], query="my search query")
        line = (tmp_path / "hits.jsonl").read_text().strip()
        entry = json.loads(line)
        assert entry["q"] == "my search query"

    def test_register_appends_on_repeated_calls(self, tmp_path: Path) -> None:
        """Two register_hits calls → two lines cumulative."""
        tracker = _make_tracker(tmp_path)
        tracker.register_hits(["chunk-1"])
        tracker.register_hits(["chunk-2"])
        lines = (tmp_path / "hits.jsonl").read_text().splitlines()
        assert len(lines) == 2

    def test_register_stores_chunk_id_and_ts(self, tmp_path: Path) -> None:
        """Each JSONL line contains chunk_id and ts fields."""
        tracker = _make_tracker(tmp_path)
        before = time.time()
        tracker.register_hits(["chunk-abc"])
        after = time.time()
        line = (tmp_path / "hits.jsonl").read_text().strip()
        entry = json.loads(line)
        assert entry["chunk_id"] == "chunk-abc"
        assert before <= entry["ts"] <= after


# ---------------------------------------------------------------------------
# TestGetHits30d
# ---------------------------------------------------------------------------

class TestGetHits30d:
    def test_returns_zero_when_no_file(self, tmp_path: Path) -> None:
        """No log file → get_hits_30d returns 0."""
        tracker = _make_tracker(tmp_path)
        assert tracker.get_hits_30d("chunk-missing") == 0

    def test_returns_zero_for_unknown_chunk(self, tmp_path: Path) -> None:
        """Known chunk registered, query for different id → 0."""
        tracker = _make_tracker(tmp_path)
        tracker.register_hits(["chunk-known"])
        assert tracker.get_hits_30d("chunk-unknown") == 0

    def test_counts_recent_hits(self, tmp_path: Path) -> None:
        """Register 3 times → get_hits_30d returns 3."""
        tracker = _make_tracker(tmp_path)
        tracker.register_hits(["chunk-a"])
        tracker.register_hits(["chunk-a"])
        tracker.register_hits(["chunk-a"])
        assert tracker.get_hits_30d("chunk-a") == 3

    def test_ignores_stale_entries(self, tmp_path: Path) -> None:
        """Manually write JSONL line with ts = now - 31*86400 → count = 0."""
        log_path = tmp_path / "hits.jsonl"
        stale_ts = time.time() - 31 * 86400
        log_path.write_text(
            json.dumps({"chunk_id": "chunk-stale", "ts": stale_ts, "q": ""}) + "\n"
        )
        tracker = HitTracker(log_path=log_path)
        assert tracker.get_hits_30d("chunk-stale") == 0

    def test_counts_only_within_window(self, tmp_path: Path) -> None:
        """Mix of stale and fresh entries — only fresh are counted."""
        log_path = tmp_path / "hits.jsonl"
        now = time.time()
        stale_ts = now - 31 * 86400
        fresh_ts = now - 1 * 86400
        lines = [
            json.dumps({"chunk_id": "chunk-x", "ts": stale_ts, "q": ""}) + "\n",
            json.dumps({"chunk_id": "chunk-x", "ts": fresh_ts, "q": ""}) + "\n",
            json.dumps({"chunk_id": "chunk-x", "ts": now, "q": ""}) + "\n",
        ]
        log_path.write_text("".join(lines))
        tracker = HitTracker(log_path=log_path)
        assert tracker.get_hits_30d("chunk-x") == 2


# ---------------------------------------------------------------------------
# TestPruning
# ---------------------------------------------------------------------------

class TestPruning:
    def test_prune_removes_stale(self, tmp_path: Path) -> None:
        """Write stale + fresh entries, call _prune_stale, only fresh remain."""
        log_path = tmp_path / "hits.jsonl"
        now = time.time()
        stale_ts = now - 31 * 86400
        fresh_ts = now - 1 * 86400
        lines = [
            json.dumps({"chunk_id": "chunk-stale", "ts": stale_ts, "q": ""}) + "\n",
            json.dumps({"chunk_id": "chunk-fresh", "ts": fresh_ts, "q": ""}) + "\n",
        ]
        log_path.write_text("".join(lines))

        tracker = HitTracker(log_path=log_path)
        # _prune_stale must be called under self._lock per the contract,
        # but we hold no external lock here — call directly for testing.
        tracker._prune_stale(now)

        remaining = log_path.read_text().splitlines()
        assert len(remaining) == 1
        entry = json.loads(remaining[0])
        assert entry["chunk_id"] == "chunk-fresh"

    def test_prune_no_file_is_noop(self, tmp_path: Path) -> None:
        """_prune_stale does nothing (no error) when the log file is absent."""
        tracker = _make_tracker(tmp_path)
        # Should not raise
        tracker._prune_stale(time.time())
        assert not (tmp_path / "hits.jsonl").exists()

    def test_prune_discards_corrupt_lines(self, tmp_path: Path) -> None:
        """Corrupt JSONL lines are silently discarded during prune."""
        log_path = tmp_path / "hits.jsonl"
        now = time.time()
        fresh_ts = now - 1 * 86400
        log_path.write_text(
            "NOT VALID JSON\n"
            + json.dumps({"chunk_id": "chunk-ok", "ts": fresh_ts, "q": ""}) + "\n"
        )
        tracker = HitTracker(log_path=log_path)
        tracker._prune_stale(now)
        lines = log_path.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["chunk_id"] == "chunk-ok"


# ---------------------------------------------------------------------------
# TestBoostFormula
# ---------------------------------------------------------------------------

class TestBoostFormula:
    """Verify query_hits_boost() formula min(1.0 + 0.1 * n, 1.5) against real function."""

    def test_zero_hits_returns_one(self, tmp_path: Path) -> None:
        """hits=0 → 1.0."""
        from depthfusion.retrieval.hybrid import query_hits_boost
        tracker = HitTracker(log_path=tmp_path / "hits.jsonl")
        assert query_hits_boost("x", tracker) == pytest.approx(1.0)

    def test_five_hits_returns_max(self, tmp_path: Path) -> None:
        """hits=5 → 1.0 + 0.1*5 = 1.5 (at cap)."""
        from depthfusion.retrieval.hybrid import query_hits_boost
        tracker = HitTracker(log_path=tmp_path / "hits.jsonl")
        tracker.register_hits(["x"] * 5)
        assert query_hits_boost("x", tracker) == pytest.approx(1.5)

    def test_ten_hits_capped_at_max(self, tmp_path: Path) -> None:
        """hits=10 → would be 2.0 but capped at 1.5."""
        from depthfusion.retrieval.hybrid import query_hits_boost
        tracker = HitTracker(log_path=tmp_path / "hits.jsonl")
        tracker.register_hits(["x"] * 10)
        assert query_hits_boost("x", tracker) == pytest.approx(1.5)

    def test_one_hit_returns_1_1(self, tmp_path: Path) -> None:
        """hits=1 → 1.1."""
        from depthfusion.retrieval.hybrid import query_hits_boost
        tracker = HitTracker(log_path=tmp_path / "hits.jsonl")
        tracker.register_hits(["x"])
        assert query_hits_boost("x", tracker) == pytest.approx(1.1)

    def test_three_hits_returns_1_3(self, tmp_path: Path) -> None:
        """hits=3 → 1.3."""
        from depthfusion.retrieval.hybrid import query_hits_boost
        tracker = HitTracker(log_path=tmp_path / "hits.jsonl")
        tracker.register_hits(["x"] * 3)
        assert query_hits_boost("x", tracker) == pytest.approx(1.3)
