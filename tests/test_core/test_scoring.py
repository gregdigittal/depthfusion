"""Tests for core/scoring.py — softmax, cosine similarity, weighted aggregate."""
import pytest

from depthfusion.core.scoring import cosine_similarity, softmax_scores, weighted_aggregate


class TestSoftmaxScores:
    def test_output_sums_to_one(self):
        scores = [1.0, 2.0, 3.0]
        result = softmax_scores(scores)
        assert abs(sum(result) - 1.0) < 1e-9

    def test_higher_score_gets_higher_weight(self):
        scores = [1.0, 3.0, 2.0]
        result = softmax_scores(scores)
        assert result[1] > result[2] > result[0]

    def test_equal_scores_give_equal_weights(self):
        scores = [2.0, 2.0, 2.0]
        result = softmax_scores(scores)
        assert all(abs(w - 1/3) < 1e-9 for w in result)

    def test_single_element_returns_one(self):
        result = softmax_scores([5.0])
        assert abs(result[0] - 1.0) < 1e-9

    def test_all_zeros_gives_uniform(self):
        result = softmax_scores([0.0, 0.0, 0.0])
        assert all(abs(w - 1/3) < 1e-9 for w in result)

    def test_empty_returns_empty(self):
        assert softmax_scores([]) == []

    def test_numerical_stability_large_values(self):
        """Should not overflow with large scores."""
        scores = [1000.0, 1001.0, 1002.0]
        result = softmax_scores(scores)
        assert abs(sum(result) - 1.0) < 1e-9
        assert all(w > 0 for w in result)


class TestCosineSimilarity:
    def test_identical_vectors_return_one(self):
        v = [1.0, 2.0, 3.0]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors_return_zero(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(cosine_similarity(a, b)) < 1e-9

    def test_opposite_vectors_return_negative_one(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(cosine_similarity(a, b) + 1.0) < 1e-9

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        assert cosine_similarity(a, b) == 0.0

    def test_both_zero_vectors_return_zero(self):
        assert cosine_similarity([0.0], [0.0]) == 0.0

    def test_result_in_range_minus_one_to_one(self):
        import random
        random.seed(42)
        for _ in range(20):
            a = [random.gauss(0, 1) for _ in range(10)]
            b = [random.gauss(0, 1) for _ in range(10)]
            sim = cosine_similarity(a, b)
            assert -1.0 - 1e-9 <= sim <= 1.0 + 1e-9


class TestWeightedAggregate:
    def test_basic_weighted_sum(self):
        scores = [0.8, 0.6]
        weights = [0.7, 0.3]
        result = weighted_aggregate(scores, weights)
        expected = 0.8 * 0.7 + 0.6 * 0.3
        assert abs(result - expected) < 1e-9

    def test_single_score_returns_score_times_weight(self):
        assert abs(weighted_aggregate([0.5], [2.0]) - 1.0) < 1e-9

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError):
            weighted_aggregate([0.5, 0.6], [0.5])

    def test_zero_weights_returns_zero(self):
        assert weighted_aggregate([1.0, 2.0], [0.0, 0.0]) == 0.0

    def test_empty_inputs_returns_zero(self):
        assert weighted_aggregate([], []) == 0.0
