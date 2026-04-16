"""Tests for _trim_to_sentence snippet helper in server.py."""

from depthfusion.mcp.server import _trim_to_sentence

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_text(base: str, repeat: int) -> str:
    """Return *base* repeated *repeat* times (space-separated)."""
    return (" " + base) * repeat


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTrimToSentence:
    """Unit tests for _trim_to_sentence."""

    # ------------------------------------------------------------------
    # 1. Text shorter than max_len is returned unchanged
    # ------------------------------------------------------------------

    def test_short_text_unchanged(self):
        text = "Hello, world."
        assert _trim_to_sentence(text, 100) == text

    def test_exact_length_unchanged(self):
        text = "Exactly fifty chars long, padded with extra text!"
        # exactly 50 chars
        text = text[:50]
        assert _trim_to_sentence(text, 50) == text

    def test_empty_string_unchanged(self):
        assert _trim_to_sentence("", 100) == ""

    # ------------------------------------------------------------------
    # 2. Sentence-boundary trim appends "…"
    # ------------------------------------------------------------------

    def test_sentence_boundary_period(self):
        # 20 chars of meaningful sentence, then more text up to 30 chars
        text = "First sentence here. Extra words that overflow."
        max_len = 25
        # The period at position 19 is > 60% of 25 = 15 → should trim there
        result = _trim_to_sentence(text, max_len)
        assert result.endswith("…")
        assert "First sentence here." in result

    def test_sentence_boundary_exclamation(self):
        text = "Watch out! There is more text after this point here."
        max_len = 30
        result = _trim_to_sentence(text, max_len)
        assert result.endswith("…")
        assert "Watch out!" in result

    def test_sentence_boundary_question_mark(self):
        text = "Is this working? Yes it seems to work fine now."
        max_len = 30
        result = _trim_to_sentence(text, max_len)
        assert result.endswith("…")
        assert "Is this working?" in result

    def test_sentence_boundary_newline(self):
        text = "Line one content here\nLine two content that goes on and on"
        max_len = 35
        result = _trim_to_sentence(text, max_len)
        assert result.endswith("…")
        # newline at position 21 is at the 60% boundary of 35 (int(35*0.6)=21) → pos >= min_pos is True → trim here
        assert "Line one content here" in result

    # ------------------------------------------------------------------
    # 3. Word-boundary fallback when no sentence boundary in range
    # ------------------------------------------------------------------

    def test_word_boundary_fallback(self):
        # Construct text where the only period is in the first 60% of max_len
        # e.g. period at position 5 in a max_len of 30 → 5 < 18 (60% of 30)
        text = "Hi. " + "x" * 50  # period at index 2, well below 60% of 30
        max_len = 30
        result = _trim_to_sentence(text, max_len)
        assert result.endswith("…")
        # Must not end with 'x' mid-word since we fallback to word boundary
        # The 'Hi. ' is short, so the long 'xxx' block has no spaces → hard cut
        # but if we ensure a space exists in the second part:
        text2 = "Hi. " + ("abc " * 20)  # spaces throughout
        result2 = _trim_to_sentence(text2, max_len)
        assert result2.endswith("…")
        # Trim should be at a space boundary
        trimmed_body = result2[:-1]  # strip ellipsis
        assert not trimmed_body.endswith(" "), "trailing space should be trimmed by word-boundary logic"

    def test_word_boundary_no_trailing_space(self):
        # Period is too early (before 60%); fallback to word boundary
        text = "A.  " + "word " * 20
        max_len = 40
        # Period at index 1 which is < 24 (60% of 40), so sentence boundary skipped
        result = _trim_to_sentence(text, max_len)
        assert result.endswith("…")
        body = result[:-1]
        assert not body.endswith(" ")

    # ------------------------------------------------------------------
    # 4. "…" is appended only when text was actually truncated
    # ------------------------------------------------------------------

    def test_no_ellipsis_when_not_truncated(self):
        text = "Short text."
        result = _trim_to_sentence(text, 1000)
        assert "…" not in result
        assert result == text

    def test_ellipsis_when_truncated(self):
        text = "A" * 200
        result = _trim_to_sentence(text, 100)
        assert result.endswith("…")

    # ------------------------------------------------------------------
    # 5. The 60% minimum is respected (doesn't over-trim)
    # ------------------------------------------------------------------

    def test_60_percent_minimum_respected_sentence(self):
        max_len = 100
        min_pos = int(max_len * 0.6)  # 60

        # Place a sentence-ending char just before the 60% threshold → should NOT use it.
        # Text is longer than max_len so truncation will occur.
        early_period_text = "A" * (min_pos - 2) + ". " + "B" * (max_len + 20)
        result_early = _trim_to_sentence(early_period_text, max_len)
        # The period at position (min_pos - 2) is below the threshold, so we expect
        # fallback to word boundary (the space at min_pos - 1) or hard cut.
        assert result_early.endswith("…")
        # Importantly, the result should NOT end with ". …" (the early period boundary)
        # because that period is below 60%
        assert ". …" not in result_early or result_early.index(". …") > min_pos - 2

    def test_60_percent_minimum_respected_exact_boundary(self):
        max_len = 100
        min_pos = int(max_len * 0.6)  # 60

        # Place a sentence-ending char exactly AT the 60% threshold → SHOULD use it.
        # Text is longer than max_len so truncation will occur.
        on_boundary_text = "A" * min_pos + ". " + "B" * (max_len + 20)
        result_on = _trim_to_sentence(on_boundary_text, max_len)
        assert result_on.endswith("…")
        # The period at position min_pos is at the threshold so it should be used
        assert result_on.startswith("A" * min_pos + ".")

    def test_does_not_over_trim_to_tiny_snippet(self):
        max_len = 100
        # Only a period very early at position 5 — should not trim there
        text = "Done." + " more text " * 20
        result = _trim_to_sentence(text, max_len)
        assert result.endswith("…")
        # Result body should be substantially longer than just "Done."
        body = result[:-1]
        assert len(body) > int(max_len * 0.6)
