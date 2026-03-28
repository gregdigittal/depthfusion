"""Tests for recursive strategies."""
from __future__ import annotations

import pytest

from depthfusion.recursive.strategies import STRATEGIES, get_strategy, recommend_strategy


def test_all_four_strategies_exist():
    for name in ("peek", "grep", "partition_map", "summarize"):
        strategy = get_strategy(name)
        assert "description" in strategy
        assert "max_tokens" in strategy


def test_get_strategy_peek():
    s = get_strategy("peek")
    assert s["max_tokens"] == 2000


def test_get_strategy_grep():
    s = get_strategy("grep")
    assert s["max_tokens"] == 5000


def test_get_strategy_partition_map():
    s = get_strategy("partition_map")
    assert s["max_tokens"] == 10000


def test_get_strategy_summarize():
    s = get_strategy("summarize")
    assert s["max_tokens"] == 20000


def test_get_strategy_unknown_raises_key_error():
    with pytest.raises(KeyError):
        get_strategy("nonexistent_strategy")


def test_recommend_strategy_peek_at_boundary():
    assert recommend_strategy(2000) == "peek"


def test_recommend_strategy_peek_below_boundary():
    assert recommend_strategy(100) == "peek"
    assert recommend_strategy(1999) == "peek"


def test_recommend_strategy_grep():
    assert recommend_strategy(2001) == "grep"
    assert recommend_strategy(5000) == "grep"


def test_recommend_strategy_partition_map():
    assert recommend_strategy(5001) == "partition_map"
    assert recommend_strategy(20000) == "partition_map"


def test_recommend_strategy_summarize():
    assert recommend_strategy(20001) == "summarize"
    assert recommend_strategy(100000) == "summarize"


def test_strategies_dict_has_four_entries():
    assert len(STRATEGIES) == 4
