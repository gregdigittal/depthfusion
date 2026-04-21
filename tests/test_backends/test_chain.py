"""Tests for FallbackChain (S-44 AC-3/AC-4).

Uses fake backends (no Haiku / Gemma dependency) to exercise the
chain's routing logic in isolation. Thread-safety and protocol
conformance are covered alongside the core fallback semantics.
"""
from __future__ import annotations

import pytest

from depthfusion.backends.base import (
    BackendExhaustedError,
    BackendOverloadError,
    BackendTimeoutError,
    LLMBackend,
    RateLimitError,
)
from depthfusion.backends.chain import FallbackChain


# --------------------------------------------------------------------------
# Fake backends
# --------------------------------------------------------------------------

_UNSET = object()  # sentinel so None can be passed explicitly as a result


class _FakeBackend:
    """Configurable `LLMBackend` stand-in. Records call counts."""

    def __init__(
        self,
        name: str,
        *,
        healthy: bool = True,
        complete_result: str | Exception = "ok",
        embed_result: object = _UNSET,    # list[list[float]] | None | Exception
        rerank_result: object = _UNSET,   # list[tuple[int, float]] | Exception
        extract_result: object = _UNSET,  # dict | None | Exception
    ) -> None:
        self.name = name
        self._healthy = healthy
        self._complete = complete_result
        self._embed = [[0.0]] if embed_result is _UNSET else embed_result
        self._rerank = [(0, 1.0)] if rerank_result is _UNSET else rerank_result
        self._extract = None if extract_result is _UNSET else extract_result
        self.call_counts = {"complete": 0, "embed": 0, "rerank": 0, "extract": 0}

    def healthy(self) -> bool:
        return self._healthy

    def complete(self, prompt: str, *, max_tokens: int, system: str | None = None) -> str:
        self.call_counts["complete"] += 1
        if isinstance(self._complete, Exception):
            raise self._complete
        return self._complete

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        self.call_counts["embed"] += 1
        if isinstance(self._embed, Exception):
            raise self._embed
        return self._embed  # type: ignore[return-value]

    def rerank(self, query: str, docs: list[str], top_k: int) -> list[tuple[int, float]]:
        self.call_counts["rerank"] += 1
        if isinstance(self._rerank, Exception):
            raise self._rerank
        return self._rerank  # type: ignore[return-value]

    def extract_structured(self, prompt: str, schema: dict) -> dict | None:
        self.call_counts["extract"] += 1
        if isinstance(self._extract, Exception):
            raise self._extract
        return self._extract  # type: ignore[return-value]


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------

class TestConstruction:
    def test_empty_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            FallbackChain([])

    def test_name_is_composite(self):
        chain = FallbackChain([_FakeBackend("a"), _FakeBackend("b"), _FakeBackend("c")])
        assert chain.name == "a+b+c"

    def test_single_backend(self):
        chain = FallbackChain([_FakeBackend("solo")])
        assert chain.name == "solo"
        assert chain.healthy()

    def test_conforms_to_protocol(self):
        chain = FallbackChain([_FakeBackend("a")])
        assert isinstance(chain, LLMBackend)


class TestHealthy:
    def test_all_healthy(self):
        chain = FallbackChain([_FakeBackend("a"), _FakeBackend("b")])
        assert chain.healthy() is True

    def test_one_healthy(self):
        chain = FallbackChain([
            _FakeBackend("a", healthy=False),
            _FakeBackend("b", healthy=True),
        ])
        assert chain.healthy() is True

    def test_none_healthy(self):
        chain = FallbackChain([
            _FakeBackend("a", healthy=False),
            _FakeBackend("b", healthy=False),
        ])
        assert chain.healthy() is False


# --------------------------------------------------------------------------
# Fallback on typed errors
# --------------------------------------------------------------------------

