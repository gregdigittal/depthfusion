"""Session scorer — scores SessionBlocks by relevance to a task description."""
from __future__ import annotations

import re

from depthfusion.core.types import SessionBlock

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "is", "was", "are", "were", "be", "been", "have",
    "has", "do", "does", "did", "will", "would", "could", "should", "this",
    "that", "it", "not", "so", "if", "as", "up", "out", "just", "also",
})


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]{1,}\b", text.lower())
    return {w for w in words if w not in _STOPWORDS}


class SessionScorer:
    """Scores SessionBlocks by tag overlap + keyword matching against a task description."""

    def score_blocks(
        self,
        blocks: list[SessionBlock],
        task_description: str,
    ) -> list[tuple[SessionBlock, float]]:
        """Score blocks by tag overlap + keyword matching vs task_description.

        Returns sorted (block, score) tuples descending by score.
        """
        if not task_description.strip():
            return [(block, 0.0) for block in blocks]

        task_tokens = _tokenize(task_description)
        task_tags = set(task_description.lower().split())

        scored: list[tuple[SessionBlock, float]] = []
        for block in blocks:
            score = self._score_single(block, task_tokens, task_tags)
            scored.append((block, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _score_single(
        self,
        block: SessionBlock,
        task_tokens: set[str],
        task_tags: set[str],
    ) -> float:
        score = 0.0

        # Tag overlap component (weight: 0.6)
        if block.tags:
            block_tags = {t.lower() for t in block.tags}
            overlap = len(block_tags & task_tags)
            tag_score = overlap / max(len(block_tags), 1)
            score += 0.6 * tag_score

        # Keyword/content overlap component (weight: 0.4)
        content_tokens = _tokenize(block.content)
        if content_tokens and task_tokens:
            overlap = len(content_tokens & task_tokens)
            content_score = overlap / max(len(task_tokens), 1)
            score += 0.4 * content_score

        return round(score, 6)
