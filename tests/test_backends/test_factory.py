# tests/test_backends/test_factory.py
"""Factory dispatch + env-var override tests.

The factory is the single place where (mode, capability) → backend resolution
happens. A bug here changes every LLM call-site's behaviour at once, so the
test surface is intentionally broad.

Backlog: T-119, T-122.
"""
from __future__ import annotations

import pytest

from depthfusion.backends.factory import get_backend
from depthfusion.backends.null import NullBackend

# ── Mode defaults ────────────────────────────────────────────────────────


def test_local_mode_returns_null_for_every_capability(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    for cap in ["reranker", "extractor", "linker", "summariser", "embedding", "decision_extractor"]:
        # Clear any per-capability override
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)
        backend = get_backend(cap)
        assert isinstance(backend, NullBackend), (
            f"local/{cap} did not resolve to NullBackend (got {type(backend).__name__})"
        )


def test_missing_mode_defaults_to_local(monkeypatch):
    monkeypatch.delenv("DEPTHFUSION_MODE", raising=False)
    for cap in ["reranker", "extractor", "linker"]:
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)
    backend = get_backend("reranker")
    assert isinstance(backend, NullBackend)


def test_vps_alias_maps_to_vps_cpu(monkeypatch):
    """Legacy `DEPTHFUSION_MODE=vps` is a v0.5 alias for vps-cpu.

    Without a DEPTHFUSION_API_KEY in scope, the vps-cpu haiku route
    falls back to NullBackend. The test asserts the alias works AND
    that the factory's healthy() safety net is active.
    """
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for cap in ["reranker", "extractor"]:
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)
    backend = get_backend("reranker")
    # vps → vps-cpu → haiku (requested) → no key → NullBackend (healthy fallback)
    assert isinstance(backend, NullBackend)
    assert backend.healthy()


def test_vps_gpu_mode_dispatches_all_caps(monkeypatch):
    """vps-gpu routes rerank/extract/linker/summariser/decision_extractor
    through gemma (→null in scaffolding) and embedding through local (→null).
    Every capability resolves to a concrete, healthy backend.
    """
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-gpu")
    for cap in ["reranker", "extractor", "linker", "summariser", "embedding", "decision_extractor"]:
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)
        backend = get_backend(cap)
        assert backend.healthy()


# ── Per-capability overrides ─────────────────────────────────────────────


def test_reranker_override_respected(monkeypatch):
    """DEPTHFUSION_RERANKER_BACKEND=null forces null regardless of mode."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
    monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "null")
    backend = get_backend("reranker")
    assert isinstance(backend, NullBackend)


def test_override_is_case_insensitive(monkeypatch):
    """Env-var values are trimmed and lowercased — `NULL` works as well."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
    monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "NULL")
    assert isinstance(get_backend("reranker"), NullBackend)


def test_override_with_whitespace_trimmed(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
    monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "  null  ")
    assert isinstance(get_backend("reranker"), NullBackend)


