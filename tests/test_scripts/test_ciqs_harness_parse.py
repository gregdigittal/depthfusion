"""Tests for scripts/ciqs_harness.py parse_scoring_template.

Covers: section boundaries, score validation (int / range), missing
dims, out-of-order sections, and noise lines inside a section.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture(scope="module")
def harness():
    path = SCRIPTS_DIR / "ciqs_harness.py"
    spec = importlib.util.spec_from_file_location("ciqs_harness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["ciqs_harness"] = module
    spec.loader.exec_module(module)
    return module


class TestParseScoringTemplate:
    def test_parses_simple_template(self, harness):
        text = (
            "## A / A1 - Retrieval Quality\n"
            "\n"
            "some notes\n"
            "\n"
            "- relevance: `score: 7`\n"
            "- specificity: `score: 8`\n"
        )
        out = harness.parse_scoring_template(text)
        assert out == {"A1": {"relevance": 7, "specificity": 8}}

    def test_multiple_sections(self, harness):
        text = (
            "## A / A1 - Retrieval Quality\n"
            "- relevance: `score: 5`\n"
            "\n"
            "## B / B2 - Code Quality\n"
            "- issue_detection: `score: 9`\n"
            "- fix_quality: `score: 6`\n"
        )
        out = harness.parse_scoring_template(text)
        assert out["A1"] == {"relevance": 5}
        assert out["B2"] == {"issue_detection": 9, "fix_quality": 6}

    def test_empty_section_skipped(self, harness):
        # A section with no scores shouldn't appear in the output
        text = (
            "## A / A1 - Retrieval Quality\n"
            "(no scores filled in)\n"
            "## B / B1 - Code Quality\n"
            "- issue_detection: `score: 7`\n"
        )
        out = harness.parse_scoring_template(text)
        assert "A1" not in out
        assert out["B1"] == {"issue_detection": 7}

    def test_zero_and_ten_boundaries(self, harness):
        text = (
            "## D / D1 - Session Continuity\n"
            "- factual_accuracy: `score: 0`\n"
            "- specificity: `score: 10`\n"
        )
        out = harness.parse_scoring_template(text)
        assert out["D1"] == {"factual_accuracy": 0, "specificity": 10}

    def test_rejects_out_of_range(self, harness):
        text = (
            "## A / A1 - Retrieval Quality\n"
            "- relevance: `score: 11`\n"
        )
        with pytest.raises(ValueError, match="out of 0-10 range"):
            harness.parse_scoring_template(text)

    def test_rejects_non_integer(self, harness):
        # Regex requires \d+ so "seven" just won't match at all -
        # which means the dim is silently omitted, not an error.
        # This test documents that current behaviour.
        text = (
            "## A / A1 - Retrieval Quality\n"
            "- relevance: `score: seven`\n"
            "- specificity: `score: 5`\n"
        )
        out = harness.parse_scoring_template(text)
        assert out == {"A1": {"specificity": 5}}

    def test_ignores_unrelated_markdown(self, harness):
        text = (
            "# CIQS Scoring Template - local / run 1\n"
            "\n"
            "Fill in an integer score (0-10) in each `score: ` line.\n"
            "\n"
            "---\n"
            "\n"
            "## A / A1 - Retrieval Quality\n"
            "\n"
            "**Prompt:**\n"
            "```\n"
            "some prompt text\n"
            "```\n"
            "\n"
            "**Scores (0-10 each):**\n"
            "\n"
            "- relevance: `score: 6`\n"
            "- specificity: `score: 7`\n"
            "- confidence_calibration: `score: 5`\n"
            "- novel_signal: `score: 4`\n"
            "\n"
            "**Notes:** good response\n"
            "\n"
            "---\n"
        )
        out = harness.parse_scoring_template(text)
        assert out == {
            "A1": {
                "relevance": 6,
                "specificity": 7,
                "confidence_calibration": 5,
                "novel_signal": 4,
            }
        }

    def test_duplicate_dim_keeps_last(self, harness):
        # If the operator fills in a dim twice, the regex finds both;
        # our dict comprehension keeps the last. Document this.
        text = (
            "## A / A1 - Retrieval Quality\n"
            "- relevance: `score: 3`\n"
            "- relevance: `score: 8`\n"
        )
        out = harness.parse_scoring_template(text)
        assert out["A1"]["relevance"] == 8

    def test_four_category_topics_parsed(self, harness):
        # Category D has 4 topics (D1..D4); verify multi-section parse
        # handles the full battery shape
        text = (
            "## D / D1 - Session Continuity\n- factual_accuracy: `score: 5`\n"
            "## D / D2 - Session Continuity\n- factual_accuracy: `score: 6`\n"
            "## D / D3 - Session Continuity\n- factual_accuracy: `score: 7`\n"
            "## D / D4 - Session Continuity\n- factual_accuracy: `score: 8`\n"
        )
        out = harness.parse_scoring_template(text)
        assert set(out.keys()) == {"D1", "D2", "D3", "D4"}

    def test_section_header_accepts_future_categories(self, harness):
        # Regression for High #2: hardcoded [A-E] silently skipped future
        # categories. Now we accept any [A-Z] letter.
        text = (
            "## F / F1 - Future Category\n- dim1: `score: 5`\n"
        )
        out = harness.parse_scoring_template(text)
        assert out == {"F1": {"dim1": 5}}

    def test_score_regex_false_match_inside_prompt_block(self, harness):
        # Known-risk: `_SCORE_LINE` is not code-block-aware. If a prompt
        # text inside triple-backticks happens to contain a line matching
        # the score line format, it will be attributed to the section
        # the code block is in. This test documents that failure mode
        # using in-range values so the range check doesn't mask it.
        text = (
            "## A / A1 - Retrieval Quality\n"
            "```\n"
            "- relevance: `score: 9`\n"   # inside code block (not real)
            "```\n"
            "- relevance: `score: 7`\n"   # the real score
        )
        out = harness.parse_scoring_template(text)
        # Both get matched; dict keeps last. If this ever becomes a
        # real pain point (e.g. prompts routinely contain rubric-like
        # lines), make the regex code-block-aware.
        assert "relevance" in out["A1"]
        assert out["A1"]["relevance"] == 7  # last wins in dict


class TestDeriveScoredPath:
    def test_normal_path(self, harness):
        from pathlib import Path
        raw = Path("docs/benchmarks/2026-04-21-local-run1-raw.jsonl")
        scored = harness._derive_scored_path(raw)
        assert scored.name == "2026-04-21-local-run1-scored.jsonl"
        assert scored.parent == raw.parent

    def test_rejects_missing_suffix(self, harness):
        from pathlib import Path
        # Regression for High #1: bare replace() would silently produce
        # a path identical to the input.
        raw = Path("benchmarks/something-else.jsonl")
        with pytest.raises(ValueError, match="-raw.jsonl"):
            harness._derive_scored_path(raw)

    def test_rejects_path_without_raw_in_stem(self, harness):
        from pathlib import Path
        # A directory named "-raw" shouldn't trick the derivation —
        # only the STEM is examined, not the whole path string.
        raw = Path("some/-raw/folder/myfile.jsonl")
        with pytest.raises(ValueError, match="-raw.jsonl"):
            harness._derive_scored_path(raw)

    def test_preserves_different_directories(self, harness):
        from pathlib import Path
        raw = Path("/tmp/custom/x-raw.jsonl")
        scored = harness._derive_scored_path(raw)
        assert scored == Path("/tmp/custom/x-scored.jsonl")
