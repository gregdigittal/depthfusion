# tests/test_backends/test_local_embedding.py
"""LocalEmbeddingBackend behaviour tests — T-118 / T-129 / T-131.

The backend wraps sentence-transformers for on-box vector embeddings.
Tests exercise the LLMBackend protocol contract + graceful-degradation
paths. The real sentence-transformers package is optional; most tests
inject a fake model via monkeypatch so they run without the dependency.

Backlog: T-118 (S-41 AC-6), T-129 / T-131 (S-43 AC-4).
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from depthfusion.backends.local_embedding import LocalEmbeddingBackend

# ---------------------------------------------------------------------------
# Fake sentence_transformers module (injected via sys.modules)
# ---------------------------------------------------------------------------

class _FakeEncoder:
    """Stand-in for SentenceTransformer that returns deterministic vectors.

    Each text becomes a 3-dim vector where the first component is the
    length of the text (normalised to 0-1). Enough signal for similarity
    assertions without pulling in the real model.
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.encode_calls: list[list[str]] = []

    def encode(self, texts, **kwargs):
        import numpy as np
        self.encode_calls.append(list(texts))
        # deterministic 3-dim "embedding" — enough to test shape and cosine sim
        rows = []
        for t in texts:
            length = min(len(t), 100) / 100.0
            first_char = (ord(t[0]) % 97) / 96.0 if t else 0.0
            rows.append([length, first_char, 0.5])
        return np.array(rows, dtype=float)


@pytest.fixture
def fake_sentence_transformers(monkeypatch):
    """Inject a fake `sentence_transformers` module.

    `find_spec` does not consult sys.modules — it queries the import
    finders — so we also stub it out so healthy() returns True.
    """
    fake_mod = MagicMock()
    fake_mod.SentenceTransformer = _FakeEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)
    monkeypatch.setattr(
        "depthfusion.backends.local_embedding.importlib.util.find_spec",
        lambda name, *a, **kw: object() if name == "sentence_transformers" else None,
    )
    return fake_mod


# ---------------------------------------------------------------------------
# Construction / identity
# ---------------------------------------------------------------------------

def test_name_is_local_embedding():
    """Stable identifier for audit records and factory dispatch."""
    assert LocalEmbeddingBackend().name == "local_embedding"


def test_default_model_is_all_minilm():
    """Default model must match the documented choice (384-dim, CPU-friendly)."""
    backend = LocalEmbeddingBackend()
    assert backend._model_name == "all-MiniLM-L6-v2"


def test_model_override_via_constructor():
    backend = LocalEmbeddingBackend(model_name="custom/model-v2")
    assert backend._model_name == "custom/model-v2"


