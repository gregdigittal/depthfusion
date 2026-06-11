"""Microsoft SharePoint connector via Microsoft Graph API (E-54).

Authenticates with MSAL (client credentials flow) and syncs documents
from a SharePoint drive into DepthFusion :class:`ParsedDocument` records.

Configuration (all from environment variables):

    SHAREPOINT_CLIENT_ID     — Entra/Azure AD application (client) ID
    SHAREPOINT_CLIENT_SECRET — Application secret value
    SHAREPOINT_TENANT_ID     — Directory (tenant) ID

Usage::

    from depthfusion.connectors.sharepoint import SharePointConnector

    conn = SharePointConnector()
    docs = conn.sync(
        site_url="https://contoso.sharepoint.com/sites/Engineering",
        drive_id="b!abc123",
    )
    for doc in docs:
        print(doc.source_id, doc.metadata.get("title"))

Incremental sync (delta query)::

    # First run — no delta token:
    docs = conn.sync(site_url=..., drive_id=...)

    # Subsequent runs — pass the last delta token:
    docs = conn.sync(site_url=..., drive_id=..., delta_token="latest")
"""
from __future__ import annotations

import os
from typing import Any

from depthfusion.ingest.models import ParsedDocument
from depthfusion.ingest.pipeline import IngestPipeline

# Supported MIME types that we attempt to parse.
_SUPPORTED_MIMES: set[str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/pdf",
    "text/plain",
    "text/markdown",
}

# SharePoint sensitivity-label → DepthFusion ClassificationLevel mapping.
# Unmapped labels default to "internal".
_SENSITIVITY_MAP: dict[str, str] = {
    "Public": "public",
    "General": "internal",
    "Confidential": "confidential",
    "Highly Confidential": "restricted",
    "HBI": "restricted",
    "MBI": "confidential",
    "LBI": "internal",
}

# Microsoft Graph v1.0 base URL.
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Scope required for reading SharePoint files via Graph.
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"


def _get_env(key: str) -> str:
    """Read a required environment variable; raise ConfigurationError if absent."""
    value = os.environ.get(key, "")
    if not value:
        raise ConfigurationError(
            f"Required environment variable '{key}' is not set. "
            f"SharePoint connector requires SHAREPOINT_CLIENT_ID, "
            f"SHAREPOINT_CLIENT_SECRET, and SHAREPOINT_TENANT_ID."
        )
    return value


class ConfigurationError(Exception):
    """Raised when required connector configuration is missing."""


