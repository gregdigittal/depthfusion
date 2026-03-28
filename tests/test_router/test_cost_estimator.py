"""Tests for router/cost_estimator.py — CostEstimator."""
import pytest

from depthfusion.router.cost_estimator import PRICING, CostEstimator


class TestCostEstimator:
    def test_estimate_tokens_is_len_over_4(self):
        estimator = CostEstimator()
        text = "a" * 400
        assert estimator.estimate_tokens(text) == 100

    def test_estimate_tokens_empty_string(self):
        estimator = CostEstimator()
        assert estimator.estimate_tokens("") == 0

    def test_estimate_tokens_short_text(self):
        estimator = CostEstimator()
        text = "abcd"
        assert estimator.estimate_tokens(text) == 1

    def test_estimate_cost_haiku(self):
        estimator = CostEstimator()
        # 4000 chars → 1000 tokens → 1K tokens
        text = "x" * 4000
        cost = estimator.estimate_cost(text, model="haiku")
        expected = PRICING["haiku"]["input"] * 1.0  # 1K tokens
        assert abs(cost - expected) < 1e-9

    def test_estimate_cost_sonnet(self):
        estimator = CostEstimator()
        text = "x" * 4000  # 1000 tokens = 1K
        cost = estimator.estimate_cost(text, model="sonnet")
        expected = PRICING["sonnet"]["input"] * 1.0
        assert abs(cost - expected) < 1e-9

    def test_estimate_cost_opus(self):
        estimator = CostEstimator()
        text = "x" * 4000
        cost = estimator.estimate_cost(text, model="opus")
        expected = PRICING["opus"]["input"] * 1.0
        assert abs(cost - expected) < 1e-9

    def test_estimate_cost_default_model_is_haiku(self):
        estimator = CostEstimator()
        text = "x" * 4000
        cost_default = estimator.estimate_cost(text)
        cost_haiku = estimator.estimate_cost(text, model="haiku")
        assert cost_default == cost_haiku

    def test_estimate_cost_unknown_model_raises(self):
        estimator = CostEstimator()
        with pytest.raises((KeyError, ValueError)):
            estimator.estimate_cost("text", model="unknown-model")

    def test_exceeds_ceiling_true_when_over(self):
        estimator = CostEstimator()
        text = "x" * 400_000  # 100K tokens → haiku input cost = 0.025
        # haiku: 0.00025 per 1K → 100K tokens = 0.025
        assert estimator.exceeds_ceiling(text, model="opus", ceiling=0.001) is True

    def test_exceeds_ceiling_false_when_under(self):
        estimator = CostEstimator()
        text = "x" * 40  # 10 tokens → tiny cost
        assert estimator.exceeds_ceiling(text, model="haiku", ceiling=1.00) is False

    def test_exceeds_ceiling_equal_to_ceiling_is_not_exceeding(self):
        estimator = CostEstimator()
        # 4000 chars → 1000 tokens → haiku input: 0.00025
        text = "x" * 4000
        cost = estimator.estimate_cost(text, model="haiku")
        # at exactly the ceiling it should NOT exceed
        assert estimator.exceeds_ceiling(text, model="haiku", ceiling=cost) is False

    def test_pricing_dict_has_required_models(self):
        assert "haiku" in PRICING
        assert "sonnet" in PRICING
        assert "opus" in PRICING
        for model, rates in PRICING.items():
            assert "input" in rates
            assert "output" in rates
