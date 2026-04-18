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
    Scaffolding: haiku→null fallthrough, so we only check it stays healthy.
    """
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    for cap in ["reranker", "extractor"]:
        monkeypatch.delenv(f"DEPTHFUSION_{cap.upper()}_BACKEND", raising=False)
    backend = get_backend("reranker")
    # vps→vps-cpu→haiku→null (scaffolding fallthrough)
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
