"""Fail-closed permission tests for SharePointConnector (T-614).

These tests verify that ACL resolution is always fail-closed:
- Unresolvable permissions → empty ACL (not a broad allow)
- Broken inheritance references → empty ACL
- Anonymous / external-share grants → excluded from ACL
"""
from __future__ import annotations

from unittest.mock import MagicMock

import requests


class TestFailClosedPermissions:
    """Verify that ACL errors and untrusted identities never leak access."""

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _make_connector():
        """Return a minimal SharePointConnector without a real MSAL token."""
        # Import inside the test so a missing optional dep (msal) doesn't
        # break collection.
        from depthfusion.connectors.sharepoint import SharePointConnector

        # Bypass token acquisition — we are testing ACL logic only.
        conn = SharePointConnector(
            client_id="x",
            client_secret="y",
            tenant_id="z",
        )
        conn._access_token = "fake-token"
        return conn

    @staticmethod
    def _make_session(perms_response=None, status_code=200, raise_exc=None):
        """Build a mock requests.Session whose .get() returns a canned response."""
        session = MagicMock()

        if raise_exc is not None:
            session.get.side_effect = raise_exc
        else:
            mock_resp = MagicMock()
            mock_resp.status_code = status_code
            mock_resp.headers = {}
            if status_code >= 400:
                mock_resp.raise_for_status.side_effect = requests.HTTPError(
                    response=mock_resp
                )
            else:
                mock_resp.raise_for_status.return_value = None
                mock_resp.json.return_value = {"value": perms_response or []}
            session.get.return_value = mock_resp

        return session

    # ------------------------------------------------------------------ #
    # T1: _resolve_permissions raises HTTPError → _resolve_acl returns []
    # ------------------------------------------------------------------ #

    def test_unresolvable_permission(self):
        """A 404 from the permissions endpoint must produce an empty ACL."""
        conn = self._make_connector()

        # Inject a session that returns HTTP 404 for any GET
        session = self._make_session(status_code=404)
        conn._session = session

        acl = conn._resolve_acl(
            session=session,
            drive_id="drive-abc",
            item_id="item-123",
            site_url="https://contoso.sharepoint.com/sites/eng",
        )

        assert acl == [], (
            "Expected empty ACL (fail-closed) when permissions endpoint returns 404, "
            f"got: {acl!r}"
        )

    # ------------------------------------------------------------------ #
    # T2: Broken inheritance (inheritedFrom points to unknown parent)
    # ------------------------------------------------------------------ #

    def test_broken_inheritance(self):
        """A permission with an unresolvable inheritedFrom parent must not grant access."""
        # Simulate a Graph response where the permission includes an
        # inheritedFrom reference but the grantedTo block is absent/empty.
        broken_perm = {
            "id": "perm-1",
            "roles": ["read"],
            "inheritedFrom": {
                # References a drive item that no longer exists.
                "driveId": "b!ghost-drive",
                "id": "item-does-not-exist",
                "path": "/sites/old-site/Shared Documents",
            },
            # No grantedTo / grantedToV2 → no identifiable principal
        }

        conn = self._make_connector()
        session = self._make_session(perms_response=[broken_perm])
        conn._session = session

        acl = conn._resolve_acl(
            session=session,
            drive_id="drive-abc",
            item_id="item-broken",
            site_url="https://contoso.sharepoint.com/sites/eng",
        )

        assert acl == [], (
            "Expected empty ACL when inheritance reference is unresolvable, "
            f"got: {acl!r}"
        )

    # ------------------------------------------------------------------ #
    # T3: Anonymous external-share grant must be excluded
    # ------------------------------------------------------------------ #

    def test_external_share(self):
        """Anonymous identity grants in grantedToIdentitiesV2 must not appear in ACL."""
        # This is the shape Graph returns for an "Anyone with the link" share.
        anon_perm = {
            "id": "link-perm-99",
            "roles": ["read"],
            "link": {
                "type": "anonymous",
                "scope": "anonymous",
            },
            "grantedToIdentitiesV2": [
                {
                    # Anonymous user — no real ID
                    "user": {"id": "", "displayName": ""},
                    "siteUser": {"loginName": "anonymous"},
                }
            ],
        }

        conn = self._make_connector()
        session = self._make_session(perms_response=[anon_perm])
        conn._session = session

        acl = conn._resolve_acl(
            session=session,
            drive_id="drive-abc",
            item_id="item-public",
            site_url="https://contoso.sharepoint.com/sites/eng",
        )

        # Anonymous identities must be excluded — empty ACL expected.
        assert "" not in acl, "Empty-string principal must not appear in ACL"
        # No real identity was present, so the list must be empty.
        assert acl == [], (
            "Anonymous external-share grant must not produce any ACL principal, "
            f"got: {acl!r}"
        )
