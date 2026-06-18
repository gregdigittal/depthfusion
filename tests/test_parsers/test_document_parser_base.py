"""Tests for depthfusion.parsers.documents.base (T-590 + T-591).

Covers:
  - QuarantineStore CRUD and query operations
  - Retry-eligibility filtering (list_retryable)
  - record_retry_failure: increment, clear next_retry_at when exhausted
  - remove() return values
  - exhausted() list
  - Backward-compat module-level quarantine() / get_quarantine() helpers
"""
from __future__ import annotations

import pytest

from depthfusion.parsers.documents.base import (
    QuarantineEntry,
    QuarantineStore,
    get_quarantine,
    get_quarantine_store,
    quarantine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    source_id: str = "doc-001",
    *,
    retry_count: int = 0,
    max_retries: int = 3,
    next_retry_at: str = "",
) -> QuarantineEntry:
    return QuarantineEntry(
        source_id=source_id,
        error_message="test error",
        timestamp="2026-06-11T09:00:00Z",
        raw_size_bytes=1024,
        retry_count=retry_count,
        max_retries=max_retries,
        next_retry_at=next_retry_at,
    )


# ---------------------------------------------------------------------------
# test_quarantine_store_add_and_get
# ---------------------------------------------------------------------------

def test_quarantine_store_add_and_get() -> None:
    store = QuarantineStore()
    entry = _make_entry("doc-add")

    store.add(entry)

    retrieved = store.get("doc-add")
    assert retrieved is not None
    assert retrieved.source_id == "doc-add"
    assert retrieved.error_message == "test error"
    assert retrieved.raw_size_bytes == 1024

    # Unknown source_id returns None
    assert store.get("does-not-exist") is None

    # Upsert: adding a second entry with same id replaces the first
    replacement = _make_entry("doc-add", max_retries=5)
    store.add(replacement)
    assert store.get("doc-add").max_retries == 5  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# test_quarantine_store_list_retryable_filters_exhausted
# ---------------------------------------------------------------------------

def test_quarantine_store_list_retryable_filters_exhausted() -> None:
    store = QuarantineStore()

    exhausted_entry = _make_entry("doc-exhausted", retry_count=3, max_retries=3)
    active_entry = _make_entry("doc-active", retry_count=1, max_retries=3)

    store.add(exhausted_entry)
    store.add(active_entry)

    retryable = store.list_retryable("2026-06-11T10:00:00Z")
    ids = {e.source_id for e in retryable}

    assert "doc-active" in ids
    assert "doc-exhausted" not in ids


# ---------------------------------------------------------------------------
# test_quarantine_store_list_retryable_filters_not_yet_due
# ---------------------------------------------------------------------------

def test_quarantine_store_list_retryable_filters_not_yet_due() -> None:
    store = QuarantineStore()

    # next_retry_at is in the future relative to now_iso
    future_entry = _make_entry(
        "doc-future",
        retry_count=1,
        next_retry_at="2026-06-11T12:00:00Z",
    )
    # next_retry_at is in the past → eligible
    past_entry = _make_entry(
        "doc-past",
        retry_count=1,
        next_retry_at="2026-06-11T08:00:00Z",
    )
    # next_retry_at is empty → always eligible (if not exhausted)
    no_schedule_entry = _make_entry(
        "doc-no-schedule",
        retry_count=0,
        next_retry_at="",
    )

    store.add(future_entry)
    store.add(past_entry)
    store.add(no_schedule_entry)

    now = "2026-06-11T10:00:00Z"
    retryable = store.list_retryable(now)
    ids = {e.source_id for e in retryable}

    assert "doc-future" not in ids
    assert "doc-past" in ids
    assert "doc-no-schedule" in ids


# ---------------------------------------------------------------------------
# test_quarantine_store_record_retry_failure_increments_count
# ---------------------------------------------------------------------------

