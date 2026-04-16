# tests/test_retrieval/test_reranker.py
from unittest.mock import MagicMock, patch

from depthfusion.retrieval.reranker import HaikuReranker

SAMPLE_BLOCKS = [
    {"chunk_id": "vps-instance", "source": "memory", "score": 5.0,
     "snippet": "VPS server at 77.42.45.197, SSH access via key auth"},
    {"chunk_id": "preferences", "source": "memory", "score": 3.0,
     "snippet": "Coding preferences: TypeScript strict mode, no any types"},
    {"chunk_id": "project-patterns", "source": "memory", "score": 1.0,
     "snippet": "Cross-project patterns for architecture decisions"},
]


def test_reranker_is_disabled_when_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    r = HaikuReranker()
    assert not r.is_available()


def test_reranker_passthrough_when_unavailable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    r = HaikuReranker()
    result = r.rerank("VPS server IP", SAMPLE_BLOCKS, top_k=3)
    assert result == SAMPLE_BLOCKS  # unchanged passthrough


def test_reranker_returns_top_k(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="[0, 2, 1]")]
    with patch("depthfusion.retrieval.reranker.anthropic") as mock_anthropic,          patch("depthfusion.retrieval.reranker._ANTHROPIC_IMPORTABLE", True):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic.Anthropic.return_value = mock_client
        r = HaikuReranker()
        result = r.rerank("VPS server", SAMPLE_BLOCKS, top_k=2)
    assert len(result) == 2
    assert result[0]["chunk_id"] == "vps-instance"
    assert result[1]["chunk_id"] == "project-patterns"


def test_reranker_fallback_on_bad_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="I cannot determine relevance")]
    with patch("depthfusion.retrieval.reranker.anthropic") as mock_anthropic,          patch("depthfusion.retrieval.reranker._ANTHROPIC_IMPORTABLE", True):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic.Anthropic.return_value = mock_client
        r = HaikuReranker()
        result = r.rerank("VPS server", SAMPLE_BLOCKS, top_k=2)
    # Should fall back to original order on bad JSON
    assert len(result) == 2
    assert result[0]["chunk_id"] == "vps-instance"


def test_reranker_handles_api_exception(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with patch("depthfusion.retrieval.reranker.anthropic") as mock_anthropic,          patch("depthfusion.retrieval.reranker._ANTHROPIC_IMPORTABLE", True):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        mock_anthropic.Anthropic.return_value = mock_client
        r = HaikuReranker()
        result = r.rerank("VPS server", SAMPLE_BLOCKS, top_k=2)
    # Should fall back to original order on exception
    assert len(result) == 2
    assert result[0]["chunk_id"] == "vps-instance"


def test_reranker_empty_blocks(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    r = HaikuReranker()
    result = r.rerank("anything", [], top_k=3)
    assert result == []
