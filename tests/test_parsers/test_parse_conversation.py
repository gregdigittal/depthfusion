"""Integration tests for depthfusion.parsers.parse_conversation."""
from __future__ import annotations

import json
from pathlib import Path

from depthfusion.parsers import parse_conversation

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestParseConversationReturnShape:
    """parse_conversation must always return list[dict] with the expected keys."""

    REQUIRED_KEYS = {"role", "content", "timestamp"}

    def _assert_shape(self, result: list[dict]) -> None:
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, dict)
            assert self.REQUIRED_KEYS <= set(item.keys()), (
                f"Missing keys. Got: {set(item.keys())}"
            )

    def test_chatgpt_returns_correct_shape(self) -> None:
        data = (FIXTURES / "chatgpt-sample.json").read_text()
        result = parse_conversation("chatgpt", data)
        self._assert_shape(result)
        assert len(result) >= 2

    def test_gemini_returns_correct_shape(self) -> None:
        data = (FIXTURES / "gemini-sample.json").read_text()
        result = parse_conversation("gemini", data)
        self._assert_shape(result)
        assert len(result) >= 2

    def test_deepseek_returns_correct_shape(self) -> None:
        data = (FIXTURES / "deepseek-sample.json").read_text()
        result = parse_conversation("deepseek", data)
        self._assert_shape(result)
        assert len(result) >= 2

    def test_generic_returns_correct_shape(self) -> None:
        data = "Human: Hi\nAssistant: Hello!"
        result = parse_conversation("generic", data)
        self._assert_shape(result)

    def test_unknown_provider_falls_back_to_generic(self) -> None:
        data = json.dumps([{"role": "user", "content": "test"}])
        result = parse_conversation("unknownprovider", data)
        self._assert_shape(result)
        assert result[0]["role"] == "user"


class TestParseConversationValues:
    def test_chatgpt_roles_are_valid(self) -> None:
        data = (FIXTURES / "chatgpt-sample.json").read_text()
        result = parse_conversation("chatgpt", data)
        for item in result:
            assert item["role"] in ("user", "assistant", "system")

    def test_gemini_timestamps_are_empty_strings(self) -> None:
        data = (FIXTURES / "gemini-sample.json").read_text()
        result = parse_conversation("gemini", data)
        for item in result:
            assert item["timestamp"] == ""

    def test_chatgpt_timestamps_are_iso_or_empty(self) -> None:
        data = (FIXTURES / "chatgpt-sample.json").read_text()
        result = parse_conversation("chatgpt", data)
        for item in result:
            ts = item["timestamp"]
            assert isinstance(ts, str)
            if ts:
                assert "T" in ts

    def test_empty_input_returns_empty_list(self) -> None:
        for provider in ("chatgpt", "gemini", "deepseek", "generic"):
            assert parse_conversation(provider, "") == [], f"Failed for {provider}"

    def test_malformed_json_returns_empty_list_for_json_parsers(self) -> None:
        bad = "{ not json }"
        for provider in ("chatgpt", "gemini", "deepseek"):
            assert parse_conversation(provider, bad) == [], f"Failed for {provider}"
