"""Tests for router/dispatcher.py — QueryDispatcher."""
from depthfusion.core.config import DepthFusionConfig
from depthfusion.router.dispatcher import QueryDispatcher


class TestQueryDispatcher:
    def test_force_strategy_overrides_all_logic(self):
        config = DepthFusionConfig(rlm_enabled=False, fusion_enabled=False)
        dispatcher = QueryDispatcher(config=config)
        strategy = dispatcher.dispatch(
            query="anything",
            context_tokens=200_000,
            is_indexed=True,
            force_strategy="rrf",
        )
        assert strategy == "rrf"

    def test_force_strategy_passthrough(self):
        dispatcher = QueryDispatcher()
        strategy = dispatcher.dispatch(
            query="q",
            context_tokens=1000,
            is_indexed=False,
            force_strategy="passthrough",
        )
        assert strategy == "passthrough"

    def test_large_unindexed_routes_to_rlm(self):
        config = DepthFusionConfig(rlm_enabled=True)
        dispatcher = QueryDispatcher(config=config)
        strategy = dispatcher.dispatch(
            query="large query",
            context_tokens=200_000,  # > 150_000
            is_indexed=False,
        )
        assert strategy == "rlm"

    def test_indexed_content_routes_to_weighted_fusion(self):
        config = DepthFusionConfig(fusion_enabled=True)
        dispatcher = QueryDispatcher(config=config)
        strategy = dispatcher.dispatch(
            query="indexed query",
            context_tokens=10_000,
            is_indexed=True,
        )
        assert strategy == "weighted_fusion"

    def test_rlm_disabled_falls_back_to_rrf(self):
        config = DepthFusionConfig(rlm_enabled=False)
        dispatcher = QueryDispatcher(config=config)
        strategy = dispatcher.dispatch(
            query="large query",
            context_tokens=200_000,
            is_indexed=False,
        )
        assert strategy == "rrf", "When rlm disabled and would route to rlm, must fall back to rrf"

    def test_fusion_disabled_falls_back_to_rrf(self):
        config = DepthFusionConfig(fusion_enabled=False)
        dispatcher = QueryDispatcher(config=config)
        strategy = dispatcher.dispatch(
            query="indexed query",
            context_tokens=5_000,
            is_indexed=True,
        )
        assert strategy == "rrf", "When fusion disabled and would route to weighted_fusion, must fall back to rrf"

    def test_small_unindexed_routes_to_rrf(self):
        config = DepthFusionConfig(rlm_enabled=True, fusion_enabled=True)
        dispatcher = QueryDispatcher(config=config)
        strategy = dispatcher.dispatch(
            query="small query",
            context_tokens=50_000,  # < 150_000
            is_indexed=False,
        )
        assert strategy == "rrf"

    def test_exactly_at_token_threshold_not_rlm(self):
        """context_tokens == 150_000 is NOT > 150_000, so must NOT go to rlm."""
        config = DepthFusionConfig(rlm_enabled=True)
        dispatcher = QueryDispatcher(config=config)
        strategy = dispatcher.dispatch(
            query="boundary query",
            context_tokens=150_000,
            is_indexed=False,
        )
        assert strategy == "rrf", "At exactly 150_000 tokens (not >), must route to rrf"

    def test_above_threshold_unindexed_rlm_enabled(self):
        config = DepthFusionConfig(rlm_enabled=True)
        dispatcher = QueryDispatcher(config=config)
        strategy = dispatcher.dispatch(
            query="very large",
            context_tokens=150_001,  # just above threshold
            is_indexed=False,
        )
        assert strategy == "rlm"

    def test_no_config_uses_defaults(self):
        dispatcher = QueryDispatcher()
        # Default config has fusion_enabled=True
        strategy = dispatcher.dispatch(
            query="test",
            context_tokens=1000,
            is_indexed=True,
        )
        assert strategy == "weighted_fusion"

    def test_force_strategy_rlm_even_when_disabled(self):
        """force_strategy overrides even disabled strategies."""
        config = DepthFusionConfig(rlm_enabled=False)
        dispatcher = QueryDispatcher(config=config)
        strategy = dispatcher.dispatch(
            query="q",
            context_tokens=1000,
            is_indexed=False,
            force_strategy="rlm",
        )
        assert strategy == "rlm"