def test_empty_override_falls_through_to_mode_default(monkeypatch):
    """An empty override env-var is equivalent to not setting it."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "")
    assert isinstance(get_backend("reranker"), NullBackend)


# ── mode argument overrides env ─────────────────────────────────────────


def test_explicit_mode_argument_overrides_env(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-gpu")
    monkeypatch.delenv("DEPTHFUSION_RERANKER_BACKEND", raising=False)
    backend = get_backend("reranker", mode="local")
    assert isinstance(backend, NullBackend)


# ── Error cases ──────────────────────────────────────────────────────────


def test_unknown_capability_raises_value_error():
    with pytest.raises(ValueError, match="Unknown capability"):
        get_backend("nonexistent_capability")


def test_unknown_backend_override_raises(monkeypatch):
    """If the user sets DEPTHFUSION_*_BACKEND=fictional, fail fast rather
    than silently routing to null — this catches typos before they cause
    invisible behaviour changes.
    """
    monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "fictional-backend")
    with pytest.raises(ValueError, match="Unknown backend"):
        get_backend("reranker")


def test_unknown_mode_falls_through_to_null(monkeypatch):
    """Unknown modes are NOT errors — the default dispatch table returns
    null for anything not in the keyspace. This is deliberate: it keeps
    arbitrary mode strings from breaking production if a deploy pipeline
    sets `DEPTHFUSION_MODE=staging` or similar.
    """
    monkeypatch.setenv("DEPTHFUSION_MODE", "staging-environment")
    monkeypatch.delenv("DEPTHFUSION_RERANKER_BACKEND", raising=False)
    backend = get_backend("reranker")
    assert isinstance(backend, NullBackend)


# ── Haiku dispatch (T-116 live) ──────────────────────────────────────────


def test_haiku_override_returns_haiku_when_key_set(monkeypatch):
    """Explicit haiku override + API key → returns a healthy HaikuBackend."""
    from depthfusion.backends.haiku import HaikuBackend
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "haiku")
    monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-test")
    backend = get_backend("reranker")
    assert isinstance(backend, HaikuBackend)
    assert backend.healthy()


def test_haiku_override_falls_back_to_null_without_key(monkeypatch):
    """Explicit haiku override + no API key → falls through to NullBackend
    rather than returning an unhealthy HaikuBackend. This is the safe-by-
    default contract: callers never see an `.healthy() is False` backend
    from the factory.
    """
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    monkeypatch.setenv("DEPTHFUSION_RERANKER_BACKEND", "haiku")
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    backend = get_backend("reranker")
    assert isinstance(backend, NullBackend)


def test_vps_cpu_defaults_to_haiku_when_key_set(monkeypatch):
    """vps-cpu default for most capabilities is haiku; with a key, the
    factory returns HaikuBackend.
    """
    from depthfusion.backends.haiku import HaikuBackend
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
    monkeypatch.setenv("DEPTHFUSION_API_KEY", "sk-test")
    for cap in ["reranker", "extractor", "linker", "summariser", "decision_extractor"]:
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)
        backend = get_backend(cap)
        assert isinstance(backend, HaikuBackend), (
            f"vps-cpu/{cap} with DEPTHFUSION_API_KEY did not route to Haiku "
            f"(got {type(backend).__name__})"
        )


def test_vps_cpu_falls_back_to_null_without_key(monkeypatch):
    """Without DEPTHFUSION_API_KEY, vps-cpu routes degrade cleanly to null."""
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for cap in ["reranker", "extractor"]:
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)
        backend = get_backend(cap)
        assert isinstance(backend, NullBackend)


def test_factory_never_returns_unhealthy_backend(monkeypatch):
    """Load-bearing contract: callers can trust `backend.healthy()` is True
    without having to check it themselves. Factory handles the fallback.
    """
    # Set up a state where haiku is selected but can't construct
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
    monkeypatch.delenv("DEPTHFUSION_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for cap in ["reranker", "extractor", "linker", "summariser", "decision_extractor", "embedding"]:
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)
        backend = get_backend(cap)
        assert backend.healthy() is True, (
            f"Factory returned unhealthy backend for {cap}: {type(backend).__name__}"
        )


# ── Gemma dispatch (T-133) ──────────────────────────────────────────────


def test_gemma_override_returns_gemma():
    """Explicit gemma override → GemmaBackend. No GEX44 required for
    construction since healthy() is config-only.
    """
    import os

    from depthfusion.backends.gemma import GemmaBackend
    # Use monkeypatch-equivalent via direct env control in a fresh context
    # Note: GemmaBackend defaults are valid even without env vars set
    os.environ["DEPTHFUSION_RERANKER_BACKEND"] = "gemma"
    try:
        backend = get_backend("reranker")
        assert isinstance(backend, GemmaBackend)
        assert backend.healthy()
    finally:
        del os.environ["DEPTHFUSION_RERANKER_BACKEND"]


def test_vps_gpu_mode_routes_all_llm_caps_to_gemma(monkeypatch):
    """vps-gpu default: reranker/extractor/linker/summariser/decision_extractor
    all resolve to GemmaBackend (embedding routes to local_embedding, which
    falls back to NullBackend until T-118 lands).
    """
    from depthfusion.backends.gemma import GemmaBackend
    from depthfusion.backends.null import NullBackend
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-gpu")
    for cap in ["reranker", "extractor", "linker", "summariser", "decision_extractor"]:
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)
        backend = get_backend(cap)
        assert isinstance(backend, GemmaBackend), (
            f"vps-gpu/{cap} did not route to GemmaBackend "
            f"(got {type(backend).__name__})"
        )
    # embedding still routes to local-backend stub (→ NullBackend until T-118)
    monkeypatch.delenv("DEPTHFUSION_EMBEDDING_BACKEND", raising=False)
    embedding_backend = get_backend("embedding")
    assert isinstance(embedding_backend, NullBackend)


def test_gemma_factory_uses_custom_url_from_env(monkeypatch):
    """The factory instantiates GemmaBackend with default __init__ args,
    which then reads DEPTHFUSION_GEMMA_URL from env. Verifies the
    pipeline from env → factory → GemmaBackend is intact.
    """
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-gpu")
    monkeypatch.setenv("DEPTHFUSION_GEMMA_URL", "http://gex44.test:8000/v1")
    monkeypatch.delenv("DEPTHFUSION_RERANKER_BACKEND", raising=False)
    backend = get_backend("reranker")
    assert backend._url == "http://gex44.test:8000/v1"
