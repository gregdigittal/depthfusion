"""Tests for PrincipalStore — thread-safe SQLite backend for principals.

Covers:
- upsert creates a row; get returns it
- get returns None for an unknown principal_id
- list_recent returns rows newest-first
- upsert updates last_seen on a duplicate key
- thread safety: 10 concurrent upserts produce no exception
"""
from __future__ import annotations

import threading
import time

import pytest

from depthfusion.identity import Principal, PrincipalStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_principal(uid: str, upn: str = "user@example.com") -> Principal:
    return Principal(
        principal_id=uid,
        upn=upn,
        display_name="Test User",
        groups=["group-a", "group-b"],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_upsert_and_get(tmp_path):
    """upsert followed by get returns the same principal."""
    store = PrincipalStore(db_path=tmp_path / "identity.db")
    p = make_principal("sub-001")
    store.upsert(p)

    result = store.get("sub-001")
    assert result is not None
    assert result.principal_id == "sub-001"
    assert result.upn == "user@example.com"
    assert result.display_name == "Test User"
    assert result.groups == ["group-a", "group-b"]


def test_get_unknown_returns_none(tmp_path):
    """get returns None when the principal_id is not in the store."""
    store = PrincipalStore(db_path=tmp_path / "identity.db")
    assert store.get("does-not-exist") is None


def test_list_recent_returns_newest_first(tmp_path):
    """list_recent orders results by last_seen descending."""
    store = PrincipalStore(db_path=tmp_path / "identity.db")

    # Insert three principals with slight time gaps so last_seen differs.
    for i in range(3):
        store.upsert(make_principal(f"sub-{i:03d}", upn=f"user{i}@example.com"))
        # Small sleep to ensure distinct last_seen timestamps.
        time.sleep(0.01)

    results = store.list_recent(limit=10)
    assert len(results) == 3
    # sub-002 was inserted last, so it should appear first.
    assert results[0].principal_id == "sub-002"
    assert results[1].principal_id == "sub-001"
    assert results[2].principal_id == "sub-000"


def test_upsert_updates_last_seen_on_duplicate(tmp_path):
    """A second upsert for the same principal_id updates last_seen."""
    import sqlite3

    db_path = tmp_path / "identity.db"
    store = PrincipalStore(db_path=db_path)

    p = make_principal("sub-dup")
    store.upsert(p)

    # Read the first last_seen directly from the DB.
    with sqlite3.connect(str(db_path)) as conn:
        first_seen = conn.execute(
            "SELECT last_seen FROM principals WHERE principal_id = ?", ("sub-dup",)
        ).fetchone()[0]

    time.sleep(0.05)  # Ensure time advances.

    store.upsert(p)

    with sqlite3.connect(str(db_path)) as conn:
        second_seen = conn.execute(
            "SELECT last_seen FROM principals WHERE principal_id = ?", ("sub-dup",)
        ).fetchone()[0]

    assert second_seen > first_seen


def test_thread_safety(tmp_path):
    """10 threads upserting concurrently must not raise any exception."""
    store = PrincipalStore(db_path=tmp_path / "identity.db")
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            store.upsert(make_principal(f"sub-t{idx:02d}", upn=f"t{idx}@example.com"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Unexpected exceptions in threads: {errors}"
    # All 10 rows should be present.
    assert len(store.list_recent(limit=20)) == 10
