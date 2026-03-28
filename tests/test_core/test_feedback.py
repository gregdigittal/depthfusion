"""Tests for core/feedback.py — FeedbackStore JSONL and weight learning."""
import json

import pytest

from depthfusion.core.feedback import FeedbackStore
from depthfusion.core.types import FeedbackEntry


@pytest.fixture
def store(tmp_path):
    return FeedbackStore(path=tmp_path / "feedback.jsonl")


class TestFeedbackStoreAppend:
    def test_append_creates_file(self, store, tmp_path):
        entry = FeedbackEntry(query="q", source="memory", chunk_id="c1", relevant=True)
        store.append(entry)
        assert store.path.exists()

    def test_appended_entry_readable(self, store):
        entry = FeedbackEntry(query="test query", source="session", chunk_id="c2", relevant=False)
        store.append(entry)
        entries = store.read_all()
        assert len(entries) == 1
        assert entries[0].query == "test query"
        assert entries[0].relevant is False

    def test_multiple_appends_all_readable(self, store):
        for i in range(5):
            store.append(FeedbackEntry(query=f"q{i}", source="s", chunk_id=f"c{i}", relevant=i % 2 == 0))
        entries = store.read_all()
        assert len(entries) == 5

    def test_append_is_valid_jsonl(self, store):
        """Each line in the file must be valid JSON."""
        for i in range(3):
            store.append(FeedbackEntry(query=f"q{i}", source="s", chunk_id=f"c{i}", relevant=True))
        with open(store.path) as f:
            lines = f.readlines()
        assert len(lines) == 3
        for line in lines:
            json.loads(line)  # must not raise


class TestFeedbackStoreReadAll:
    def test_read_empty_store_returns_empty_list(self, store):
        assert store.read_all() == []

    def test_read_nonexistent_path_returns_empty(self, tmp_path):
        s = FeedbackStore(path=tmp_path / "nonexistent.jsonl")
        assert s.read_all() == []


class TestLearnSourceWeights:
    def test_all_relevant_gives_weight_one(self, store):
        for _ in range(10):
            store.append(FeedbackEntry(query="q", source="memory", chunk_id="c", relevant=True))
        weights = store.learn_source_weights()
        assert abs(weights.get("memory", 0) - 1.0) < 1e-9

    def test_all_irrelevant_gives_floor_weight(self, store):
        for _ in range(10):
            store.append(FeedbackEntry(query="q", source="memory", chunk_id="c", relevant=False))
        weights = store.learn_source_weights()
        assert weights["memory"] == pytest.approx(0.1)

    def test_mixed_relevance_is_precision(self, store):
        """5 relevant out of 10 → weight = 0.5"""
        for i in range(10):
            store.append(FeedbackEntry(query="q", source="memory", chunk_id=f"c{i}", relevant=i < 5))
        weights = store.learn_source_weights()
        assert weights["memory"] == pytest.approx(0.5)

    def test_multiple_sources_weighted_independently(self, store):
        for i in range(10):
            store.append(FeedbackEntry(query="q", source="memory", chunk_id=f"c{i}", relevant=True))
        for i in range(10):
            store.append(FeedbackEntry(query="q", source="session", chunk_id=f"s{i}", relevant=i < 3))
        weights = store.learn_source_weights()
        assert weights["memory"] == pytest.approx(1.0)
        assert weights["session"] == pytest.approx(0.3)

    def test_unknown_source_defaults_to_one(self, store):
        """Sources with no feedback data get neutral weight 1.0."""
        weights = store.learn_source_weights()
        assert weights.get("unknown_source", 1.0) == 1.0

    def test_weight_never_below_floor(self, store):
        """Even 0% precision → floored at 0.1."""
        for i in range(20):
            store.append(FeedbackEntry(query="q", source="bad_source", chunk_id=f"c{i}", relevant=False))
        weights = store.learn_source_weights()
        assert weights["bad_source"] >= 0.1
