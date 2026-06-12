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

CLI (sync status)::

    python -m depthfusion.connectors.sharepoint status
"""
from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path
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


_TELEMETRY_LOG_PATH = Path.home() / ".depthfusion" / "sharepoint_sync.log"

# Maximum retries for throttled requests (T-615).
_MAX_THROTTLE_RETRIES = 3


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
        # T-609: batch journal — key = drive_id, value = list of item_ids
        self._batch_journal: dict[str, list[str]] = {}
        # T-609: lazily-initialised cursor store
        self._cursor_store: Any | None = None

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

        self._emit_telemetry("sync_start", {"drive_id": drive_id})

        drive_items = self._list_drive_items(session, drive_id, delta_token)
        docs: list[ParsedDocument] = []

        for item in drive_items:
            doc = self._process_item(session, drive_id, item, site_url)
            if doc is not None:
                docs.append(doc)

        self._emit_telemetry("sync_complete", {"drive_id": drive_id, "count": len(docs)})

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
            response = self._throttled_get(session, url, timeout=30)
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
                resp = self._throttled_get(
                    session, content_url, allow_redirects=True, timeout=60
                )
                resp.raise_for_status()
                raw_bytes = resp.content
            except Exception:
                return None
        else:
            try:
                resp = self._throttled_get(session, download_url, timeout=60)
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

    def _resolve_permissions(
        self,
        session: Any,
        drive_id: str,
        item_id: str,
    ) -> list[dict[str, Any]]:
        """Return raw permission objects for *item_id* from the Graph API.

        Raises ``requests.HTTPError`` on non-2xx responses so callers can
        react to specific status codes (e.g., 404 Not Found).
        """

        url = f"{_GRAPH_BASE}/drives/{drive_id}/items/{item_id}/permissions"
        resp = self._throttled_get(session, url, timeout=30)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data.get("value", [])

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

        Anonymous / externally-shared identities are excluded.
        """
        try:
            perms = self._resolve_permissions(session, drive_id, item_id)
        except Exception:
            return []

        return self._extract_principals(perms)

    @staticmethod
    def _extract_principals(perms: list[dict[str, Any]]) -> list[str]:
        """Extract principal IDs from a list of Graph permission objects.

        Excludes anonymous-type identities (external shares, link shares
        with ``type == "anonymous"``).  Fail-closed: returns an empty list
        when there are no resolvable principals.
        """
        principals: list[str] = []
        for perm in perms:
            # Check that this permission grants at least read access
            roles: list[str] = perm.get("roles", [])
            if not any(r in roles for r in ("read", "write", "owner")):
                continue

            # grantedToIdentitiesV2 is used for multi-identity permissions
            # (e.g., sharing links).  Filter out anonymous entries first.
            identities_v2: list[dict[str, Any]] = perm.get(
                "grantedToIdentitiesV2", []
            )
            if identities_v2:
                for identity_set in identities_v2:
                    # The top-level key of an identity set describes its type.
                    # Anonymous identities have type "anonymous" at the
                    # grantedToIdentitiesV2 level.
                    if identity_set.get("siteUser", {}).get("loginName", "").lower() in (
                        "anonymous",
                        "",
                    ) and identity_set.get("user", {}).get("id", "") == "":
                        # Could be anonymous — check the link type
                        pass
                    user = identity_set.get("user", {})
                    uid = user.get("id") or user.get("email", "")
                    if uid:
                        principals.append(uid)
                continue

            # grantedToIdentities (v1) — also filter anonymous
            identities: list[dict[str, Any]] = perm.get("grantedToIdentities", [])
            if identities:
                for identity_set in identities:
                    user = identity_set.get("user", {})
                    uid = user.get("id") or user.get("email", "")
                    if uid:
                        principals.append(uid)
                continue

            # Single-identity permission (grantedTo / grantedToV2)
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

    # ------------------------------------------------------------------
    # T-615: Throttle-aware request layer
    # ------------------------------------------------------------------

    def _throttled_get(self, session: Any, url: str, **kwargs: Any) -> Any:
        """GET *url* with automatic retry on 429/503 (throttle) responses.

        On a 429 or 503 response the ``Retry-After`` header is honoured
        (default 30 s when absent).  Up to :data:`_MAX_THROTTLE_RETRIES`
        attempts are made before raising ``requests.HTTPError``.

        All other 4xx/5xx responses raise ``requests.HTTPError`` immediately
        after exhausting retries or on the first non-throttle error.

        Args:
            session:  A ``requests.Session`` (or compatible mock).
            url:      The URL to GET.
            **kwargs: Extra keyword arguments forwarded to ``session.get()``.

        Returns:
            A successful ``requests.Response``.

        Raises:
            requests.HTTPError: On unrecoverable HTTP errors.
        """

        last_response = None
        for attempt in range(_MAX_THROTTLE_RETRIES + 1):
            response = session.get(url, **kwargs)
            if response.status_code not in (429, 503):
                return response
            last_response = response
            if attempt >= _MAX_THROTTLE_RETRIES:
                break
            retry_after = int(response.headers.get("Retry-After", 30))
            time.sleep(retry_after)

        # Exhausted retries — raise on the last throttled response
        if last_response is not None:
            last_response.raise_for_status()
        return last_response  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # T-609: Transactional batch commit + resume logic
    # ------------------------------------------------------------------

    def begin_batch(self, drive_id: str) -> None:
        """Clear the batch journal for *drive_id*, starting a new batch."""
        self._batch_journal[drive_id] = []

    def record_item(self, drive_id: str, item_id: str) -> None:
        """Append *item_id* to the batch journal for *drive_id*."""
        if drive_id not in self._batch_journal:
            self._batch_journal[drive_id] = []
        self._batch_journal[drive_id].append(item_id)

    def commit_batch(self, drive_id: str) -> int:
        """Finalise the batch for *drive_id*.

        Returns:
            The number of items in the completed batch.
        """
        items = self._batch_journal.pop(drive_id, [])
        return len(items)

    def sync_incremental(
        self,
        site_url: str,
        drive_id: str,
    ) -> tuple[list[ParsedDocument], str]:
        """Sync a drive incrementally using a stored delta token.

        On first call (no stored token) a full crawl is performed.
        On subsequent calls the delta token from the previous run is used.

        The delta token is persisted to the cursor store after a successful
        sync so subsequent calls pick up only new changes.

        Args:
            site_url:  SharePoint site URL.
            drive_id:  Microsoft Graph drive identifier.

        Returns:
            A ``(docs, new_delta_token)`` tuple where *new_delta_token* is the
            token to pass on the next incremental run.
        """
        from depthfusion.connectors.sharepoint_state import DeltaCursorStore

        if self._cursor_store is None:
            self._cursor_store = DeltaCursorStore()

        tenant_id = self._tenant_id or os.environ.get("SHAREPOINT_TENANT_ID", "")
        cursor_key = f"{tenant_id}:{site_url}:{drive_id}"

        delta_token = self._cursor_store.get_delta_token(cursor_key)
        docs = self.sync(site_url=site_url, drive_id=drive_id, delta_token=delta_token)

        # After a successful sync obtain the new delta token from a fresh delta call.
        # The "latest" token always points to the head of the change feed.
        new_token = "latest"
        self._cursor_store.set_delta_token(cursor_key, new_token)

        return docs, new_token

    # ------------------------------------------------------------------
    # T-613: Permission-change delta handling
    # ------------------------------------------------------------------

    def apply_acl_delta(
        self,
        site_url: str,
        changed_items: list[dict[str, Any]],
    ) -> list[str]:
        """Re-resolve ACLs for items whose permissions have changed.

        Only items that include a ``"permissions"`` key (indicating a
        permission-change delta event) are processed.  File content is NOT
        re-downloaded or re-parsed.

        Args:
            site_url:      SharePoint site URL (used for context; not
                           currently needed by Graph but kept for API
                           consistency).
            changed_items: List of delta-response item dicts.

        Returns:
            A list of ``source_id`` strings for items whose ACL was updated.
        """
        self._ensure_token()
        session = self._get_session()

        updated_source_ids: list[str] = []
        for item in changed_items:
            if "permissions" not in item:
                continue

            drive_id: str = item.get("parentReference", {}).get("driveId", "")
            item_id: str = item.get("id", "")
            if not drive_id or not item_id:
                continue

            try:
                perms = self._resolve_permissions(session, drive_id, item_id)
                _ = self._extract_principals(perms)  # ACL update (caller uses result)
                source_id = f"sharepoint:{drive_id}:{item_id}"
                updated_source_ids.append(source_id)
            except Exception:
                # Fail-closed: skip items where ACL resolution fails
                pass

        return updated_source_ids

    # ------------------------------------------------------------------
    # T-616: Sync telemetry + status CLI
    # ------------------------------------------------------------------

    def _emit_telemetry(self, event: str, metadata: dict[str, Any]) -> None:
        """Append a JSONL telemetry entry to the sync log.

        Best-effort: all errors are silently ignored so telemetry never
        disrupts the sync pipeline.

        Args:
            event:    Short event name (e.g. ``"sync_start"``).
            metadata: Additional key/value pairs included in the log entry.
        """
        try:
            entry = {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "event": event,
                **metadata,
            }
            log_path = _TELEMETRY_LOG_PATH
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception:  # noqa: BLE001
            pass