class SharePointConnector:
    """Sync documents from a SharePoint drive into DepthFusion.

    Authentication uses MSAL client-credentials flow (app-only permissions).
    The service account must have at least *Files.Read.All* on the target
    site (grant via SharePoint Admin > API access or Sites.Selected consent).

    Args:
        client_id:      Entra application client ID.  Defaults to the
                        ``SHAREPOINT_CLIENT_ID`` environment variable.
        client_secret:  Entra application secret.  Defaults to
                        ``SHAREPOINT_CLIENT_SECRET``.
        tenant_id:      Entra tenant ID.  Defaults to
                        ``SHAREPOINT_TENANT_ID``.
        pipeline:       :class:`~depthfusion.ingest.pipeline.IngestPipeline`
                        instance.  A default pipeline is created when
                        not provided.
        requests_session:  Optionally inject a ``requests.Session`` (useful
                           for testing with ``responses`` mock library).
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
        pipeline: IngestPipeline | None = None,
        requests_session: Any | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._tenant_id = tenant_id
        self._pipeline = pipeline or IngestPipeline()
        self._session = requests_session
        self._access_token: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync(
        self,
        site_url: str,
        drive_id: str,
        delta_token: str | None = None,
    ) -> list[ParsedDocument]:
        """Sync documents from a SharePoint drive.

        Performs a full crawl when *delta_token* is ``None``; an incremental
        delta-query sync when *delta_token* is provided.

        Only files whose MIME type is in the supported set are downloaded and
        parsed.  Files the service account cannot read are silently skipped.

        Args:
            site_url:    SharePoint site URL (used for ACL resolution).
            drive_id:    Microsoft Graph drive identifier.
            delta_token: Delta query token from a previous sync run.
                         Pass ``None`` for an initial full crawl, or
                         ``"latest"`` to fetch only changes since the
                         previous sync.

        Returns:
            A list of :class:`ParsedDocument` objects ready for indexing.
        """
        self._ensure_token()
        session = self._get_session()

        drive_items = self._list_drive_items(session, drive_id, delta_token)
        docs: list[ParsedDocument] = []

        for item in drive_items:
            doc = self._process_item(session, drive_id, item, site_url)
            if doc is not None:
                docs.append(doc)

        return docs

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _ensure_token(self) -> None:
        """Obtain an access token if we don't already have one."""
        if self._access_token:
            return

        try:
            import msal
        except ImportError as exc:
            raise ImportError(
                "msal is required for SharePoint connector. "
                "Install it with: pip install msal"
            ) from exc

        client_id = self._client_id or _get_env("SHAREPOINT_CLIENT_ID")
        client_secret = self._client_secret or _get_env("SHAREPOINT_CLIENT_SECRET")
        tenant_id = self._tenant_id or _get_env("SHAREPOINT_TENANT_ID")

        authority = f"https://login.microsoftonline.com/{tenant_id}"
        app = msal.ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=authority,
        )
        result = app.acquire_token_for_client(scopes=[_GRAPH_SCOPE])
        if "access_token" not in result:
            raise ConfigurationError(
                f"MSAL token acquisition failed: {result.get('error_description', result)}"
            )
        self._access_token = result["access_token"]

    # ------------------------------------------------------------------
    # Graph API helpers
    # ------------------------------------------------------------------

    def _get_session(self) -> Any:
        """Return a requests.Session with the bearer token header set."""
        if self._session is not None:
            return self._session

        import requests

        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
            }
        )
        return session

    def _list_drive_items(
        self,
        session: Any,
        drive_id: str,
        delta_token: str | None,
    ) -> list[dict[str, Any]]:
        """Walk the drive (or delta) and return a flat list of file items.

        Handles pagination via ``@odata.nextLink``.
        """
        if delta_token:
            if delta_token == "latest":
                url: str | None = (
                    f"{_GRAPH_BASE}/drives/{drive_id}/root/delta?$token=latest"
                )
            else:
                url = f"{_GRAPH_BASE}/drives/{drive_id}/root/delta?$token={delta_token}"
        else:
            url = f"{_GRAPH_BASE}/drives/{drive_id}/root/delta"

        items: list[dict[str, Any]] = []

        while url:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")

        # Filter to files only (exclude folders and deleted items)
        return [
            item
            for item in items
            if "file" in item and not item.get("deleted")
        ]

    def _process_item(
        self,
        session: Any,
        drive_id: str,
        item: dict[str, Any],
        site_url: str,
    ) -> ParsedDocument | None:
        """Download, parse, and ACL-stamp a single drive item.

        Returns ``None`` if the file type is unsupported or download fails.
        """
        mime_type: str = item.get("file", {}).get("mimeType", "")
        if mime_type not in _SUPPORTED_MIMES:
            return None

        item_id: str = item.get("id", "")
        name: str = item.get("name", "")
        web_url: str = item.get("webUrl", "")

        # Build a stable source_id from the drive + item id
        source_id = f"sharepoint:{drive_id}:{item_id}"

        # Resolve SharePoint permissions → ACL allow list
        acl_allow = self._resolve_acl(session, drive_id, item_id, site_url)

        # Map sensitivity label → DepthFusion classification
        sensitivity = self._extract_sensitivity(item)
        classification = _SENSITIVITY_MAP.get(sensitivity, "internal")

        # Download content
        download_url: str = (
            item.get("@microsoft.graph.downloadUrl")
            or item.get("downloadUrl")
            or ""
        )
        if not download_url:
            # Fallback: fetch download URL via Graph content endpoint
            content_url = (
                f"{_GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
            )
            try:
                resp = session.get(content_url, allow_redirects=True, timeout=60)
                resp.raise_for_status()
                raw_bytes = resp.content
            except Exception:
                return None
        else:
            try:
                resp = session.get(download_url, timeout=60)
                resp.raise_for_status()
                raw_bytes = resp.content
            except Exception:
                return None

        # Build metadata
        created_dt = item.get("createdDateTime", "")
        modified_dt = item.get("lastModifiedDateTime", "")
        created_by: str = (
            item.get("createdBy", {}).get("user", {}).get("displayName", "")
        )
        metadata: dict[str, str] = {
            "title": name,
            "web_url": web_url,
            "created": created_dt,
            "modified": modified_dt,
            "created_by": created_by,
            "drive_id": drive_id,
            "item_id": item_id,
        }

        try:
            doc = self._pipeline.run_from_bytes(
                source_id=source_id,
                data=raw_bytes,
                mime_type=mime_type,
                acl_allow=acl_allow,
                classification=classification,
                metadata=metadata,
            )
        except Exception:
            return None

        return doc

    def _resolve_acl(
        self,
        session: Any,
        drive_id: str,
        item_id: str,
        site_url: str,
    ) -> list[str]:
        """Resolve the effective permissions for *item_id* into a principal list.

        Calls the Graph permissions endpoint and extracts user/group ids that
        have read access.  Falls back to an empty list on any error (fail-closed).
        """
        try:
            url = f"{_GRAPH_BASE}/drives/{drive_id}/items/{item_id}/permissions"
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except Exception:
            return []

        principals: list[str] = []
        for perm in data.get("value", []):
            # Check that this permission grants at least read access
            roles: list[str] = perm.get("roles", [])
            if not any(r in roles for r in ("read", "write", "owner")):
                continue

            granted_to = perm.get("grantedTo") or perm.get("grantedToV2", {})
            if not granted_to:
                continue

            user = granted_to.get("user", {})
            group = granted_to.get("group", {})

            uid = user.get("id") or user.get("email", "")
            if uid:
                principals.append(uid)

            gid = group.get("id", "")
            if gid:
                principals.append(gid)

        return principals

    @staticmethod
    def _extract_sensitivity(item: dict[str, Any]) -> str:
        """Extract the sensitivity label string from a drive item, if present."""
        # Graph may surface sensitivityLabel in different locations depending on
        # whether Information Protection is enabled.
        label: str = (
            item.get("sensitivityLabel", {}).get("displayName", "")
            or item.get("informationProtectionLabel", {}).get("displayName", "")
            or ""
        )
        return label


__all__ = ["SharePointConnector", "ConfigurationError"]
