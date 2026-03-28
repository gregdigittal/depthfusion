"""Tests for fusion/rrf.py — Reciprocal Rank Fusion."""
from __future__ import annotations

from depthfusion.fusion.rrf import fuse, rrf_score


class TestRrfScore:
    def test_single_rank_formula(self):
        """1/(k + rank) for k=60, rank=1 → 1/61."""
        result = rrf_score([1], k=60)
        assert abs(result - 1 / 61) < 1e-10

    def test_multiple_ranks_sum(self):
        """Score across two lists with ranks 1 and 3 → 1/61 + 1/63."""
        result = rrf_score([1, 3], k=60)
        expected = 1 / 61 + 1 / 63
        assert abs(result - expected) < 1e-10

    def test_default_k_is_60(self):
        """Default k=60 is used when not specified."""
        result_default = rrf_score([2])
        result_explicit = rrf_score([2], k=60)
        assert result_default == result_explicit

    def test_custom_k(self):
        """Custom k parameter changes the score."""
        result = rrf_score([1], k=10)
        assert abs(result - 1 / 11) < 1e-10

    def test_empty_ranks_returns_zero(self):
        """Empty rank list → 0.0."""
        assert rrf_score([]) == 0.0


class TestFuse:
    def test_empty_lists_return_empty(self):
        """Empty input → empty result."""
        assert fuse([]) == []

    def test_single_list_passthrough_order(self):
        """Single ranked list: order is preserved (higher rank = lower number = higher score)."""
        result = fuse([["doc_a", "doc_b", "doc_c"]])
        doc_ids = [r[0] for r in result]
        assert doc_ids == ["doc_a", "doc_b", "doc_c"]

    def test_doc_in_all_lists_scores_higher(self):
        """A doc appearing in all lists scores higher than one in only one list."""
        list1 = ["alpha", "beta", "gamma"]
        list2 = ["alpha", "gamma", "delta"]
        # alpha appears in both lists at rank 1; delta only in list2 at rank 3
        result = fuse([list1, list2])
        score_map = {doc_id: score for doc_id, score in result}
        assert score_map["alpha"] > score_map["delta"]

    def test_formula_exact_for_known_input(self):
        """Verify exact RRF score for known ranks with k=60."""
        # doc_x at rank 1 in list1, rank 2 in list2
        # expected score = 1/61 + 1/62
        result = fuse([["doc_x", "doc_y"], ["doc_z", "doc_x"]], k=60)
        score_map = {doc_id: score for doc_id, score in result}
        expected = 1 / 61 + 1 / 62
        assert abs(score_map["doc_x"] - expected) < 1e-10

    def test_result_sorted_descending(self):
        """Result is sorted by score descending."""
        result = fuse([["a", "b", "c"], ["b", "a", "c"]])
        scores = [score for _, score in result]
        assert scores == sorted(scores, reverse=True)

    def test_ties_broken_consistently(self):
        """Documents with equal scores appear in a stable, deterministic order."""
        # gamma and delta each appear only once at rank 1 in separate lists → equal scores
        list1 = ["alpha", "gamma"]
        list2 = ["alpha", "delta"]
        result1 = fuse([list1, list2])
        result2 = fuse([list1, list2])
        assert result1 == result2  # deterministic

    def test_custom_k_propagates(self):
        """Custom k changes the scores."""
        result_60 = fuse([["a", "b"]], k=60)
        result_10 = fuse([["a", "b"]], k=10)
        score_map_60 = {d: s for d, s in result_60}
        score_map_10 = {d: s for d, s in result_10}
        # With smaller k, the score is higher
        assert score_map_10["a"] > score_map_60["a"]

    def test_all_list_empty_strings_handled(self):
        """Empty ranked list within the outer list is skipped gracefully."""
        result = fuse([[], ["doc_a", "doc_b"]])
        doc_ids = [r[0] for r in result]
        assert "doc_a" in doc_ids
        assert "doc_b" in doc_ids
