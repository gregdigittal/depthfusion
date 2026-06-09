from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import depthfusion.mcp.server as server


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "chatgpt-sample.json"


def _loads(result: str) -> dict:
    return json.loads(result)


def _fake_backend_factory(*, healthy: bool = True, response: str = "mock response"):
    class _FakeBackend:
        def __init__(self, model: str = "") -> None:
            self.model = model

        def healthy(self) -> bool:
            return healthy

        def complete(self, prompt: str, *, max_tokens: int, system: str | None = None, model: str | None = None) -> str:
            self.prompt = prompt
            self.max_tokens = max_tokens
            self.system = system
            self.requested_model = model
            return response

    return _FakeBackend


class TestListProviders:
    def test_list_providers_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        body = _loads(server._tool_list_providers())
        provider = body["providers"][0]

        assert provider["configured"] is False
        assert provider["healthy"] is False

    def test_list_providers_with_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        body = _loads(server._tool_list_providers())
        provider = body["providers"][0]

        assert provider["configured"] is True

    def test_list_providers_returns_valid_json(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        body = _loads(server._tool_list_providers())

        assert "providers" in body
        assert isinstance(body["providers"], list)


class TestBridge:
    def test_bridge_requires_prompt(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        body = _loads(server._tool_bridge({"model": "openai/gpt-4o", "prompt": ""}))

        assert "error" in body

    def test_bridge_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        body = _loads(server._tool_bridge({"model": "openai/gpt-4o", "prompt": "hello"}))

        assert "error" in body
        assert "OPENROUTER_API_KEY" in body["error"]

    def test_bridge_calls_openrouter_and_stores_fragment(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setattr(server, "OpenRouterBackend", _fake_backend_factory(response="bridge reply"))
        monkeypatch.setattr(server, "_tool_recall", lambda arguments: json.dumps({"blocks": [{"content": "recalled memory"}]}))

        publish_mock = MagicMock(return_value=json.dumps({"ok": True}))
        monkeypatch.setattr(server, "_tool_publish_context", publish_mock)

        body = _loads(
            server._tool_bridge(
                {
                    "model": "openai/gpt-4o",
                    "prompt": "What should I remember?",
                    "context_tags": ["session"],
                }
            )
        )

        assert body["response"] == "bridge reply"
        assert body["memories_injected"] == 1
        assert body["fragments_stored"] == 1
        assert publish_mock.call_count == 1

    def test_bridge_handles_network_error(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        class _ErrorBackend:
            def __init__(self, model: str = "") -> None:
                self.model = model

            def healthy(self) -> bool:
                return True

            def complete(self, prompt: str, *, max_tokens: int, system: str | None = None, model: str | None = None) -> str:
                raise RuntimeError("network failure")

        monkeypatch.setattr(server, "OpenRouterBackend", _ErrorBackend)

        body = _loads(server._tool_bridge({"model": "openai/gpt-4o", "prompt": "hello"}))

        assert "error" in body
        assert "network failure" in body["error"]


class TestIngestConversation:
    def test_ingest_chatgpt_fixture(self):
        data = FIXTURE_PATH.read_text()

        body = _loads(server._tool_ingest_conversation({"provider": "chatgpt", "data": data}))

        assert body["fragments_stored"] > 0

    def test_ingest_malformed_json(self):
        body = _loads(server._tool_ingest_conversation({"provider": "chatgpt", "data": "not json"}))

        assert body["fragments_stored"] == 0
        assert "errors" in body
        assert isinstance(body["errors"], list)

    def test_ingest_empty_data(self):
        body = _loads(server._tool_ingest_conversation({"provider": "generic", "data": ""}))

        assert "error" in body

    def test_ingest_skips_user_turns(self):
        data = FIXTURE_PATH.read_text()

        body = _loads(server._tool_ingest_conversation({"provider": "chatgpt", "data": data}))

        assert body["skipped"] > 0
        assert body["fragments_stored"] > 0

