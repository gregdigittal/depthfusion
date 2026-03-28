"""Tests for session/scorer.py — SessionScorer."""
from depthfusion.core.types import SessionBlock
from depthfusion.session.scorer import SessionScorer


def make_block(session_id: str, block_index: int, content: str, tags: list[str]) -> SessionBlock:
    return SessionBlock(
        session_id=session_id,
        block_index=block_index,
        content=content,
        tags=tags,
    )


class TestSessionScorer:
    def test_higher_tag_overlap_gives_higher_score(self):
        scorer = SessionScorer()
        block_high = make_block("s1", 0, "Python debugging hooks", ["python", "debugging", "hooks"])
        block_low = make_block("s2", 0, "Unrelated stuff", ["unrelated"])
        task = "python debugging hooks"

        results = scorer.score_blocks([block_high, block_low], task)
        scores = {b.session_id: score for b, score in results}
        assert scores["s1"] > scores["s2"]

    def test_empty_task_all_zero_scores(self):
        scorer = SessionScorer()
        blocks = [
            make_block("s1", 0, "Some content", ["python"]),
            make_block("s2", 0, "Other content", ["hooks"]),
        ]
        results = scorer.score_blocks(blocks, "")
        for _, score in results:
            assert score == 0.0

    def test_sorting_descending(self):
        scorer = SessionScorer()
        blocks = [
            make_block("low", 0, "Irrelevant", ["food", "travel"]),
            make_block("high", 0, "Python scoring", ["python", "scoring", "depthfusion"]),
            make_block("mid", 0, "Some python", ["python"]),
        ]
        results = scorer.score_blocks(blocks, "python scoring depthfusion")
        scores = [score for _, score in results]
        assert scores == sorted(scores, reverse=True), "Results must be sorted descending by score"

    def test_empty_blocks_returns_empty(self):
        scorer = SessionScorer()
        results = scorer.score_blocks([], "some task")
        assert results == []

    def test_returns_all_blocks(self):
        scorer = SessionScorer()
        blocks = [make_block(f"s{i}", 0, "content", ["tag"]) for i in range(5)]
        results = scorer.score_blocks(blocks, "some task")
        assert len(results) == 5

    def test_exact_keyword_match_in_content_boosts_score(self):
        scorer = SessionScorer()
        block_match = make_block("match", 0, "This is about depthfusion scoring", [])
        block_no_match = make_block("nomatch", 0, "Random irrelevant text here", [])
        results = scorer.score_blocks([block_match, block_no_match], "depthfusion scoring")
        scores = {b.session_id: s for b, s in results}
        assert scores["match"] > scores["nomatch"]

    def test_result_is_list_of_tuples(self):
        scorer = SessionScorer()
        block = make_block("s1", 0, "content", ["tag"])
        results = scorer.score_blocks([block], "task")
        assert isinstance(results, list)
        assert len(results) == 1
        block_out, score = results[0]
        assert isinstance(block_out, SessionBlock)
        assert isinstance(score, float)