def test_quarantine_store_record_retry_failure_increments_count() -> None:
    store = QuarantineStore()
    entry = _make_entry("doc-retry", retry_count=0, max_retries=3)
    store.add(entry)

    store.record_retry_failure("doc-retry", "connection reset", "2026-06-11T11:00:00Z")

    updated = store.get("doc-retry")
    assert updated is not None
    assert updated.retry_count == 1
    assert updated.last_error == "connection reset"
    assert updated.next_retry_at == "2026-06-11T11:00:00Z"

    # Second failure
    store.record_retry_failure("doc-retry", "timeout", "2026-06-11T12:00:00Z")
    updated2 = store.get("doc-retry")
    assert updated2 is not None
    assert updated2.retry_count == 2
    assert updated2.last_error == "timeout"

    # Silently ignores unknown source_id
    store.record_retry_failure("unknown-id", "err", "2026-06-11T13:00:00Z")


# ---------------------------------------------------------------------------
# test_quarantine_store_record_retry_failure_clears_next_retry_when_exhausted
# ---------------------------------------------------------------------------

def test_quarantine_store_record_retry_failure_clears_next_retry_when_exhausted() -> None:
    store = QuarantineStore()
    # Two retries done, one remaining
    entry = _make_entry("doc-last-chance", retry_count=2, max_retries=3)
    store.add(entry)

    # This failure tips it over the limit
    store.record_retry_failure(
        "doc-last-chance",
        "disk full",
        "2026-06-11T14:00:00Z",  # would-be next retry (should be cleared)
    )

    updated = store.get("doc-last-chance")
    assert updated is not None
    assert updated.retry_count == 3
    assert updated.retry_count >= updated.max_retries  # exhausted
    # next_retry_at must be cleared when exhausted
    assert updated.next_retry_at == ""
    assert updated.last_error == "disk full"


# ---------------------------------------------------------------------------
# test_quarantine_store_remove
# ---------------------------------------------------------------------------

def test_quarantine_store_remove() -> None:
    store = QuarantineStore()
    store.add(_make_entry("doc-remove"))

    # Removing an existing entry returns True and actually removes it
    assert store.remove("doc-remove") is True
    assert store.get("doc-remove") is None

    # Removing a non-existent entry returns False
    assert store.remove("doc-remove") is False
    assert store.remove("never-existed") is False


# ---------------------------------------------------------------------------
# test_quarantine_store_exhausted_list
# ---------------------------------------------------------------------------

def test_quarantine_store_exhausted_list() -> None:
    store = QuarantineStore()

    store.add(_make_entry("doc-ok", retry_count=1, max_retries=3))
    store.add(_make_entry("doc-ex1", retry_count=3, max_retries=3))
    store.add(_make_entry("doc-ex2", retry_count=5, max_retries=3))  # over limit

    exhausted = store.exhausted()
    ids = {e.source_id for e in exhausted}

    assert "doc-ok" not in ids
    assert "doc-ex1" in ids
    assert "doc-ex2" in ids


# ---------------------------------------------------------------------------
# test_backward_compat_quarantine_fn
# ---------------------------------------------------------------------------

def test_backward_compat_quarantine_fn() -> None:
    """quarantine() and get_quarantine() must delegate to the default store."""
    # Use a fresh store to avoid cross-test contamination
    from depthfusion.parsers.documents import base as _base

    original_store = _base._default_quarantine_store
    _base._default_quarantine_store = QuarantineStore()

    try:
        entry = QuarantineEntry(
            source_id="compat-doc",
            error_message="compat test",
            timestamp="2026-06-11T09:30:00Z",
            raw_size_bytes=512,
        )

        quarantine(entry)

        all_entries = get_quarantine()
        ids = {e.source_id for e in all_entries}
        assert "compat-doc" in ids

        # get_quarantine_store() returns the same object used by quarantine()
        assert get_quarantine_store().get("compat-doc") is not None
    finally:
        # Restore the original singleton so other tests are not affected
        _base._default_quarantine_store = original_store
