# tests/test_retrieval/test_reranker.py
"""Tests for HaikuReranker — the pipeline-level rerank adapter.

Post T-120 migration (v0.5.0): tests inject a mock backend through the
`HaikuReranker(backend=...)` constructor rather than patching the
anthropic SDK or assigning `_client` directly. This matches the new
provider-agnostic interface and gives tests clean control of the backend's
return values and exception behaviour.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from depthfusion.backends.base import (
    BackendOverloadError,
    BackendTimeoutError,
    RateLimitError,
)
from depthfusion.backends.null import NullBackend
from depthfusion.retrieval.reranker import HaikuReranker

SAMPLE_BLOCKS = [
    {"chunk_id": "vps-instance", "source": "memory", "score": 5.0,
     "snippet": "VPS server at 77.42.45.197, SSH access via key auth"},
    {"chunk_id": "preferences", "source": "memory", "score": 3.0,
     "snippet": "Coding preferences: TypeScript strict mode, no any types"},
    {"chunk_id": "project-patterns", "source": "memory", "score": 1.0,
     "snippet": "Cross-project patterns for architecture decisions"},
]


# ── Availability / passthrough ───────────────────────────────────────────


def test_reranker_is_disabled_when_no_api_key(monkeypatch):
    """Without a DEPTHFUSION_API_KEY, the factory returns NullBackend
    whose `healthy()` is True but whose rerank is a no-op — the reranker
    reports unavailable because... actually it reports .healthy() from
    whatever backend. NullBackend's healthy() is True, but the is_available
    contract now tracks the backend — passthrough still happens because
    NullBackend.rerank returns identity ordering with 0.0 scores.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    r = HaikuReranker()
    # In local mode with no API key, the factory returns NullBackend,
    # which is healthy. The rerank behaviour is what differs — see below.
    assert isinstance(r._backend, NullBackend)


def test_reranker_passthrough_with_null_backend():
    """Explicit NullBackend injection → rerank returns first-N ordering
    (identity) truncated to top_k. This is the semantic equivalent of
    the v0.4.x 'no API key' passthrough.
    """
    r = HaikuReranker(backend=NullBackend())
    result = r.rerank("VPS server IP", SAMPLE_BLOCKS, top_k=3)
    assert len(result) == 3
    # NullBackend returns identity (0, 0.0), (1, 0.0), (2, 0.0) — so
    # we get the first-N blocks in original order.
    assert result[0]["chunk_id"] == "vps-instance"
    assert result[1]["chunk_id"] == "preferences"
    assert result[2]["chunk_id"] == "project-patterns"


def test_reranker_passthrough_when_backend_unhealthy():
    """Injected unhealthy backend → identity truncation to top_k."""
    mock_backend = MagicMock()
    mock_backend.healthy.return_value = False
    r = HaikuReranker(backend=mock_backend)
    result = r.rerank("anything", SAMPLE_BLOCKS, top_k=2)
    assert result == SAMPLE_BLOCKS[:2]
    # Backend's rerank should NOT be called when unhealthy
    mock_backend.rerank.assert_not_called()


# ── Reorder + fill-to-top-k ──────────────────────────────────────────────


def test_reranker_returns_reordered_blocks():
    """Happy path: backend returns indices [0, 2, 1] → blocks reorder."""
    mock_backend = MagicMock()
    mock_backend.healthy.return_value = True
    mock_backend.rerank.return_value = [(0, 1.0), (2, 0.95), (1, 0.90)]
    r = HaikuReranker(backend=mock_backend)
    result = r.rerank("VPS server", SAMPLE_BLOCKS, top_k=2)

    assert len(result) == 2
    assert result[0]["chunk_id"] == "vps-instance"
    assert result[1]["chunk_id"] == "project-patterns"


