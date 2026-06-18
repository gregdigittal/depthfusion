"""Tests for depthfusion.parsers.deepseek.DeepSeekParser."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from depthfusion.parsers.deepseek import DeepSeekParser

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def parser() -> DeepSeekParser:
    return DeepSeekParser()


@pytest.fixture()
def sample_data() -> str:
    return (FIXTURES / "deepseek-sample.json").read_text()


class TestDeepSeekParserHappyPath:
    def test_wrapped_format_extracts_messages(self, parser: DeepSeekParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        assert len(messages) >= 2

    def test_flat_array_format(self, parser: DeepSeekParser) -> None:
        data = json.dumps([
            {"role": "user", "content": "What is 2 + 2?"},
            {"role": "assistant", "content": "2 + 2 equals 4."},
        ])
        messages = parser.parse(data)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

    def test_only_user_and_assistant_roles(self, parser: DeepSeekParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        for msg in messages:
            assert msg.role in ("user", "assistant")

    def test_system_messages_are_skipped(self, parser: DeepSeekParser) -> None:
        data = json.dumps([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ])
        messages = parser.parse(data)
        assert len(messages) == 2
        assert all(m.role != "system" for m in messages)

    def test_timestamps_are_empty_strings(self, parser: DeepSeekParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        for msg in messages:
            assert msg.timestamp == ""

    def test_content_is_non_empty(self, parser: DeepSeekParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        for msg in messages:
            assert msg.content.strip()

    def test_sample_order_is_preserved(self, parser: DeepSeekParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        roles = [m.role for m in messages]
        assert roles[0] == "user"
        assert roles[1] == "assistant"


class TestDeepSeekParserEdgeCases:
    def test_malformed_json_returns_empty_list(self, parser: DeepSeekParser) -> None:
        assert parser.parse("{bad json") == []

    def test_empty_string_returns_empty_list(self, parser: DeepSeekParser) -> None:
        assert parser.parse("") == []

    def test_whitespace_only_returns_empty_list(self, parser: DeepSeekParser) -> None:
        assert parser.parse("  ") == []

    def test_empty_content_messages_are_skipped(self, parser: DeepSeekParser) -> None:
        data = json.dumps([
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "Sure!"},
        ])
        messages = parser.parse(data)
        assert len(messages) == 1
        assert messages[0].role == "assistant"
