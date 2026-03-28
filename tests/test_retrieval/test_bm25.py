# tests/test_retrieval/test_bm25.py
import pytest
from depthfusion.retrieval.bm25 import BM25, tokenize


def test_tokenize_basic():
    tokens = tokenize("Hello World from Python")
    assert "hello" in tokens
    assert "world" in tokens
    assert "python" in tokens


def test_tokenize_removes_stopwords():
    tokens = tokenize("the quick brown fox")
    assert "the" not in tokens
    assert "quick" in tokens


def test_bm25_scores_relevant_doc_higher():
    corpus = [
        "VPS server IP address configuration",
        "cooking recipe pasta ingredients",
        "server deployment configuration guide",
    ]
    bm25 = BM25([tokenize(d) for d in corpus])
    scores = bm25.rank_all(tokenize("VPS server configuration"))
    top_idx = scores[0][0]
    assert top_idx in (0, 2)  # VPS or deployment doc should rank first


def test_bm25_zero_score_for_no_overlap():
    corpus = ["completely unrelated text about cooking"]
    bm25 = BM25([tokenize(d) for d in corpus])
    scores = bm25.rank_all(tokenize("VPS server deployment"))
    assert scores[0][1] == 0.0


def test_bm25_length_normalization():
    # Long doc with few relevant terms should score lower than short doc
    long_doc = "filler " * 500 + "VPS server"
    short_doc = "VPS server configuration"
    bm25 = BM25([tokenize(long_doc), tokenize(short_doc)])
    scores = bm25.rank_all(tokenize("VPS server"))
    assert scores[0][0] == 1  # short doc should rank higher


def test_bm25_rank_all_sorted_descending():
    corpus = ["python code review", "javascript code", "python testing"]
    bm25 = BM25([tokenize(d) for d in corpus])
    ranked = bm25.rank_all(tokenize("python"))
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)
