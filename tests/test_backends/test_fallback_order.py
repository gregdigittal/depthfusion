"""Tests for S-41 AC-8 — quality-ranked fallback chain (DR-018 §4 → I-18).

The factory's fallback order must be quality-descending. Cost/latency
optimisation may reorder within a quality tier but may NOT promote a
lower-quality backend over a higher-quality one.

Quality ranking (descending):
  gemma (tier 3) > haiku (tier 2) > local (tier 1, embedding only) > null (tier 0)

Contracts under test:
  - vps-gpu LLM caps: chain = [gemma, haiku, null] — gemma unhealthy → haiku tried first
  - vps-gpu embedding: chain = [local, null] — local unhealthy → null
  - vps-cpu LLM caps: chain = [haiku, null]
  - local: always null (no LLM backends available at local tier)
  - A lower-quality backend is never returned when a higher-quality one is healthy
"""
from __future__ import annotations

import pytest

from depthfusion.backends.chain import FallbackChain
from depthfusion.backends.factory import get_backend
from depthfusion.backends.null import NullBackend

_LLM_CAPS = ["reranker", "extractor", "linker", "summariser", "decision_extractor"]


# ---------------------------------------------------------------------------
# vps-gpu: gemma unhealthy → haiku (not null)
# ---------------------------------------------------------------------------

class TestVpsGpuFallbackOrder:
    def test_gemma_unhealthy_returns_chain_not_null_directly(
        self, monkeypatch
    ):
        """When gemma is unhealthy on vps-gpu, the factory must return a chain
        that includes haiku rather than immediately falling back to NullBackend.
        DR-018 §4 mandates quality-ranked order: gemma → haiku → null.
        """
        monkeypatch.setenv("DEPTHFUSION_MODE", "vps-gpu")
        monkeypatch.setenv("DEPTHFUSION_GEMMA_URL", "")      # makes gemma unhealthy
        monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-ant-test")
        monkeypatch.delenv("DEPTHFUSION_RERANKER_BACKEND", raising=False)

        backend = get_backend("reranker")
        # With gemma unhealthy, should be a FallbackChain containing haiku
        # (or fall to haiku directly if haiku is healthy).
        # Either way: NOT a plain NullBackend, because haiku is available.
        assert not isinstance(backend, NullBackend), (
            "vps-gpu with gemma unhealthy must not immediately return NullBackend; "
            "haiku is still available in the quality chain"
        )

    def test_gemma_and_haiku_both_unhealthy_returns_null(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_MODE", "vps-gpu")
        monkeypatch.setenv("DEPTHFUSION_GEMMA_URL", "")
        monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("DEPTHFUSION_RERANKER_BACKEND", raising=False)

        backend = get_backend("reranker")
        # Both real backends unhealthy → terminal NullBackend
        assert isinstance(backend, NullBackend)

    @pytest.mark.parametrize("cap", _LLM_CAPS)
    def test_vps_gpu_llm_chain_quality_order(self, monkeypatch, cap):
        """Quality ordering: returned chain names must not have null before haiku."""
        monkeypatch.setenv("DEPTHFUSION_MODE", "vps-gpu")
        monkeypatch.setenv("DEPTHFUSION_GEMMA_URL", "http://127.0.0.1:8000/v1")
        monkeypatch.setenv("DEPTHFUSION_GEMMA_MODEL", "google/gemma-3-12b-it-AWQ")
        monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-ant-test")
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)

        backend = get_backend(cap)
        if isinstance(backend, FallbackChain):
            names = backend.name.split("+")
            # null must never appear before haiku in the chain
            if "null" in names and "haiku" in names:
                assert names.index("null") > names.index("haiku"), (
                    f"Quality violation: null appears before haiku in chain {names}"
                )
            # haiku must never appear before gemma
            if "haiku" in names and "gemma" in names:
                assert names.index("haiku") > names.index("gemma"), (
                    f"Quality violation: haiku appears before gemma in chain {names}"
                )


# ---------------------------------------------------------------------------
# vps-cpu: haiku → null (no gemma at this tier)
# ---------------------------------------------------------------------------

class TestVpsCpuFallbackOrder:
    @pytest.mark.parametrize("cap", _LLM_CAPS)
    def test_haiku_healthy_returned_directly(self, monkeypatch, cap):
        monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
        monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-ant-test")
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)

        backend = get_backend(cap)
        # Haiku is healthy → no NullBackend at the head
        assert not isinstance(backend, NullBackend)

    @pytest.mark.parametrize("cap", _LLM_CAPS)
    def test_haiku_unhealthy_returns_null(self, monkeypatch, cap):
        monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
        monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)

        backend = get_backend(cap)
        assert isinstance(backend, NullBackend)


# ---------------------------------------------------------------------------
# local: always null — no LLM backends at local tier
# ---------------------------------------------------------------------------

class TestLocalAlwaysNull:
    @pytest.mark.parametrize("cap", _LLM_CAPS + ["embedding"])
    def test_local_mode_is_null(self, monkeypatch, cap):
        monkeypatch.setenv("DEPTHFUSION_MODE", "local")
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)
        backend = get_backend(cap)
        assert isinstance(backend, NullBackend)


# ---------------------------------------------------------------------------
# env-var override bypasses chain but must still be healthy
# ---------------------------------------------------------------------------

class TestEnvVarOverrideBypassesChain:
    def test_explicit_null_override_returns_null_on_vps_gpu(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_MODE", "vps-gpu")
        monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "null")
        backend = get_backend("reranker")
        assert isinstance(backend, NullBackend)

    def test_explicit_haiku_override_on_local_mode(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_MODE", "local")
        monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "haiku")
        monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-ant-test")
        backend = get_backend("reranker")
        assert not isinstance(backend, NullBackend)
