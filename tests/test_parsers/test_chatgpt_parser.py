"""Tests for depthfusion.parsers.chatgpt.ChatGPTParser."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from depthfusion.parsers.chatgpt import ChatGPTParser

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def parser() -> ChatGPTParser:
    return ChatGPTParser()


@pytest.fixture()
def sample_data() -> str:
    return (FIXTURES / "chatgpt-sample.json").read_text()


class TestChatGPTParserHappyPath:
    def test_returns_list_of_messages(self, parser: ChatGPTParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        assert isinstance(messages, list)
        assert len(messages) >= 2

    def test_only_user_and_assistant_roles(self, parser: ChatGPTParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        for msg in messages:
            assert msg.role in ("user", "assistant"), f"Unexpected role: {msg.role}"

    def test_tool_messages_are_skipped(self, parser: ChatGPTParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        contents = [m.content for m in messages]
        assert not any("should be skipped" in c for c in contents)

    def test_timestamps_are_iso_strings(self, parser: ChatGPTParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        for msg in messages:
            # Timestamp must be a non-empty ISO string or empty string
            assert isinstance(msg.timestamp, str)
            if msg.timestamp:
                assert "T" in msg.timestamp, f"Not ISO format: {msg.timestamp}"

    def test_content_is_non_empty(self, parser: ChatGPTParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        for msg in messages:
            assert msg.content.strip(), "Content should not be blank"

    def test_alternating_roles(self, parser: ChatGPTParser, sample_data: str) -> None:
        messages = parser.parse(sample_data)
        # The sample has user→assistant→user→assistant ordering (tool is dropped)
        roles = [m.role for m in messages]
        assert roles == ["user", "assistant", "user", "assistant"]


class TestChatGPTParserEdgeCases:
    def test_malformed_json_returns_empty_list(self, parser: ChatGPTParser) -> None:
        assert parser.parse("{not valid json}") == []

    def test_empty_string_returns_empty_list(self, parser: ChatGPTParser) -> None:
        assert parser.parse("") == []

    def test_whitespace_only_returns_empty_list(self, parser: ChatGPTParser) -> None:
        assert parser.parse("   \n\t  ") == []

    def test_non_list_json_returns_empty_list(self, parser: ChatGPTParser) -> None:
        assert parser.parse('{"key": "value"}') == []

    def test_null_message_nodes_are_skipped(self, parser: ChatGPTParser) -> None:
        data = json.dumps([{"title": "t", "mapping": {"id1": {"message": None}}}])
        assert parser.parse(data) == []

    def test_missing_parts_yields_no_message(self, parser: ChatGPTParser) -> None:
        data = json.dumps([{
            "title": "t",
            "mapping": {
                "id1": {
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": []},
                        "create_time": 1234567890,
                    }
                }
            }
        }])
        assert parser.parse(data) == []
