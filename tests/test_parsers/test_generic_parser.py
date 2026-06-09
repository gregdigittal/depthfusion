"""Tests for depthfusion.parsers.generic.GenericParser."""
from __future__ import annotations

import json

import pytest

from depthfusion.parsers.generic import GenericParser


@pytest.fixture()
def parser() -> GenericParser:
    return GenericParser()


class TestGenericParserJsonArray:
    def test_json_array_with_role_content(self, parser: GenericParser) -> None:
        data = json.dumps([
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ])
        messages = parser.parse(data)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

    def test_json_array_preserves_content(self, parser: GenericParser) -> None:
        data = json.dumps([{"role": "user", "content": "Tell me something interesting."}])
        messages = parser.parse(data)
        assert messages[0].content == "Tell me something interesting."

    def test_json_array_timestamps_are_empty(self, parser: GenericParser) -> None:
        data = json.dumps([{"role": "assistant", "content": "Sure!"}])
        messages = parser.parse(data)
        assert messages[0].timestamp == ""

    def test_unknown_role_defaults_to_assistant(self, parser: GenericParser) -> None:
        data = json.dumps([{"role": "oracle", "content": "The answer is 42."}])
        messages = parser.parse(data)
        assert len(messages) == 1
        assert messages[0].role == "assistant"


class TestGenericParserPrefixText:
    def test_human_prefix(self, parser: GenericParser) -> None:
        text = "Human: What time is it?\nAssistant: It's noon."
        messages = parser.parse(text)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

    def test_user_prefix(self, parser: GenericParser) -> None:
        text = "User: Can you help me?\nAI: Of course!"
        messages = parser.parse(text)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

    def test_multiline_message(self, parser: GenericParser) -> None:
        text = "Human: First line.\nSecond line.\nAssistant: Got it."
        messages = parser.parse(text)
        assert len(messages) == 2
        assert "First line." in messages[0].content
        assert "Second line." in messages[0].content

    def test_prefix_is_case_insensitive(self, parser: GenericParser) -> None:
        text = "HUMAN: Hello\nassistant: World"
        messages = parser.parse(text)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

    def test_bot_prefix_maps_to_assistant(self, parser: GenericParser) -> None:
        text = "User: Hi\nBot: Hello back"
        messages = parser.parse(text)
        assert messages[1].role == "assistant"


class TestGenericParserFallback:
    def test_unknown_input_wraps_as_assistant(self, parser: GenericParser) -> None:
        text = "This is just some random text with no prefixes."
        messages = parser.parse(text)
        assert len(messages) == 1
        assert messages[0].role == "assistant"
        assert messages[0].content == text.strip()

    def test_empty_string_returns_empty_list(self, parser: GenericParser) -> None:
        assert parser.parse("") == []

    def test_whitespace_only_returns_empty_list(self, parser: GenericParser) -> None:
        assert parser.parse("   \n  ") == []

    def test_fallback_timestamp_is_empty(self, parser: GenericParser) -> None:
        messages = parser.parse("No structure here.")
        assert messages[0].timestamp == ""
