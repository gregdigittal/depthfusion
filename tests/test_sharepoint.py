"""Tests for the SharePoint connector (E-54).

All Microsoft Graph API calls are mocked using the ``responses`` library
so no live credentials or network access is required.
"""
from __future__ import annotations

import io
import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import responses as responses_lib

from depthfusion.connectors.sharepoint import (
    ConfigurationError,
    SharePointConnector,
)
from depthfusion.ingest.models import ParsedDocument
from depthfusion.ingest.pipeline import IngestPipeline


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DRIVE_ID = "drive-abc-123"
_ITEM_ID_DOCX = "item-docx-001"
_ITEM_ID_PDF = "item-pdf-002"
_ITEM_ID_TXT = "item-txt-003"
_ITEM_ID_UNSUPPORTED = "item-exe-004"

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TENANT = "tenant-xyz"
_CLIENT_ID = "client-abc"
_CLIENT_SECRET = "super-secret"

_TOKEN_URL = f"https://login.microsoftonline.com/{_TENANT}/oauth2/v2.0/token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_docx_bytes() -> bytes:
    """Return minimal .docx bytes."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("SharePoint document content for testing.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_txt_bytes(text: str = "Plain text from SharePoint.") -> bytes:
    return text.encode("utf-8")


def _drive_delta_response(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a Graph delta response payload."""
    return {
        "value": items,
        "@odata.nextLink": None,
        "@odata.deltaLink": f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta?$token=next-delta-xyz",
    }


def _file_item(
    item_id: str,
    name: str,
    mime_type: str,
    download_url: str = "",
    sensitivity: str = "",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": item_id,
        "name": name,
        "file": {"mimeType": mime_type},
        "webUrl": f"https://contoso.sharepoint.com/Shared/{name}",
        "createdDateTime": "2026-01-01T00:00:00Z",
        "lastModifiedDateTime": "2026-06-01T00:00:00Z",
        "createdBy": {"user": {"displayName": "Alice"}},
    }
    if download_url:
        item["@microsoft.graph.downloadUrl"] = download_url
    if sensitivity:
        item["sensitivityLabel"] = {"displayName": sensitivity}
    return item


def _permissions_response(principals: list[str]) -> dict[str, Any]:
    perms = []
    for principal in principals:
        perms.append(
            {
                "roles": ["read"],
                "grantedTo": {
                    "user": {"id": principal, "email": f"{principal}@corp.com"}
                },
            }
        )
    return {"value": perms}


# ---------------------------------------------------------------------------
# Fixture: pre-configured connector with injected requests session
# ---------------------------------------------------------------------------

