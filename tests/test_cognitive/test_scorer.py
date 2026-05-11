import pytest

from depthfusion.cognitive.scorer import CognitiveScorer, ScoringContext


def test_scorer_weights_sum_to_one():
    s = CognitiveScorer()
    assert abs(sum(s.weights.values()) - 1.0) < 1e-9


def test_scorer_all_components_max_gives_one():
    s = CognitiveScorer()
    ctx = ScoringContext(
        semantic=1.0, lexical=1.0, confidence=1.0,
        regime_match=1.0, graph_proximity=1.0,
        recency=1.0, historical_usefulness=1.0, workflow_intent=1.0,
    )
    assert abs(s.score(ctx) - 1.0) < 1e-9


def test_scorer_all_components_zero_gives_zero():
    s = CognitiveScorer()
    ctx = ScoringContext(
        semantic=0.0, lexical=0.0, confidence=0.0,
        regime_match=0.0, graph_proximity=0.0,
        recency=0.0, historical_usefulness=0.0, workflow_intent=0.0,
    )
    assert s.score(ctx) == 0.0


def test_scorer_semantic_dominates():
    s = CognitiveScorer()
    ctx_high = ScoringContext(
        semantic=1.0, lexical=0.0, confidence=0.5,
        regime_match=0.0, graph_proximity=0.0,
        recency=0.0, historical_usefulness=0.0, workflow_intent=0.0,
    )
    ctx_low = ScoringContext(
        semantic=0.0, lexical=1.0, confidence=0.5,
        regime_match=0.0, graph_proximity=0.0,
        recency=0.0, historical_usefulness=0.0, workflow_intent=0.0,
    )
    assert s.score(ctx_high) > s.score(ctx_low)


def test_scorer_breakdown():
    s = CognitiveScorer()
    ctx = ScoringContext(
        semantic=0.8, lexical=0.6, confidence=0.9,
        regime_match=1.0, graph_proximity=0.5,
        recency=0.7, historical_usefulness=0.4, workflow_intent=0.3,
    )
    score, breakdown = s.score_with_breakdown(ctx)
    assert 0.0 <= score <= 1.0
    assert "semantic" in breakdown
    assert abs(breakdown["semantic"] - 0.8 * 0.25) < 1e-9
