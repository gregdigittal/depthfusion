"""T-610: SharePoint delta E2E test — delta-only sync verification.

Scenario
--------
1. Initial full crawl via ``sync_incremental`` (no stored token).
   The drive contains three files: A, B, C.
2. The cursor store persists the token for the drive.
3. Second ``sync_incremental`` call uses the stored token (``"latest"``).
   The mocked Graph delta response contains:
   - item A — changed (updated content)
   - item B — deleted
   - item C — **absent** (unchanged; Graph omits it from delta responses)
4. Assertions:
   - Second sync yields exactly item A (the changed file).
   - item B is a delete event (no ``ParsedDocument`` produced because
     ``_list_drive_items`` filters out deleted items before ``_process_item``
     is called — the connector currently skips deletes at the listing stage).
   - item C is NOT yielded (unchanged, absent from the delta response).
   - The delta token in the cursor store round-trips correctly between the
     two calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from depthfusion.connectors.sharepoint import SharePointConnector
from depthfusion.connectors.sharepoint_state import DeltaCursorStore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_DRIVE_ID = "drive-delta-e2e"
_SITE_URL = "https://contoso.sharepoint.com/sites/Delta"
_TENANT = "tenant-delta-e2e"
_CLIENT_ID = "client-delta-e2e"
_CLIENT_SECRET = "secret-delta-e2e"

# Item IDs used in the scenario
_ITEM_A = "item-A-changed"   # present in both crawls; content changes on second
_ITEM_B = "item-B-deleted"  # present in first crawl; deleted before second
_ITEM_C = "item-C-unchanged"  # present in first crawl; unchanged (absent from delta)

# The token stored after first sync
_STORED_TOKEN = "latest"
# The delta URL that the second sync will call
_DELTA_URL_SECOND = f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta?$token={_STORED_TOKEN}"
# The full-crawl URL (no token)
_DELTA_URL_FULL = f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta"
# Permissions URL prefix
_PERMS_URL_PREFIX = f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/items"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _txt_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def _file_item(item_id: str, name: str, download_url: str) -> dict[str, Any]:
    """Build a minimal Graph drive-item dict for a text/plain file."""
    return {
        "id": item_id,
        "name": name,
        "file": {"mimeType": "text/plain"},
        "webUrl": f"https://contoso.sharepoint.com/Shared/{name}",
        "createdDateTime": "2026-01-01T00:00:00Z",
        "lastModifiedDateTime": "2026-06-01T00:00:00Z",
        "createdBy": {"user": {"displayName": "Alice"}},
        "@microsoft.graph.downloadUrl": download_url,
    }


def _deleted_item(item_id: str) -> dict[str, Any]:
    """Build a Graph delta item that represents a deletion."""
    return {
        "id": item_id,
        "deleted": {"state": "deleted"},
    }


def _delta_response(items: list[dict[str, Any]], delta_token: str) -> dict[str, Any]:
    return {
        "value": items,
        "@odata.deltaLink": (
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta?$token={delta_token}"
        ),
    }


def _permissions_response(principals: list[str]) -> dict[str, Any]:
    return {
        "value": [
            {
                "roles": ["read"],
                "grantedTo": {"user": {"id": p, "email": f"{p}@corp.com"}},
            }
            for p in principals
        ]
    }


# ---------------------------------------------------------------------------
# Fake session: supports call-count–aware responses
# ---------------------------------------------------------------------------

class _MultiCallSession:
    """Fake requests.Session that can return different bodies per call count.

    For URLs registered with ``register_sequence``, successive calls to
    ``get(url)`` cycle through the provided responses in order.  For URLs
    registered with ``register``, the same response is returned every time.
    """

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self._static: dict[str, tuple[int, Any]] = {}
        self._sequences: dict[str, list[tuple[int, Any]]] = {}
        self._call_counts: dict[str, int] = {}

    def register(self, url: str, body: Any, status: int = 200) -> None:
        """Register a static response for *url*."""
        self._static[url] = (status, body)

    def register_bytes(self, url: str, content: bytes, status: int = 200) -> None:
        """Register a static bytes response for *url*."""
        self._static[url] = (status, content)

    def register_sequence(self, url: str, responses: list[tuple[int, Any]]) -> None:
        """Register an ordered sequence of (status, body) responses for *url*.

        The first call returns ``responses[0]``, the second ``responses[1]``,
        and so on.  After the last entry is exhausted all further calls return
        the last entry.
        """
        self._sequences[url] = list(responses)
        self._call_counts[url] = 0

    def get(self, url: str, **kwargs: Any) -> MagicMock:
        resp = MagicMock()

        # Sequence-aware lookup
        if url in self._sequences:
            idx = self._call_counts[url]
            seq = self._sequences[url]
            status, body = seq[min(idx, len(seq) - 1)]
            self._call_counts[url] = idx + 1
        elif url in self._static:
            status, body = self._static[url]
        else:
            status, body = 404, {"error": "Not Found"}

        resp.status_code = status
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

        return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_cursor_store(tmp_path: Path) -> DeltaCursorStore:
    """A DeltaCursorStore backed by a temp file (isolated per test)."""
    return DeltaCursorStore(store_path=tmp_path / "cursors.json")


def _make_connector(
    session: _MultiCallSession,
    cursor_store: DeltaCursorStore,
) -> SharePointConnector:
    """Return a connector with injected session and cursor store."""
    conn = SharePointConnector(
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        tenant_id=_TENANT,
        requests_session=session,
    )
    conn._access_token = "fake-bearer-token"
    conn._cursor_store = cursor_store
    return conn


# ---------------------------------------------------------------------------
# T-610: Delta E2E test
# ---------------------------------------------------------------------------

class TestDeltaE2E:
    """E2E test verifying that incremental delta sync yields only changed items.

    Two-phase scenario:
    - Phase 1 (full crawl): items A, B, C are returned.
    - Phase 2 (delta sync): only A (changed) and B (deleted) appear in the
      Graph delta response; C is absent (unchanged).

    Assertions:
    - Phase 1 yields 3 ParsedDocuments (A, B, C).
    - Phase 2 yields exactly 1 ParsedDocument (A — the changed item).
    - B is a deleted item → filtered out by the connector before parsing.
    - C is absent from the delta response → not re-ingested.
    - The delta token persists through the cursor store between calls.
    """

    def _build_session(self) -> _MultiCallSession:
        """Register all mocked Graph URLs for the two-phase scenario."""
        session = _MultiCallSession()

        # --- Download URLs ---
        dl_a_v1 = "https://dl.example.com/a-v1.txt"
        dl_a_v2 = "https://dl.example.com/a-v2.txt"
        dl_b = "https://dl.example.com/b.txt"
        dl_c = "https://dl.example.com/c.txt"

        session.register_bytes(dl_a_v1, _txt_bytes("Item A original content."))
        session.register_bytes(dl_a_v2, _txt_bytes("Item A UPDATED content."))
        session.register_bytes(dl_b, _txt_bytes("Item B content."))
        session.register_bytes(dl_c, _txt_bytes("Item C content."))

        # --- Permissions (static — same for all calls) ---
        for item_id in (_ITEM_A, _ITEM_B, _ITEM_C):
            session.register(
                f"{_PERMS_URL_PREFIX}/{item_id}/permissions",
                _permissions_response(["user-alpha"]),
            )

        # --- Phase 1: full crawl (URL without token) ---
        full_items = [
            _file_item(_ITEM_A, "a.txt", dl_a_v1),
            _file_item(_ITEM_B, "b.txt", dl_b),
            _file_item(_ITEM_C, "c.txt", dl_c),
        ]
        full_response = _delta_response(full_items, delta_token="tok-after-full")

        # --- Phase 2: delta response (URL with ?$token=latest) ---
        # Graph returns only changed (A with new downloadUrl) and deleted (B).
        # C is absent — the delta API only returns changed/deleted items.
        delta_items = [
            _file_item(_ITEM_A, "a.txt", dl_a_v2),  # changed
            _deleted_item(_ITEM_B),                  # deleted
        ]
        delta_response = _delta_response(delta_items, delta_token="tok-after-delta")

        # Register delta URL as a sequence: first call → full crawl; second → delta
        session.register_sequence(
            _DELTA_URL_FULL,
            [(200, full_response), (200, full_response)],  # shouldn't be called twice
        )
        session.register(_DELTA_URL_SECOND, delta_response)

        return session

    def test_delta_sync_yields_only_changed_items(
        self,
        tmp_cursor_store: DeltaCursorStore,
    ) -> None:
        """Phase 1 returns all items; Phase 2 returns only the changed item."""
        session = self._build_session()
        conn = _make_connector(session, tmp_cursor_store)

        cursor_key = f"{_TENANT}:{_SITE_URL}:{_DRIVE_ID}"

        # ------------------------------------------------------------------ #
        # Phase 1: full crawl (no stored delta token)
        # ------------------------------------------------------------------ #
        assert tmp_cursor_store.get_delta_token(cursor_key) is None, (
            "No token should exist before the first sync"
        )

        docs_phase1, token_after_first = conn.sync_incremental(
            site_url=_SITE_URL,
            drive_id=_DRIVE_ID,
        )

        # All three items should be ingested on full crawl
        assert len(docs_phase1) == 3, (
            f"Expected 3 docs from full crawl, got {len(docs_phase1)}: "
            f"{[d.source_id for d in docs_phase1]}"
        )
        source_ids_phase1 = {d.source_id for d in docs_phase1}
        assert f"sharepoint:{_DRIVE_ID}:{_ITEM_A}" in source_ids_phase1
        assert f"sharepoint:{_DRIVE_ID}:{_ITEM_B}" in source_ids_phase1
        assert f"sharepoint:{_DRIVE_ID}:{_ITEM_C}" in source_ids_phase1

        # Token is persisted after the first sync
        stored_token = tmp_cursor_store.get_delta_token(cursor_key)
        assert stored_token is not None, (
            "DeltaCursorStore must persist a token after the first sync"
        )
        assert stored_token == _STORED_TOKEN, (
            f"Expected stored token '{_STORED_TOKEN}', got '{stored_token}'"
        )

        # ------------------------------------------------------------------ #
        # Phase 2: delta sync — uses the persisted token
        # ------------------------------------------------------------------ #
        docs_phase2, token_after_second = conn.sync_incremental(
            site_url=_SITE_URL,
            drive_id=_DRIVE_ID,
        )

        # Only item A (changed) should be returned.
        # Item B is deleted → filtered by _list_drive_items (not parsed).
        # Item C is absent from the delta response → not yielded.
        assert len(docs_phase2) == 1, (
            f"Expected exactly 1 doc from delta sync (changed item A only), "
            f"got {len(docs_phase2)}: {[d.source_id for d in docs_phase2]}"
        )

        returned_doc = docs_phase2[0]
        assert returned_doc.source_id == f"sharepoint:{_DRIVE_ID}:{_ITEM_A}", (
            f"Expected changed item A in delta sync result, got {returned_doc.source_id!r}"
        )

        # Verify C (unchanged) was NOT re-ingested
        source_ids_phase2 = {d.source_id for d in docs_phase2}
        assert f"sharepoint:{_DRIVE_ID}:{_ITEM_C}" not in source_ids_phase2, (
            "Unchanged item C must not be re-ingested in the delta sync"
        )

        # Verify B (deleted) was NOT returned as a ParsedDocument
        assert f"sharepoint:{_DRIVE_ID}:{_ITEM_B}" not in source_ids_phase2, (
            "Deleted item B must not appear as a ParsedDocument in the delta sync"
        )

        # Token is updated after the second sync too
        stored_token_after_second = tmp_cursor_store.get_delta_token(cursor_key)
        assert stored_token_after_second is not None, (
            "DeltaCursorStore must update the token after the second sync"
        )

    def test_delta_token_persists_through_cursor_store(
        self,
        tmp_cursor_store: DeltaCursorStore,
    ) -> None:
        """Verify the delta token written by sync_incremental round-trips through the store.

        Uses a separate DeltaCursorStore instance (same backing file) to
        confirm the token is actually on disk, not just in memory.
        """
        session = self._build_session()
        conn = _make_connector(session, tmp_cursor_store)

        cursor_key = f"{_TENANT}:{_SITE_URL}:{_DRIVE_ID}"

        # First sync persists the token
        conn.sync_incremental(site_url=_SITE_URL, drive_id=_DRIVE_ID)

        # Open a fresh DeltaCursorStore pointing at the same file
        fresh_store = DeltaCursorStore(store_path=tmp_cursor_store._path)
        token_on_disk = fresh_store.get_delta_token(cursor_key)

        assert token_on_disk == _STORED_TOKEN, (
            f"Token must survive a round-trip through the JSON backing file. "
            f"Expected '{_STORED_TOKEN}', got '{token_on_disk!r}'"
        )

    def test_unchanged_items_absent_from_delta_response_not_re_ingested(
        self,
        tmp_cursor_store: DeltaCursorStore,
    ) -> None:
        """Negative case: item C must not appear in the second sync results."""
        session = self._build_session()
        conn = _make_connector(session, tmp_cursor_store)

        # Full crawl
        conn.sync_incremental(site_url=_SITE_URL, drive_id=_DRIVE_ID)

        # Delta sync
        docs_phase2, _ = conn.sync_incremental(site_url=_SITE_URL, drive_id=_DRIVE_ID)

        source_ids = {d.source_id for d in docs_phase2}
        assert f"sharepoint:{_DRIVE_ID}:{_ITEM_C}" not in source_ids, (
            "Item C was not in the delta response (unchanged) and must not be "
            f"re-ingested; actual second-sync docs: {list(source_ids)}"
        )