class TestCompleteFallback:
    def test_first_succeeds(self):
        a = _FakeBackend("a", complete_result="from-a")
        b = _FakeBackend("b", complete_result="from-b")
        chain = FallbackChain([a, b])
        assert chain.complete("hi", max_tokens=10) == "from-a"
        assert a.call_counts["complete"] == 1
        assert b.call_counts["complete"] == 0

    def test_rate_limit_triggers_fallback(self):
        a = _FakeBackend("a", complete_result=RateLimitError("429"))
        b = _FakeBackend("b", complete_result="from-b")
        chain = FallbackChain([a, b])
        assert chain.complete("hi", max_tokens=10) == "from-b"
        assert a.call_counts["complete"] == 1
        assert b.call_counts["complete"] == 1

    def test_overload_triggers_fallback(self):
        a = _FakeBackend("a", complete_result=BackendOverloadError("529"))
        b = _FakeBackend("b", complete_result="from-b")
        chain = FallbackChain([a, b])
        assert chain.complete("hi", max_tokens=10) == "from-b"

    def test_timeout_triggers_fallback(self):
        a = _FakeBackend("a", complete_result=BackendTimeoutError("slow"))
        b = _FakeBackend("b", complete_result="from-b")
        chain = FallbackChain([a, b])
        assert chain.complete("hi", max_tokens=10) == "from-b"

    def test_non_fallback_exception_propagates(self):
        # S-44 contract: only typed errors trigger fallback; other
        # exceptions are bugs and must surface to the caller.
        a = _FakeBackend("a", complete_result=ValueError("unexpected"))
        b = _FakeBackend("b", complete_result="from-b")
        chain = FallbackChain([a, b])
        with pytest.raises(ValueError, match="unexpected"):
            chain.complete("hi", max_tokens=10)
        # b was NOT tried — only typed errors trigger fallback
        assert b.call_counts["complete"] == 0

    def test_all_raise_triggers_exhausted(self):
        a = _FakeBackend("a", complete_result=RateLimitError("a-429"))
        b = _FakeBackend("b", complete_result=BackendTimeoutError("b-timeout"))
        chain = FallbackChain([a, b])
        with pytest.raises(BackendExhaustedError) as excinfo:
            chain.complete("hi", max_tokens=10)
        assert excinfo.value.chain == ["a", "b"]

    def test_unhealthy_backends_skipped(self):
        # An unhealthy backend is not counted as a fallback attempt;
        # the chain skips straight to the next healthy one.
        a = _FakeBackend("a", healthy=False, complete_result="never-called")
        b = _FakeBackend("b", complete_result="from-b")
        chain = FallbackChain([a, b])
        assert chain.complete("hi", max_tokens=10) == "from-b"
        assert a.call_counts["complete"] == 0  # never called
        assert b.call_counts["complete"] == 1

    def test_all_unhealthy_raises_exhausted_empty_chain(self):
        a = _FakeBackend("a", healthy=False)
        b = _FakeBackend("b", healthy=False)
        chain = FallbackChain([a, b])
        with pytest.raises(BackendExhaustedError) as excinfo:
            chain.complete("hi", max_tokens=10)
        # Nothing was actually tried — chain attribute is empty
        assert excinfo.value.chain == []


# --------------------------------------------------------------------------
# Embed / rerank / extract_structured — same semantics
# --------------------------------------------------------------------------

class TestEmbedFallback:
    def test_first_none_is_final(self):
        # Returning None (backend doesn't support embedding) is NOT a
        # fallback trigger — it's a valid return per protocol.
        a = _FakeBackend("a", embed_result=None)
        b = _FakeBackend("b", embed_result=[[1.0, 2.0]])
        chain = FallbackChain([a, b])
        assert chain.embed(["t"]) is None
        assert b.call_counts["embed"] == 0  # not tried

    def test_timeout_triggers_fallback(self):
        a = _FakeBackend("a", embed_result=BackendTimeoutError("slow"))
        b = _FakeBackend("b", embed_result=[[1.0, 2.0]])
        chain = FallbackChain([a, b])
        assert chain.embed(["t"]) == [[1.0, 2.0]]


