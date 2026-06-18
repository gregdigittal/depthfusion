"""Tests for depthfusion.parsers.gemini.GeminiParser."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from depthfusion.parsers.gemini import GeminiParser

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def parser() -> GeminiParser:
    return GeminiParser()


@pytest.fixture()
def sample_data() -> str:
    return (FIXTURES / "gemini-sample.json").read_text()


class TestGeminiParserHappyPath:
    def test_flat_array_format(self, parser: GeminiParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        assert len(messages) >= 2

    def test_wrapped_format(self, parser: GeminiParser) -> None:
        data = json.dumps({
            "conversations": [
                {"human": "Hello there", "model": "Hi! How can I help?"},
                {"human": "Tell me a joke", "model": "Why did the chicken cross the road?"},
            ]
        })
        messages = parser.parse(data)
        assert len(messages) == 4
        roles = [m.role for m in messages]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_human_maps_to_user(self, parser: GeminiParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        user_msgs = [m for m in messages if m.role == "user"]
        assert len(user_msgs) >= 1

    def test_model_maps_to_assistant(self, parser: GeminiParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        assert len(assistant_msgs) >= 1

    def test_timestamps_are_empty_strings(self, parser: GeminiParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        for msg in messages:
            assert msg.timestamp == ""

    def test_content_is_non_empty(self, parser: GeminiParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        for msg in messages:
            assert msg.content.strip()

    def test_alternating_roles_in_sample(self, parser: GeminiParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        roles = [m.role for m in messages]
        # Sample has 3 turns → 6 messages alternating user/assistant
        assert roles[0] == "user"
        assert roles[1] == "assistant"


class TestGeminiParserEdgeCases:
    def test_malformed_json_returns_empty_list(self, parser: GeminiParser) -> None:
        assert parser.parse("{not valid}") == []

    def test_empty_string_returns_empty_list(self, parser: GeminiParser) -> None:
        assert parser.parse("") == []

    def test_whitespace_only_returns_empty_list(self, parser: GeminiParser) -> None:
        assert parser.parse("  \n  ") == []

    def test_missing_human_field_emits_only_model(self, parser: GeminiParser) -> None:
        data = json.dumps([{"model": "Just an answer, no question"}])
        messages = parser.parse(data)
        assert len(messages) == 1
        assert messages[0].role == "assistant"

    def test_empty_turns_are_skipped(self, parser: GeminiParser) -> None:
        data = json.dumps([{"human": "", "model": ""}])
        messages = parser.parse(data)
        assert messages == []
