# tests/test_backends/test_base.py
"""Contract tests for the LLMBackend protocol + typed error hierarchy.

Backlog: T-115 AC-01-1, T-122.
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
from depthfusion.backends.null import NullBackend

# ── Protocol conformance ─────────────────────────────────────────────────


def test_null_backend_satisfies_runtime_protocol():
    """runtime_checkable `LLMBackend` must accept NullBackend as an
    instance — otherwise the whole factory contract is broken.
    """
    assert isinstance(NullBackend(), LLMBackend)


def test_protocol_exposes_all_six_methods():
    """Regression guard: the protocol shape is part of the public API.
    If anyone renames or drops a method, every backend implementation
    breaks silently at runtime — catch it at the protocol level.
    """
    required = {"complete", "embed", "rerank", "extract_structured", "healthy"}
    for method in required:
        assert hasattr(LLMBackend, method), f"Protocol missing method: {method}"


def test_backend_has_name_attribute():
    """Every backend declares a `.name` — used in audit records and
    fallback-chain telemetry.
    """
    b = NullBackend()
    assert hasattr(b, "name")
    assert isinstance(b.name, str)
    assert len(b.name) > 0


# ── Typed error hierarchy ────────────────────────────────────────────────


def test_rate_limit_error_is_exception():
    assert issubclass(RateLimitError, Exception)


def test_overload_error_is_exception():
    assert issubclass(BackendOverloadError, Exception)


def test_timeout_error_is_exception():
    assert issubclass(BackendTimeoutError, Exception)


def test_exhausted_error_is_exception():
    assert issubclass(BackendExhaustedError, Exception)


def test_all_four_error_classes_are_distinct():
    """If any two error classes accidentally alias, callers can't
    distinguish between a rate-limit event and a timeout.
    """
    classes = [
        RateLimitError,
        BackendOverloadError,
        BackendTimeoutError,
        BackendExhaustedError,
    ]
    assert len(set(classes)) == 4


def test_error_classes_can_be_raised_and_caught():
    """Smoke test: the classes are actually usable as exceptions."""
    with pytest.raises(RateLimitError):
        raise RateLimitError("test")
    with pytest.raises(BackendOverloadError):
        raise BackendOverloadError("test")
    with pytest.raises(BackendTimeoutError):
        raise BackendTimeoutError("test")


def test_exhausted_error_carries_chain_attribute():
    """BackendExhaustedError must carry the list of attempted backends
    so callers / audit logs see the full fallback path.
    """
    err = BackendExhaustedError(["gemma", "haiku", "null"])
    assert err.chain == ["gemma", "haiku", "null"]
    assert "gemma" in str(err)


def test_exhausted_error_custom_message():
    err = BackendExhaustedError(["haiku"], message="Custom message")
    assert "Custom message" in str(err)