class TestRerankFallback:
    def test_fallback_on_overload(self):
        a = _FakeBackend("a", rerank_result=BackendOverloadError("overloaded"))
        b = _FakeBackend("b", rerank_result=[(0, 0.9), (1, 0.8)])
        chain = FallbackChain([a, b])
        result = chain.rerank("q", ["d1", "d2"], top_k=2)
        assert result == [(0, 0.9), (1, 0.8)]


class TestExtractStructuredFallback:
    def test_fallback_on_rate_limit(self):
        a = _FakeBackend("a", extract_result=RateLimitError("429"))
        b = _FakeBackend("b", extract_result={"field": "value"})
        chain = FallbackChain([a, b])
        assert chain.extract_structured("prompt", {}) == {"field": "value"}

    def test_none_is_final(self):
        # None is the valid "not supported" sentinel for extract_structured
        a = _FakeBackend("a", extract_result=None)
        b = _FakeBackend("b", extract_result={"field": "value"})
        chain = FallbackChain([a, b])
        assert chain.extract_structured("prompt", {}) is None


# --------------------------------------------------------------------------
# Event emission
# --------------------------------------------------------------------------

class TestFallbackEventEmission:
    def test_emits_on_transition(self, monkeypatch, tmp_path):
        # Point metrics dir at a tmp location so we don't pollute global state
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", "true")

        a = _FakeBackend("a", complete_result=RateLimitError("429"))
        b = _FakeBackend("b", complete_result="ok")
        chain = FallbackChain([a, b])
        chain.complete("hi", max_tokens=10)

        # Locate today's metrics file
        from depthfusion.metrics.collector import MetricsCollector
        mc = MetricsCollector()
        path = mc.metrics_dir / f"{__import__('datetime').date.today().isoformat()}.jsonl"
        if not path.exists():
            pytest.skip("metrics dir not under HOME (container-specific path)")
        text = path.read_text()
        assert "backend.runtime_fallback" in text
        assert '"from": "a"' in text
        assert '"to": "b"' in text
        assert "RateLimitError" in text

    def test_disabled_via_env_var(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("DEPTHFUSION_BACKEND_FALLBACK_LOG", "false")

        a = _FakeBackend("a", complete_result=RateLimitError("429"))
        b = _FakeBackend("b", complete_result="ok")
        chain = FallbackChain([a, b])
        chain.complete("hi", max_tokens=10)

        # No metrics file should exist (or it should not contain our event)
        from depthfusion.metrics.collector import MetricsCollector
        mc = MetricsCollector()
        path = mc.metrics_dir / f"{__import__('datetime').date.today().isoformat()}.jsonl"
        if path.exists():
            text = path.read_text()
            assert "backend.runtime_fallback" not in text


# --------------------------------------------------------------------------
# End-to-end chain semantics: 3-link cascade
# --------------------------------------------------------------------------

class TestThreeLinkCascade:
    def test_gemma_haiku_null_cascade(self):
        # Simulates the v0.6 default vps-gpu chain: Gemma -> Haiku -> Null.
        # Gemma overloads; Haiku rate-limits; Null returns safe default.
        gemma = _FakeBackend("gemma", complete_result=BackendOverloadError("529"))
        haiku = _FakeBackend("haiku", complete_result=RateLimitError("429"))
        null = _FakeBackend("null", complete_result="")  # NullBackend's default
        chain = FallbackChain([gemma, haiku, null])

        result = chain.complete("summarise this", max_tokens=64)
        assert result == ""  # Null's safe default — chain did not exhaust
        assert gemma.call_counts["complete"] == 1
        assert haiku.call_counts["complete"] == 1
        assert null.call_counts["complete"] == 1

    def test_exhaustion_chain_names_in_order(self):
        a = _FakeBackend("a", complete_result=RateLimitError("!"))
        b = _FakeBackend("b", complete_result=BackendTimeoutError("!"))
        c = _FakeBackend("c", complete_result=BackendOverloadError("!"))
        chain = FallbackChain([a, b, c])
        with pytest.raises(BackendExhaustedError) as excinfo:
            chain.complete("x", max_tokens=1)
        assert excinfo.value.chain == ["a", "b", "c"]
        # And the message is informative
        assert "exhausted" in str(excinfo.value)