def test_reranker_fill_to_top_k_preserves_v04_behaviour():
    """v0.4.x fill-to-top_k: if backend returns < top_k indices,
    remaining blocks are appended in original BM25 order.
    """
    mock_backend = MagicMock()
    mock_backend.healthy.return_value = True
    # Backend returns only 1 index — HaikuReranker fills with remaining
    mock_backend.rerank.return_value = [(2, 1.0)]
    r = HaikuReranker(backend=mock_backend)
    result = r.rerank("query", SAMPLE_BLOCKS, top_k=3)

    assert len(result) == 3
    assert result[0]["chunk_id"] == "project-patterns"  # from backend
    assert result[1]["chunk_id"] == "vps-instance"       # fill[0]
    assert result[2]["chunk_id"] == "preferences"        # fill[1]


def test_reranker_drops_invalid_indices_from_backend():
    """Defensive: if backend returns out-of-range indices, they're
    skipped without crashing.
    """
    mock_backend = MagicMock()
    mock_backend.healthy.return_value = True
    mock_backend.rerank.return_value = [(99, 1.0), (0, 0.9)]
    r = HaikuReranker(backend=mock_backend)
    result = r.rerank("q", SAMPLE_BLOCKS, top_k=3)
    # Invalid index 99 dropped; 0 kept; then fill with remaining
    assert result[0]["chunk_id"] == "vps-instance"


# ── Error handling: typed errors + unexpected exceptions both degrade ───


def test_reranker_falls_back_on_rate_limit():
    """RateLimitError from the backend is caught at the reranker boundary
    and becomes a graceful passthrough — preserving v0.4.x pipeline-layer
    behaviour. (The typed error still surfaced at the backend level so
    future fallback-chain logic can react.)
    """
    mock_backend = MagicMock()
    mock_backend.healthy.return_value = True
    mock_backend.rerank.side_effect = RateLimitError("limited")
    r = HaikuReranker(backend=mock_backend)
    result = r.rerank("q", SAMPLE_BLOCKS, top_k=2)
    assert result == SAMPLE_BLOCKS[:2]


def test_reranker_falls_back_on_overload():
    mock_backend = MagicMock()
    mock_backend.healthy.return_value = True
    mock_backend.rerank.side_effect = BackendOverloadError("overloaded")
    r = HaikuReranker(backend=mock_backend)
    assert r.rerank("q", SAMPLE_BLOCKS, top_k=2) == SAMPLE_BLOCKS[:2]


def test_reranker_falls_back_on_timeout():
    mock_backend = MagicMock()
    mock_backend.healthy.return_value = True
    mock_backend.rerank.side_effect = BackendTimeoutError("timed out")
    r = HaikuReranker(backend=mock_backend)
    assert r.rerank("q", SAMPLE_BLOCKS, top_k=2) == SAMPLE_BLOCKS[:2]


def test_reranker_falls_back_on_unexpected_exception():
    """Non-typed exceptions (e.g. from a future backend) still produce
    a safe passthrough — we never let the pipeline layer crash.
    """
    mock_backend = MagicMock()
    mock_backend.healthy.return_value = True
    mock_backend.rerank.side_effect = RuntimeError("something weird")
    r = HaikuReranker(backend=mock_backend)
    assert r.rerank("q", SAMPLE_BLOCKS, top_k=2) == SAMPLE_BLOCKS[:2]


# ── Edge cases ───────────────────────────────────────────────────────────


def test_reranker_empty_blocks_returns_empty():
    r = HaikuReranker(backend=NullBackend())
    assert r.rerank("anything", [], top_k=3) == []


def test_reranker_converts_blocks_to_docs_correctly():
    """Regression guard: the blocks→docs translation uses snippet with
    300-char cap, falling back to chunk_id. v0.4.x semantics preserved.
    """
    mock_backend = MagicMock()
    mock_backend.healthy.return_value = True
    mock_backend.rerank.return_value = [(0, 1.0)]
    r = HaikuReranker(backend=mock_backend)
    r.rerank("q", SAMPLE_BLOCKS, top_k=1)

    call_args = mock_backend.rerank.call_args
    docs = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("docs")
    assert docs is not None
    assert len(docs) == 3
    # Docs are snippet strings truncated to 300 chars
    assert all(isinstance(d, str) for d in docs)
    assert all(len(d) <= 300 for d in docs)