class _FakeSession:
    """A minimal requests-session-compatible object backed by a dict of URLs."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self._responses: dict[str, Any] = {}

    def register(self, url: str, json_body: Any, status: int = 200) -> None:
        self._responses[url] = (status, json_body)

    def register_bytes(self, url: str, content: bytes, status: int = 200) -> None:
        self._responses[url] = (status, content)

    def get(self, url: str, **kwargs: Any) -> MagicMock:
        resp = MagicMock()
        if url in self._responses:
            status, body = self._responses[url]
            resp.status_code = status
            if isinstance(body, bytes):
                resp.content = body
                resp.json.side_effect = ValueError("not JSON")
            else:
                resp.json.return_value = body
                resp.content = json.dumps(body).encode()
            if status >= 400:
                resp.raise_for_status.side_effect = Exception(
                    f"HTTP {status}"
                )
            else:
                resp.raise_for_status.return_value = None
        else:
            resp.status_code = 404
            resp.raise_for_status.side_effect = Exception("404 Not Found")
        return resp


def _make_connector(session: _FakeSession) -> SharePointConnector:
    """Return a connector with a pre-injected session (no real auth)."""
    conn = SharePointConnector(
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        tenant_id=_TENANT,
        requests_session=session,
    )
    # Bypass MSAL by directly setting the token
    conn._access_token = "fake-bearer-token"
    return conn


# ---------------------------------------------------------------------------
# Tests: basic sync
# ---------------------------------------------------------------------------

class TestSharePointConnectorSync:
    def test_sync_txt_file_produces_parsed_document(self) -> None:
        session = _FakeSession()
        txt_content = _make_txt_bytes("Hello from SharePoint.")
        download_url = "https://download.example.com/file.txt"

        item = _file_item(_ITEM_ID_TXT, "readme.txt", "text/plain", download_url)
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta",
            _drive_delta_response([item]),
        )
        session.register(download_url, None, status=200)
        session._responses[download_url] = (200, txt_content)  # bytes

        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/items/{_ITEM_ID_TXT}/permissions",
            _permissions_response(["alice", "bob"]),
        )

        conn = _make_connector(session)
        docs = conn.sync(
            site_url="https://contoso.sharepoint.com/sites/Eng",
            drive_id=_DRIVE_ID,
        )

        assert len(docs) == 1
        doc = docs[0]
        assert isinstance(doc, ParsedDocument)
        assert doc.source_id == f"sharepoint:{_DRIVE_ID}:{_ITEM_ID_TXT}"
        assert "alice" in doc.acl_allow
        assert "bob" in doc.acl_allow

    def test_sync_skips_unsupported_mime(self) -> None:
        session = _FakeSession()
        item = _file_item(
            _ITEM_ID_UNSUPPORTED, "virus.exe", "application/x-msdownload"
        )
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta",
            _drive_delta_response([item]),
        )
        conn = _make_connector(session)
        docs = conn.sync(
            site_url="https://contoso.sharepoint.com/sites/Eng",
            drive_id=_DRIVE_ID,
        )
        assert docs == []

    def test_sync_skips_deleted_items(self) -> None:
        session = _FakeSession()
        item = _file_item(_ITEM_ID_TXT, "gone.txt", "text/plain")
        item["deleted"] = {"state": "deleted"}
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta",
            _drive_delta_response([item]),
        )
        conn = _make_connector(session)
        docs = conn.sync(
            site_url="https://contoso.sharepoint.com/sites/Eng",
            drive_id=_DRIVE_ID,
        )
        assert docs == []

    def test_sync_empty_drive_returns_empty_list(self) -> None:
        session = _FakeSession()
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta",
            _drive_delta_response([]),
        )
        conn = _make_connector(session)
        docs = conn.sync(
            site_url="https://contoso.sharepoint.com/sites/Eng",
            drive_id=_DRIVE_ID,
        )
        assert docs == []


# ---------------------------------------------------------------------------
# Tests: sensitivity label → classification mapping
# ---------------------------------------------------------------------------

class TestSensitivityMapping:
    def _sync_with_sensitivity(self, sensitivity: str) -> ParsedDocument | None:
        session = _FakeSession()
        txt_bytes = _make_txt_bytes("Classified doc.")
        download_url = "https://dl.example.com/doc.txt"

        item = _file_item(
            _ITEM_ID_TXT, "doc.txt", "text/plain",
            download_url=download_url, sensitivity=sensitivity
        )
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta",
            _drive_delta_response([item]),
        )
        session._responses[download_url] = (200, txt_bytes)
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/items/{_ITEM_ID_TXT}/permissions",
            _permissions_response([]),
        )
        conn = _make_connector(session)
        docs = conn.sync(
            site_url="https://contoso.sharepoint.com/sites/Eng",
            drive_id=_DRIVE_ID,
        )
        return docs[0] if docs else None

    def test_public_label_maps_to_public(self) -> None:
        doc = self._sync_with_sensitivity("Public")
        assert doc is not None
        assert doc.classification == "public"

    def test_confidential_label_maps_to_confidential(self) -> None:
        doc = self._sync_with_sensitivity("Confidential")
        assert doc is not None
        assert doc.classification == "confidential"

    def test_highly_confidential_maps_to_restricted(self) -> None:
        doc = self._sync_with_sensitivity("Highly Confidential")
        assert doc is not None
        assert doc.classification == "restricted"

    def test_unknown_label_defaults_to_internal(self) -> None:
        doc = self._sync_with_sensitivity("Unclassified Widget Label")
        assert doc is not None
        assert doc.classification == "internal"

    def test_no_sensitivity_label_defaults_to_internal(self) -> None:
        doc = self._sync_with_sensitivity("")
        assert doc is not None
        assert doc.classification == "internal"


# ---------------------------------------------------------------------------
# Tests: ACL resolution
# ---------------------------------------------------------------------------

class TestAclResolution:
    def test_permissions_endpoint_populates_acl(self) -> None:
        session = _FakeSession()
        txt_bytes = _make_txt_bytes("ACL test doc.")
        download_url = "https://dl.example.com/acl.txt"

        item = _file_item(_ITEM_ID_TXT, "acl.txt", "text/plain", download_url)
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta",
            _drive_delta_response([item]),
        )
        session._responses[download_url] = (200, txt_bytes)
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/items/{_ITEM_ID_TXT}/permissions",
            _permissions_response(["user-001", "group-engineering"]),
        )

        conn = _make_connector(session)
        docs = conn.sync(
            site_url="https://contoso.sharepoint.com/sites/Eng",
            drive_id=_DRIVE_ID,
        )
        assert len(docs) == 1
        assert "user-001" in docs[0].acl_allow
        assert "group-engineering" in docs[0].acl_allow

    def test_failed_permissions_endpoint_returns_empty_acl(self) -> None:
        session = _FakeSession()
        txt_bytes = _make_txt_bytes("Fail-closed test.")
        download_url = "https://dl.example.com/fail.txt"

        item = _file_item(_ITEM_ID_TXT, "fail.txt", "text/plain", download_url)
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta",
            _drive_delta_response([item]),
        )
        session._responses[download_url] = (200, txt_bytes)
        # Permissions endpoint returns 403
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/items/{_ITEM_ID_TXT}/permissions",
            {"error": "Forbidden"},
            status=403,
        )

        conn = _make_connector(session)
        docs = conn.sync(
            site_url="https://contoso.sharepoint.com/sites/Eng",
            drive_id=_DRIVE_ID,
        )
        # Doc is still returned, but with empty ACL (fail-closed)
        assert len(docs) == 1
        assert docs[0].acl_allow == []


# ---------------------------------------------------------------------------
# Tests: configuration errors
# ---------------------------------------------------------------------------

class TestConfigurationErrors:
    def test_missing_env_vars_raise_config_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SHAREPOINT_CLIENT_ID", raising=False)
        monkeypatch.delenv("SHAREPOINT_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("SHAREPOINT_TENANT_ID", raising=False)

        conn = SharePointConnector()
        with pytest.raises(ConfigurationError, match="SHAREPOINT_CLIENT_ID"):
            conn._ensure_token()

    def test_env_vars_used_when_constructor_args_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SHAREPOINT_CLIENT_ID", "env-client-id")
        monkeypatch.setenv("SHAREPOINT_CLIENT_SECRET", "env-secret")
        monkeypatch.setenv("SHAREPOINT_TENANT_ID", "env-tenant-id")

        # Patch MSAL so we don't make real network calls
        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = {
            "access_token": "mocked-token"
        }
        with patch("msal.ConfidentialClientApplication", return_value=mock_app):
            conn = SharePointConnector()
            conn._ensure_token()
            assert conn._access_token == "mocked-token"


# ---------------------------------------------------------------------------
# Tests: delta / incremental sync
# ---------------------------------------------------------------------------

class TestDeltaSync:
    def test_delta_token_appended_to_url(self) -> None:
        """Verify delta_token is passed to the Graph delta endpoint."""
        session = _FakeSession()
        delta_url = (
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta?$token=my-delta-token"
        )
        session.register(delta_url, _drive_delta_response([]))

        conn = _make_connector(session)
        docs = conn.sync(
            site_url="https://contoso.sharepoint.com/sites/Eng",
            drive_id=_DRIVE_ID,
            delta_token="my-delta-token",
        )
        assert docs == []

    def test_latest_delta_uses_latest_token(self) -> None:
        session = _FakeSession()
        latest_url = (
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta?$token=latest"
        )
        session.register(latest_url, _drive_delta_response([]))

        conn = _make_connector(session)
        docs = conn.sync(
            site_url="https://contoso.sharepoint.com/sites/Eng",
            drive_id=_DRIVE_ID,
            delta_token="latest",
        )
        assert docs == []


# ---------------------------------------------------------------------------
# Tests: metadata population
# ---------------------------------------------------------------------------

class TestMetadataPopulation:
    def test_metadata_fields_present(self) -> None:
        session = _FakeSession()
        txt_bytes = _make_txt_bytes("Metadata test.")
        download_url = "https://dl.example.com/meta.txt"

        item = _file_item(_ITEM_ID_TXT, "meta.txt", "text/plain", download_url)
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta",
            _drive_delta_response([item]),
        )
        session._responses[download_url] = (200, txt_bytes)
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/items/{_ITEM_ID_TXT}/permissions",
            _permissions_response([]),
        )

        conn = _make_connector(session)
        docs = conn.sync(
            site_url="https://contoso.sharepoint.com/sites/Eng",
            drive_id=_DRIVE_ID,
        )
        assert len(docs) == 1
        meta = docs[0].metadata
        assert meta.get("title") == "meta.txt"
        assert meta.get("drive_id") == _DRIVE_ID
        assert meta.get("item_id") == _ITEM_ID_TXT
        assert meta.get("created") == "2026-01-01T00:00:00Z"
        assert meta.get("modified") == "2026-06-01T00:00:00Z"
        assert meta.get("created_by") == "Alice"


# ---------------------------------------------------------------------------
# Tests: docx via SharePoint
# ---------------------------------------------------------------------------

class TestSharePointDocxSync:
    def test_docx_downloaded_and_parsed(self) -> None:
        session = _FakeSession()
        docx_bytes = _make_docx_bytes()
        download_url = "https://dl.example.com/report.docx"

        item = _file_item(
            _ITEM_ID_DOCX,
            "report.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            download_url,
        )
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/root/delta",
            _drive_delta_response([item]),
        )
        session._responses[download_url] = (200, docx_bytes)
        session.register(
            f"{_GRAPH_BASE}/drives/{_DRIVE_ID}/items/{_ITEM_ID_DOCX}/permissions",
            _permissions_response(["reader-001"]),
        )

        conn = _make_connector(session)
        docs = conn.sync(
            site_url="https://contoso.sharepoint.com/sites/Eng",
            drive_id=_DRIVE_ID,
        )

        assert len(docs) == 1
        doc = docs[0]
        assert "SharePoint document content" in doc.text
        assert "reader-001" in doc.acl_allow
