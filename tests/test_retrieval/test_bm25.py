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


# ---------------------------------------------------------------------------
# S-112 AC-3: field boost tests
# ---------------------------------------------------------------------------

def test_field_boost_applied_when_query_matches_field():
    """score_with_field_boost returns 1.2× the base score on a field hit."""
    corpus = ["VPS server configuration guide"]
    bm25 = BM25([tokenize(d) for d in corpus])
    base = bm25.score(tokenize("server"), 0)
    boosted = bm25.score_with_field_boost(tokenize("server"), 0, ["server", "config"])
    assert boosted == pytest.approx(base * 1.2)


def test_field_boost_not_applied_when_no_query_term_in_fields():
    """No boost when query terms are absent from field_tokens."""
    corpus = ["VPS server configuration guide"]
    bm25 = BM25([tokenize(d) for d in corpus])
    base = bm25.score(tokenize("server"), 0)
    no_boost = bm25.score_with_field_boost(tokenize("server"), 0, ["deployment", "linux"])
    assert no_boost == base


def test_field_boost_not_applied_when_field_tokens_empty():
    """Empty field_tokens → identical to base BM25 score."""
    corpus = ["python code review"]
    bm25 = BM25([tokenize(d) for d in corpus])
    base = bm25.score(tokenize("python"), 0)
    no_boost = bm25.score_with_field_boost(tokenize("python"), 0, [])
    assert no_boost == base


def test_field_boost_zero_base_stays_zero():
    """Zero BM25 score is not spuriously boosted."""
    corpus = ["completely unrelated cooking recipe"]
    bm25 = BM25([tokenize(d) for d in corpus])
    boosted = bm25.score_with_field_boost(tokenize("server"), 0, ["server"])
    assert boosted == 0.0


def test_rank_with_field_boost_boosts_correct_doc():
    """rank_with_field_boost reorders results when field hit is on the lower-ranked doc."""
    corpus = [
        "server configuration management",   # doc 0: moderate base score
        "server deployment tutorial",          # doc 1: similar base score
    ]
    bm25 = BM25([tokenize(d) for d in corpus])
    query = tokenize("server")

    # Without boost, get baseline order
    base_ranked = bm25.rank_all(query)
    base_top = base_ranked[0][0]

    # Give doc 1 a facts hit on the query term "server"
    field_tokens = [[], ["server"]]
    boosted_ranked = bm25.rank_with_field_boost(query, field_tokens)
    boosted_top = boosted_ranked[0][0]

    # Doc 1 should either win or at least be boosted (its score > base score)
    doc1_base = next(s for i, s in base_ranked if i == 1)
    doc1_boosted = next(s for i, s in boosted_ranked if i == 1)
    assert doc1_boosted == pytest.approx(doc1_base * 1.2)
    # Boosted top should be doc 1 (which now has the field advantage)
    assert boosted_top == 1


def test_rank_with_field_boost_sorted_descending():
    """rank_with_field_boost always returns results sorted descending."""
    corpus = ["alpha beta", "beta gamma", "gamma delta"]
    bm25 = BM25([tokenize(d) for d in corpus])
    ranked = bm25.rank_with_field_boost(tokenize("beta"), [[], ["beta"], []])
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_with_field_boost_short_field_list():
    """Docs beyond len(field_tokens_per_doc) receive no boost gracefully."""
    corpus = ["server alpha", "server beta", "server gamma"]
    bm25 = BM25([tokenize(d) for d in corpus])
    # Only supply field_tokens for first two docs; third gets no boost
    ranked = bm25.rank_with_field_boost(tokenize("server"), [["server"], []])
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)
