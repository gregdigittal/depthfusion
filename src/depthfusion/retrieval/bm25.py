"""BM25 retrieval — extracted from mcp/server.py for standalone use."""
from __future__ import annotations

import math
import re

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "is", "was", "are", "were", "be", "been", "have",
    "has", "do", "does", "did", "will", "would", "could", "should", "this",
    "that", "it", "not", "so", "if", "as", "up", "out", "just", "also",
})


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase word tokens, removing stopwords."""
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]{1,}\b", text.lower())
    return [w for w in words if w not in _STOPWORDS]


class BM25:
    """BM25 scorer for small corpora. No external dependencies.

    k1=1.5 (term saturation), b=0.75 (length normalization).
    IDF uses the smoothed Robertson formula to prevent negative scores.
    """

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.N = len(corpus_tokens)
        self.avgdl = sum(len(d) for d in corpus_tokens) / max(self.N, 1)
        self._tf: list[dict[str, int]] = []
        self._df: dict[str, int] = {}
        for doc in corpus_tokens:
            tf: dict[str, int] = {}
            for term in doc:
                tf[term] = tf.get(term, 0) + 1
            self._tf.append(tf)
            for term in set(doc):
                self._df[term] = self._df.get(term, 0) + 1
        self._dl = [len(d) for d in corpus_tokens]

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log((self.N - df + 0.5) / (df + 0.5) + 1)

    def score(self, query_terms: list[str], doc_idx: int) -> float:
        tf = self._tf[doc_idx]
        dl = self._dl[doc_idx]
        result = 0.0
        for term in query_terms:
            f = tf.get(term, 0)
            if not f:
                continue
            idf = self._idf(term)
            numerator = f * (self.k1 + 1)
            denominator = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            result += idf * (numerator / denominator)
        return result

    def rank_all(self, query_terms: list[str]) -> list[tuple[int, float]]:
        """Return (doc_idx, bm25_score) sorted descending."""
        scores = [(i, self.score(query_terms, i)) for i in range(self.N)]
        return sorted(scores, key=lambda x: -x[1])
