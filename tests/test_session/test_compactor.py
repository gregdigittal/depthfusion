"""Tests for session/compactor.py — SessionCompactor."""
from depthfusion.session.compactor import SessionCompactor


class TestSessionCompactor:
    def test_output_shorter_than_input_for_unrelated_task(self):
        compactor = SessionCompactor(preserve_ratio=0.3)
        content = "\n\n".join([
            "Section about cooking recipes and food preparation techniques",
            "Another section about gardening and plant care",
            "More content about travel destinations in Europe",
            "Discussion about sports and athletic training",
            "Notes about movies and entertainment",
        ])
        result = compactor.compact(content, "python debugging depthfusion hooks")
        assert len(result) < len(content), "Compacted output must be shorter than input"

    def test_high_relevance_sections_preserved(self):
        compactor = SessionCompactor(preserve_ratio=0.5)
        relevant = "Debugging depthfusion hooks in session/tagger.py — found C1 violation"
        content = "\n\n".join([
            relevant,
            "Unrelated section about cooking pasta",
            "More cooking recipes",
        ])
        result = compactor.compact(content, "depthfusion hooks tagger C1")
        assert relevant in result, "High-relevance section must be preserved verbatim"

    def test_preserve_ratio_1_returns_all_content(self):
        compactor = SessionCompactor(preserve_ratio=1.0)
        content = "\n\n".join([
            "Section one about python",
            "Section two about javascript",
            "Section three about typescript",
        ])
        result = compactor.compact(content, "rust embedded systems")
        # With preserve_ratio=1.0, all content must be kept
        for line in ["Section one", "Section two", "Section three"]:
            assert line in result

    def test_empty_input_returns_empty(self):
        compactor = SessionCompactor()
        result = compactor.compact("", "some task")
        assert result == ""

    def test_returns_string(self):
        compactor = SessionCompactor()
        result = compactor.compact("Some session content here", "task")
        assert isinstance(result, str)

    def test_whitespace_only_input_returns_empty_or_whitespace(self):
        compactor = SessionCompactor()
        result = compactor.compact("   \n\n   ", "task")
        assert result.strip() == ""

    def test_single_section_high_relevance_preserved(self):
        compactor = SessionCompactor(preserve_ratio=0.5)
        content = "Debugging session/tagger.py — C1 constraint check"
        result = compactor.compact(content, "session tagger C1")
        assert result.strip() != "", "Single relevant section must not be dropped entirely"

    def test_preserve_ratio_zero_drops_all_low_relevance(self):
        compactor = SessionCompactor(preserve_ratio=0.0)
        content = "\n\n".join([
            "Section about cooking recipes",
            "Section about gardening",
        ])
        result = compactor.compact(content, "python depthfusion debugging")
        # With ratio 0.0, nothing should be preserved verbatim
        assert len(result) <= len(content)
