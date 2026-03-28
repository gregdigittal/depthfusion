"""Recursive LLM strategies — selection and recommendation."""
from __future__ import annotations

STRATEGIES: dict[str, dict] = {
    "peek": {
        "description": "Read first 2000 tokens of content",
        "max_tokens": 2000,
    },
    "grep": {
        "description": "Search content for query-relevant sections",
        "max_tokens": 5000,
    },
    "partition_map": {
        "description": "Partition content into chunks, summarize each, combine",
        "max_tokens": 10000,
    },
    "summarize": {
        "description": "Produce a dense summary of the full content",
        "max_tokens": 20000,
    },
}


def get_strategy(name: str) -> dict:
    """Get strategy config by name. Raises KeyError for unknown strategies."""
    if name not in STRATEGIES:
        raise KeyError(f"Unknown strategy: {name!r}. Valid: {list(STRATEGIES)}")
    return STRATEGIES[name]


def recommend_strategy(content_tokens: int) -> str:
    """Recommend a strategy based on content size.

    - <=2000: peek
    - <=5000: grep
    - <=20000: partition_map
    - >20000: summarize
    """
    if content_tokens <= 2000:
        return "peek"
    if content_tokens <= 5000:
        return "grep"
    if content_tokens <= 20000:
        return "partition_map"
    return "summarize"
