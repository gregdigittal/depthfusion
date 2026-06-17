"""SharePoint delta E2E test — T-610 (E-54).

Verifies that incremental (delta) sync via ``SharePointConnector.sync_incremental``
pulls only changed items since the last sync token, processes deletions correctly,
and round-trips the delta token through :class:`DeltaCursorStore` between syncs.

Scenario
--------
Drive state before first sync:
  - item-A  (text/plain)  — will remain unchanged
  - item-B  (text/plain)  — will be modified between syncs
  - item-C  (text/plain)  — will be deleted before the second sync

First sync (full crawl, no stored token):
  Graph returns item-A, item-B, item-C as present files.
  Response includes ``@odata.deltaLink`` with token ``delta-token-1``.

Second sync (incremental, token = ``delta-token-1``):
  Graph returns only item-B (changed) + item-C (deleted).
  item-A is absent because it was not changed.

Assertions
----------
1. First sync yields docs for item-A, item-B, item-C.
2. Delta token ``delta-token-1`` is stored in the cursor store after first sync.
3. Second sync uses the stored token in its Graph URL.
4. Second sync yields exactly item-B (changed); item-A is NOT yielded again.
5. item-C is reported as a deleted ID.
6. A new delta token ``delta-token-2`` is stored after the second sync.

No network calls, no live credentials.  All Graph responses are injected via
a ``_FakeSession`` object similar to the one used in ``test_sharepoint.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from depthfusion.connectors.sharepoint import SharePointConnector
from depthfusion.connectors.sharepoint_state import DeltaCursorStore
from depthfusion.ingest.models import ParsedDocument

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_DRIVE_ID = "drive-delta-test"
_SITE_URL = "https://contoso.sharepoint.com/sites/DeltaTest"
_TENANT = "tenant-delta"
_CLIENT_ID = "client-delta"
_CLIENT_SECRET = "secret-delta"

# Item IDs
_ITEM_A = "item-unchanged-A"
_ITEM_B = "item-changed-B"
_ITEM_C = "item-deleted-C"

# Delta tokens
_DELTA_TOKEN_1 = "delta-token-1"
_DELTA_TOKEN_2 = "delta-token-2"

# The cursor key that sync_incremental builds internally
_CURSOR_KEY = f"{_TENANT}:{_SITE_URL}:{_DRIVE_ID}"

# Graph URL patterns
_FULL_CRAWL_URL = f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta"
_DELTA_URL_1 = f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta?$token={_DELTA_TOKEN_1}"
_DELTA_LINK_1 = f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta?$token={_DELTA_TOKEN_1}"
_DELTA_LINK_2 = f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta?$token={_DELTA_TOKEN_2}"

# Permissions endpoint for any item
def _perms_url(item_id: str) -> str:
    return f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/items/{item_id}/permissions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _txt_item(item_id: str, name: str, download_url: str) -> dict[str, Any]:
    """Build a minimal Graph drive-item dict for a plain-text file."""
    return {
        "id": item_id,
        "name": name,
        "file": {"mimeType": "text/plain"},
        "webUrl": f"https://contoso.sharepoint.com/Shared/{name}",
        "createdDateTime": "2026-01-01T00:00:00Z",
        "lastModifiedDateTime": "2026-06-01T00:00:00Z",
        "createdBy": {"user": {"displayName": "Delta Tester"}},
        "@microsoft.graph.downloadUrl": download_url,
    }


def _deleted_item(item_id: str) -> dict[str, Any]:
    """Build a minimal Graph delta item representing a deletion."""
    return {
        "id": item_id,
        "deleted": {"state": "deleted"},
    }


def _delta_response(
    items: list[dict[str, Any]],
    delta_link: str,
) -> dict[str, Any]:
    """Build a Graph delta response with the given items and a deltaLink."""
    return {
        "value": items,
        "@odata.deltaLink": delta_link,
    }


class _FakeSession:
    """Minimal requests-session-compatible object backed by a dict of URLs.

    Supports registering both JSON responses and raw bytes (for file downloads).
    """

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self._responses: dict[str, Any] = {}
        # Track which URLs were requested and how many times
        self.request_log: list[str] = []

    def register(self, url: str, json_body: Any, status: int = 200) -> None:
        self._responses[url] = (status, json_body)

    def register_bytes(self, url: str, content: bytes, status: int = 200) -> None:
        self._responses[url] = (status, content)

    def get(self, url: str, **kwargs: Any) -> MagicMock:
        self.request_log.append(url)
        resp = MagicMock()
        if url in self._responses:
            status, body = self._responses[url]
            resp.status_code = status
            resp.headers = {}
            if isinstance(body, bytes):
                resp.content = body
                resp.json.side_effect = ValueError("not JSON")
            else:
                resp.json.return_value = body
                resp.content = json.dumps(body).encode()
            if status >= 400:
                resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
            else:
                resp.raise_for_status.return_value = None
        else:
            resp.status_code = 404
            resp.headers = {}
            resp.raise_for_status.side_effect = Exception(f"404 Not Found: {url!r}")
        return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def cursor_store(tmp_path: Path) -> DeltaCursorStore:
    """Isolated DeltaCursorStore backed by a throw-away JSON file."""
    store_path = tmp_path / "delta_cursors.json"
    return DeltaCursorStore(store_path=store_path)


@pytest.fixture()
def download_url_a() -> str:
    return "https://download.example.com/file-a.txt"


@pytest.fixture()
def download_url_b() -> str:
    return "https://download.example.com/file-b.txt"


@pytest.fixture()
def first_sync_session(download_url_a: str, download_url_b: str) -> _FakeSession:
    """Session that serves the full-crawl response (first sync).

    Items returned: A (unchanged), B (will change), C (will be deleted).
    No download URL for C (it will be deleted before second sync; but
    we serve it here to confirm it was seen in the full crawl).
    """
    session = _FakeSession()

    # Full-crawl delta endpoint (no token)
    session.register(
        _FULL_CRAWL_URL,
        _delta_response(
            [
                _txt_item(_ITEM_A, "file-a.txt", download_url_a),
                _txt_item(_ITEM_B, "file-b.txt", download_url_b),
                _txt_item(_ITEM_C, "file-c.txt", "https://download.example.com/file-c.txt"),
            ],
            delta_link=_DELTA_LINK_1,
        ),
    )

    # File content downloads
    session.register_bytes(download_url_a, b"Unchanged content of file A.")
    session.register_bytes(download_url_b, b"Original content of file B.")
    session.register_bytes(
        "https://download.example.com/file-c.txt", b"Content of file C (will be deleted)."
    )

    # Permissions endpoint (empty = internal)
    empty_perms = {"value": []}
    session.register(_perms_url(_ITEM_A), empty_perms)
    session.register(_perms_url(_ITEM_B), empty_perms)
    session.register(_perms_url(_ITEM_C), empty_perms)

    return session


@pytest.fixture()
def second_sync_session(download_url_b: str) -> _FakeSession:
    """Session that serves the delta response for the second (incremental) sync.

    Uses token ``_DELTA_TOKEN_1`` in the URL.
    Returns: item-B (changed) + item-C (deleted).
    item-A is NOT in this response (unchanged — Graph omits it).
    """
    session = _FakeSession()

    # Incremental delta endpoint (uses token from first sync)
    session.register(
        _DELTA_URL_1,
        _delta_response(
            [
                _txt_item(_ITEM_B, "file-b.txt", download_url_b),
                _deleted_item(_ITEM_C),
            ],
            delta_link=_DELTA_LINK_2,
        ),
    )

    # Updated content for file B
    session.register_bytes(download_url_b, b"Updated content of file B - this is the change.")

    # Permissions for B (unchanged)
    session.register(_perms_url(_ITEM_B), {"value": []})

    return session


def _make_connector(
    session: _FakeSession,
    cursor_store: DeltaCursorStore,
) -> SharePointConnector:
    """Return a SharePointConnector with injected session and cursor store."""
    conn = SharePointConnector(
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        tenant_id=_TENANT,
        requests_session=session,
    )
    # Bypass MSAL
    conn._access_token = "fake-bearer-token"
    # Inject isolated cursor store so no real filesystem is touched
    conn._cursor_store = cursor_store
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSharePointDeltaE2E:
    """E2E delta-sync tests (T-610).

    Each test uses the two-session fixture pattern:
      1. ``first_sync_session``  → full crawl response
      2. ``second_sync_session`` → incremental delta response

    Between the two syncs the cursor store persists the delta token so the
    second connector instance (sharing the same store) picks up where the
    first left off.
    """

    def test_first_sync_yields_all_items(
        self,
        first_sync_session: _FakeSession,
        cursor_store: DeltaCursorStore,
    ) -> None:
        """Full crawl returns documents for all three items (A, B, C)."""
        conn = _make_connector(first_sync_session, cursor_store)
        docs, deleted_ids, new_token = conn.sync_incremental(
            site_url=_SITE_URL, drive_id=_DRIVE_ID
        )

        source_ids = {d.source_id for d in docs}
        assert f"sharepoint:{_DRIVE_ID}:{_ITEM_A}" in source_ids
        assert f"sharepoint:{_DRIVE_ID}:{_ITEM_B}" in source_ids
        assert f"sharepoint:{_DRIVE_ID}:{_ITEM_C}" in source_ids

        # No deletions in the full crawl
        assert deleted_ids == [], f"Expected no deletes in full crawl; got {deleted_ids}"

        # All three docs are ParsedDocument instances
        for doc in docs:
            assert isinstance(doc, ParsedDocument)

    def test_delta_token_stored_after_first_sync(
        self,
        first_sync_session: _FakeSession,
        cursor_store: DeltaCursorStore,
    ) -> None:
        """After the first sync the cursor store must hold the actual delta token."""
        conn = _make_connector(first_sync_session, cursor_store)
        _, _, new_token = conn.sync_incremental(site_url=_SITE_URL, drive_id=_DRIVE_ID)

        # The token returned must be the one extracted from the @odata.deltaLink
        assert new_token == _DELTA_TOKEN_1, (
            f"Expected new_token={_DELTA_TOKEN_1!r}; got {new_token!r}"
        )

        # The cursor store must persist the same token under the correct key
        stored_token = cursor_store.get_delta_token(_CURSOR_KEY)
        assert stored_token == _DELTA_TOKEN_1, (
            f"Cursor store must hold {_DELTA_TOKEN_1!r} after first sync; "
            f"got {stored_token!r}"
        )

    def test_second_sync_uses_stored_token_in_url(
        self,
        first_sync_session: _FakeSession,
        second_sync_session: _FakeSession,
        cursor_store: DeltaCursorStore,
    ) -> None:
        """Second sync must send a request to the delta URL with the stored token."""
        # First sync: populate cursor store
        conn1 = _make_connector(first_sync_session, cursor_store)
        conn1.sync_incremental(site_url=_SITE_URL, drive_id=_DRIVE_ID)

        # Confirm token was stored
        assert cursor_store.get_delta_token(_CURSOR_KEY) == _DELTA_TOKEN_1

        # Second sync: must use the stored token in the Graph request URL
        conn2 = _make_connector(second_sync_session, cursor_store)
        conn2.sync_incremental(site_url=_SITE_URL, drive_id=_DRIVE_ID)

        # The incremental delta URL must have been requested
        assert _DELTA_URL_1 in second_sync_session.request_log, (
            f"Expected second sync to request {_DELTA_URL_1!r}; "
            f"actual requests: {second_sync_session.request_log}"
        )

    def test_second_sync_yields_only_changed_item(
        self,
        first_sync_session: _FakeSession,
        second_sync_session: _FakeSession,
        cursor_store: DeltaCursorStore,
    ) -> None:
        """Second (delta) sync yields only item-B (changed); item-A is NOT re-yielded.

        This is the core delta correctness assertion: the connector must not
        re-process unchanged items that Graph correctly omits from the delta.
        """
        # Seed the cursor store via the first sync
        conn1 = _make_connector(first_sync_session, cursor_store)
        conn1.sync_incremental(site_url=_SITE_URL, drive_id=_DRIVE_ID)

        # Run the second (incremental) sync
        conn2 = _make_connector(second_sync_session, cursor_store)
        docs, deleted_ids, new_token = conn2.sync_incremental(
            site_url=_SITE_URL, drive_id=_DRIVE_ID
        )

        source_ids = {d.source_id for d in docs}

        # item-B (changed) MUST appear in the second sync results
        assert f"sharepoint:{_DRIVE_ID}:{_ITEM_B}" in source_ids, (
            f"Changed item-B must be yielded by the second sync; got {source_ids}"
        )

        # item-A (unchanged) must NOT appear — Graph did not include it in delta
        assert f"sharepoint:{_DRIVE_ID}:{_ITEM_A}" not in source_ids, (
            f"Unchanged item-A must NOT be re-yielded by the delta sync; got {source_ids}"
        )

        # Only item-B should be in docs (item-C was deleted, not a file doc)
        assert len(docs) == 1, (
            f"Expected exactly 1 doc from delta sync (item-B only); got {len(docs)}: {source_ids}"
        )

    def test_second_sync_processes_deletion_correctly(
        self,
        first_sync_session: _FakeSession,
        second_sync_session: _FakeSession,
        cursor_store: DeltaCursorStore,
    ) -> None:
        """Second sync must report item-C as a deleted source ID."""
        # First sync seeds the cursor store
        conn1 = _make_connector(first_sync_session, cursor_store)
        conn1.sync_incremental(site_url=_SITE_URL, drive_id=_DRIVE_ID)

        # Second sync processes deletion
        conn2 = _make_connector(second_sync_session, cursor_store)
        docs, deleted_ids, new_token = conn2.sync_incremental(
            site_url=_SITE_URL, drive_id=_DRIVE_ID
        )

        expected_deleted_id = f"sharepoint:{_DRIVE_ID}:{_ITEM_C}"
        assert expected_deleted_id in deleted_ids, (
            f"Expected {expected_deleted_id!r} in deleted_ids; got {deleted_ids}"
        )

        # Deleted item must NOT appear in docs
        doc_source_ids = {d.source_id for d in docs}
        assert expected_deleted_id not in doc_source_ids, (
            f"Deleted item must not appear in docs; got {doc_source_ids}"
        )

    def test_delta_token_round_trips_between_syncs(
        self,
        first_sync_session: _FakeSession,
        second_sync_session: _FakeSession,
        cursor_store: DeltaCursorStore,
    ) -> None:
        """Delta token produced by sync 1 is stored and consumed by sync 2,
        which then stores the token from sync 2's response.

        This verifies the full token round-trip:
          initial (None) → [sync 1] → token-1 → [sync 2] → token-2
        """
        # Before any sync: no token stored
        assert cursor_store.get_delta_token(_CURSOR_KEY) is None, (
            "Cursor store must be empty before first sync"
        )

        # First sync: full crawl, stores token-1
        conn1 = _make_connector(first_sync_session, cursor_store)
        _, _, token_after_sync_1 = conn1.sync_incremental(
            site_url=_SITE_URL, drive_id=_DRIVE_ID
        )
        assert token_after_sync_1 == _DELTA_TOKEN_1
        assert cursor_store.get_delta_token(_CURSOR_KEY) == _DELTA_TOKEN_1

        # Second sync: incremental with token-1, stores token-2
        conn2 = _make_connector(second_sync_session, cursor_store)
        _, _, token_after_sync_2 = conn2.sync_incremental(
            site_url=_SITE_URL, drive_id=_DRIVE_ID
        )
        assert token_after_sync_2 == _DELTA_TOKEN_2, (
            f"Expected token-2={_DELTA_TOKEN_2!r} after second sync; got {token_after_sync_2!r}"
        )
        assert cursor_store.get_delta_token(_CURSOR_KEY) == _DELTA_TOKEN_2, (
            "Cursor store must hold token-2 after second sync"
        )

    def test_second_sync_changed_item_has_updated_content(
        self,
        first_sync_session: _FakeSession,
        second_sync_session: _FakeSession,
        cursor_store: DeltaCursorStore,
    ) -> None:
        """The changed item's ParsedDocument must contain the updated content bytes."""
        conn1 = _make_connector(first_sync_session, cursor_store)
        conn1.sync_incremental(site_url=_SITE_URL, drive_id=_DRIVE_ID)

        conn2 = _make_connector(second_sync_session, cursor_store)
        docs, _, _ = conn2.sync_incremental(site_url=_SITE_URL, drive_id=_DRIVE_ID)

        assert len(docs) == 1
        doc_b = docs[0]
        assert doc_b.source_id == f"sharepoint:{_DRIVE_ID}:{_ITEM_B}"
        # The second sync served updated bytes for item-B
        assert "Updated content" in doc_b.text, (
            f"Expected updated content in item-B doc; got: {doc_b.text!r}"
        )