def test_model_override_via_env_var(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_EMBEDDING_MODEL", "env/override-v1")
    backend = LocalEmbeddingBackend()
    assert backend._model_name == "env/override-v1"


def test_constructor_arg_beats_env_var(monkeypatch):
    """Explicit constructor argument wins over env-var default."""
    monkeypatch.setenv("DEPTHFUSION_EMBEDDING_MODEL", "env/one")
    backend = LocalEmbeddingBackend(model_name="ctor/wins")
    assert backend._model_name == "ctor/wins"


# ---------------------------------------------------------------------------
# healthy()
# ---------------------------------------------------------------------------

def test_healthy_true_when_package_available(fake_sentence_transformers):
    """With a fake sentence_transformers in sys.modules, healthy() is True."""
    backend = LocalEmbeddingBackend()
    assert backend.healthy() is True


def test_healthy_false_when_package_missing(monkeypatch):
    """Force ImportError by making find_spec return None."""
    import importlib.util as iu

    def fake_find_spec(name, *args, **kwargs):
        if name == "sentence_transformers":
            return None
        return iu.find_spec(name, *args, **kwargs)

    monkeypatch.setattr(
        "depthfusion.backends.local_embedding.importlib.util.find_spec",
        fake_find_spec,
    )
    backend = LocalEmbeddingBackend()
    assert backend.healthy() is False


def test_healthy_false_after_load_failure(fake_sentence_transformers, monkeypatch):
    """Once a load attempt fails, healthy() should report degraded permanently."""
    backend = LocalEmbeddingBackend()
    backend._load_failed = True  # simulate prior failure
    assert backend.healthy() is False


def test_healthy_does_not_load_model(fake_sentence_transformers):
    """Contract: healthy() MUST NOT load the model (would be expensive)."""
    backend = LocalEmbeddingBackend()
    backend.healthy()
    assert backend._model is None  # not constructed yet


# ---------------------------------------------------------------------------
# embed()
# ---------------------------------------------------------------------------

def test_embed_returns_list_of_lists(fake_sentence_transformers):
    backend = LocalEmbeddingBackend()
    result = backend.embed(["hello world", "second text"])
    assert result is not None
    assert len(result) == 2
    assert all(isinstance(v, list) for v in result)
    assert all(isinstance(x, float) for v in result for x in v)


def test_embed_preserves_input_order(fake_sentence_transformers):
    backend = LocalEmbeddingBackend()
    result = backend.embed(["a", "bbbbbbbbb"])
    assert result is not None
    # _FakeEncoder's first component is length-based; "bbbbbbbbb" > "a"
    assert result[1][0] > result[0][0]


def test_embed_empty_list_returns_empty_list(fake_sentence_transformers):
    """Empty input is valid (not an error) — return []."""
    backend = LocalEmbeddingBackend()
    assert backend.embed([]) == []


def test_embed_returns_none_when_package_missing(monkeypatch):
    """With no sentence_transformers importable, embed() returns None."""
    # Remove the package from sys.modules AND intercept import
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    backend = LocalEmbeddingBackend()
    result = backend.embed(["text"])
    assert result is None
    assert backend._load_failed is True  # sticky flag set


def test_embed_caches_model_after_first_call(fake_sentence_transformers):
    """Lazy-load: model constructed on first embed(), reused on subsequent."""
    backend = LocalEmbeddingBackend()
    backend.embed(["a"])
    first_model = backend._model
    assert first_model is not None
    backend.embed(["b"])
    assert backend._model is first_model  # same instance reused


def test_embed_returns_none_after_sticky_failure(fake_sentence_transformers):
    """Once load_failed=True, all subsequent embed() calls short-circuit."""
    backend = LocalEmbeddingBackend()
    backend._load_failed = True
    assert backend.embed(["anything"]) is None


def test_embed_handles_encode_exception(fake_sentence_transformers, monkeypatch):
    """Runtime errors during encode() translate to None, not propagation."""
    backend = LocalEmbeddingBackend()
    # Force model to a broken stub
    broken_model = MagicMock()
    broken_model.encode.side_effect = RuntimeError("CUDA OOM")
    backend._model = broken_model
    assert backend.embed(["text"]) is None


# ---------------------------------------------------------------------------
# Degenerate protocol methods (complete / rerank / extract_structured)
# ---------------------------------------------------------------------------

def test_complete_returns_empty_string():
    """Embedding backend does not generate text."""
    assert LocalEmbeddingBackend().complete("prompt", max_tokens=100) == ""


def test_complete_accepts_system_prompt():
    """Signature must include optional system; ignored but must not raise."""
    assert LocalEmbeddingBackend().complete("p", max_tokens=50, system="sys") == ""


def test_rerank_returns_neutral_scores():
    """rerank() returns (index, 0.0) tuples matching NullBackend's contract."""
    result = LocalEmbeddingBackend().rerank("q", ["a", "b", "c"], top_k=2)
    assert result == [(0, 0.0), (1, 0.0)]


def test_rerank_handles_empty_docs():
    assert LocalEmbeddingBackend().rerank("q", [], top_k=5) == []


def test_rerank_top_k_exceeds_docs_length():
    result = LocalEmbeddingBackend().rerank("q", ["a", "b"], top_k=10)
    assert len(result) == 2


def test_extract_structured_returns_none():
    """Embedding backend does not extract structured output."""
    assert LocalEmbeddingBackend().extract_structured("p", {"type": "object"}) is None


# ---------------------------------------------------------------------------
# Integration (optional — only runs if sentence-transformers is installed)
# ---------------------------------------------------------------------------

def test_integration_real_sentence_transformers():
    """End-to-end: if the real package is installed, embeddings should have
    the expected dimensionality (384 for all-MiniLM-L6-v2) and be distinct
    for distinct inputs.
    """
    pytest.importorskip("sentence_transformers")
    backend = LocalEmbeddingBackend()
    assert backend.healthy() is True
    result = backend.embed(["dog", "cat"])
    assert result is not None
    assert len(result) == 2
    # all-MiniLM-L6-v2 produces 384-dim vectors
    assert len(result[0]) == 384
    # Distinct inputs must give distinct vectors
    assert result[0] != result[1]