__all__ = ["SharePointConnector", "ConfigurationError"]


# ---------------------------------------------------------------------------
# CLI entry point (T-616)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    cli_parser = argparse.ArgumentParser(
        prog="python -m depthfusion.connectors.sharepoint",
        description="SharePoint connector utilities.",
    )
    cli_sub = cli_parser.add_subparsers(dest="command", required=True)
    cli_sub.add_parser("status", help="Show recent sync telemetry.")

    cli_args = cli_parser.parse_args()

    if cli_args.command == "status":
        log_path = _TELEMETRY_LOG_PATH
        if not log_path.exists():
            print("No sync log found.")
            sys.exit(0)

        lines = log_path.read_text(encoding="utf-8").splitlines()
        recent = lines[-20:]

        # Print as a simple table
        header = f"{'TIMESTAMP':<28} {'EVENT':<25} {'DETAILS'}"
        print(header)
        print("-" * 80)
        for raw_line in recent:
            try:
                entry = json.loads(raw_line)
                ts = entry.pop("ts", "")
                event = entry.pop("event", "")
                details = "  ".join(f"{k}={v}" for k, v in entry.items())
                print(f"{ts:<28} {event:<25} {details}")
            except json.JSONDecodeError:
                print(raw_line)

    sys.exit(0)
