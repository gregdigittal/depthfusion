"""Tests for PrincipalStore — thread-safe SQLite backend for principals.

S-156 AC-3 coverage only.  Tests verify:
- upsert_principal creates a row; get_principal returns it with correct fields
- get_principal returns None for an unknown principal_id
- list_principals returns rows newest-first
- upsert_principal updates last_seen on a duplicate key (group refresh on login)
- thread safety: 10 concurrent upsert_principal calls produce no exception

No S-157/S-158 fields (device_id, roles) are tested here.
"""
from __future__ import annotations

import sqlite3
import threading
import time

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
    """upsert_principal followed by get_principal returns the same principal."""
    store = PrincipalStore(db_path=tmp_path / "identity.db")
    p = make_principal("sub-001")
    store.upsert_principal(p)

    result = store.get_principal("sub-001")
    assert result is not None
    assert result.principal_id == "sub-001"
    assert result.upn == "user@example.com"
    assert result.display_name == "Test User"
    assert result.groups == ["group-a", "group-b"]


def test_get_unknown_returns_none(tmp_path):
    """get_principal returns None when the principal_id is not in the store."""
    store = PrincipalStore(db_path=tmp_path / "identity.db")
    assert store.get_principal("does-not-exist") is None


def test_list_principals_returns_newest_first(tmp_path):
    """list_principals orders results by last_seen descending."""
    store = PrincipalStore(db_path=tmp_path / "identity.db")

    # Insert three principals with slight time gaps so last_seen differs.
    for i in range(3):
        store.upsert_principal(
            make_principal(f"sub-{i:03d}", upn=f"user{i}@example.com")
        )
        # Small sleep to ensure distinct last_seen timestamps.
        time.sleep(0.01)

    results = store.list_principals(limit=10)
    assert len(results) == 3
    # sub-002 was inserted last, so it should appear first.
    assert results[0].principal_id == "sub-002"
    assert results[1].principal_id == "sub-001"
    assert results[2].principal_id == "sub-000"


def test_upsert_updates_last_seen_on_duplicate(tmp_path):
    """A second upsert_principal for the same principal_id updates last_seen.

    This is the AC-3 group-refresh path: calling upsert_principal on every
    login must overwrite the stored groups and bump last_seen.
    """
    db_path = tmp_path / "identity.db"
    store = PrincipalStore(db_path=db_path)

    p = make_principal("sub-dup")
    store.upsert_principal(p)

    # Read the first last_seen directly from the DB.
    with sqlite3.connect(str(db_path)) as conn:
        first_seen = conn.execute(
            "SELECT last_seen FROM principals WHERE principal_id = ?", ("sub-dup",)
        ).fetchone()[0]

    time.sleep(0.05)  # Ensure time advances.

    store.upsert_principal(p)

    with sqlite3.connect(str(db_path)) as conn:
        second_seen = conn.execute(
            "SELECT last_seen FROM principals WHERE principal_id = ?", ("sub-dup",)
        ).fetchone()[0]

    assert second_seen > first_seen


def test_upsert_updates_groups_on_duplicate(tmp_path):
    """A second upsert_principal replaces stored groups (AC-3 group refresh)."""
    store = PrincipalStore(db_path=tmp_path / "identity.db")

    p = make_principal("sub-grp")
    store.upsert_principal(p)

    p_updated = Principal(
        principal_id="sub-grp",
        upn="user@example.com",
        display_name="Test User",
        groups=["new-group-x", "new-group-y"],
    )
    store.upsert_principal(p_updated)

    result = store.get_principal("sub-grp")
    assert result is not None
    assert result.groups == ["new-group-x", "new-group-y"]


def test_thread_safety(tmp_path):
    """10 threads calling upsert_principal concurrently must not raise."""
    store = PrincipalStore(db_path=tmp_path / "identity.db")
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            store.upsert_principal(
                make_principal(f"sub-t{idx:02d}", upn=f"t{idx}@example.com")
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Unexpected exceptions in threads: {errors}"
    # All 10 rows should be present.
    assert len(store.list_principals(limit=20)) == 10
