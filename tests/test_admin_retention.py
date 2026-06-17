"""Tests for T-671 (E-60 S-194): retention enforcement + compliance export.

Covers:
- POST /v2/admin/retention/enforce purges out-of-window audit records
- Retention enforcement is itself audited (ADMIN_ACTION written)
- GET /v2/admin/compliance/export (/v2/admin/export) is capability-gated
- Under-privileged callers (viewer) receive 403
- Unauthenticated callers receive 401/403
- Export is denied (403) for a disallowed classification ceiling
- A permitted export returns data and writes an EXPORT_STARTED audit event
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from depthfusion.audit.log import AuditEvent, AuditEventType, AuditStore
from depthfusion.identity.models import Principal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_audit_events(data_dir: Path) -> tuple[int, int]:
    """Seed the audit store with old + recent events.

    Returns (old_count, recent_count).
    """
    store = AuditStore(db_path=data_dir / "audit.db")
    now = time.time()
    old_ts = now - (400 * 86400)  # 400 days ago — outside default 365d window
    recent_ts = now - (10 * 86400)  # 10 days ago — inside window

    old_count, recent_count = 3, 2
    for i in range(old_count):
        store.log(
            AuditEvent(
                event_type=AuditEventType.RECORD_READ,
                actor_principal_id=f"old-actor-{i}",
                timestamp=old_ts,
            )
        )
    for i in range(recent_count):
        store.log(
            AuditEvent(
                event_type=AuditEventType.RECORD_READ,
                actor_principal_id=f"recent-actor-{i}",
                timestamp=recent_ts,
            )
        )
    return old_count, recent_count


def _make_client(tmp_path: Path, principal: Principal) -> Iterator[tuple[TestClient, Path]]:
    from depthfusion.api.auth import _require_principal_dep
    from depthfusion.api.rest import app

    os.environ["DEPTHFUSION_DATA_DIR"] = str(tmp_path)
    app.dependency_overrides[_require_principal_dep] = lambda: principal
    client = TestClient(app, raise_server_exceptions=True)
    try:
        yield client, tmp_path
    finally:
        app.dependency_overrides.clear()
        if "DEPTHFUSION_DATA_DIR" in os.environ:
            del os.environ["DEPTHFUSION_DATA_DIR"]


@pytest.fixture()
def admin_client(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    """Admin principal — has MANAGE_SETTINGS + VIEW_AUDIT_LOG."""
    yield from _make_client(tmp_path, Principal(principal_id="admin-test", groups=["admin"]))


@pytest.fixture()
def viewer_client(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    """Viewer principal — has neither MANAGE_SETTINGS nor VIEW_AUDIT_LOG."""
    yield from _make_client(tmp_path, Principal(principal_id="viewer-test", groups=["viewer"]))


@pytest.fixture()
def unauth_client(tmp_path: Path) -> Iterator[TestClient]:
    """Client with the real (unconfigured) auth dependency — no override.

    With no OIDC env and no legacy token, the dependency raises 503/401/403,
    so any protected route is rejected for an unauthenticated caller.
    """
    from depthfusion.api.rest import app

    os.environ["DEPTHFUSION_DATA_DIR"] = str(tmp_path)
    # Ensure no auth is configured so the sentinel dep is active.
    for var in (
        "DEPTHFUSION_JWKS_URI",
        "DEPTHFUSION_OIDC_ISSUER",
        "DEPTHFUSION_OIDC_AUDIENCE",
        "DEPTHFUSION_V2_LEGACY_AUTH",
    ):
        os.environ.pop(var, None)
    app.dependency_overrides.clear()
    client = TestClient(app, raise_server_exceptions=True)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        if "DEPTHFUSION_DATA_DIR" in os.environ:
            del os.environ["DEPTHFUSION_DATA_DIR"]


# ---------------------------------------------------------------------------
# Authentication / authorization gates
# ---------------------------------------------------------------------------


class TestRetentionAuthz:
    def test_unauthenticated_retention_rejected(self, unauth_client: TestClient) -> None:
        resp = unauth_client.post("/v2/admin/retention/enforce", json={})
        # Unconfigured auth dependency rejects (503) — never 200.
        assert resp.status_code in (401, 403, 503)

    def test_unauthenticated_export_rejected(self, unauth_client: TestClient) -> None:
        resp = unauth_client.get("/v2/admin/export")
        assert resp.status_code in (401, 403, 503)

    def test_viewer_retention_forbidden(
        self, viewer_client: tuple[TestClient, Path]
    ) -> None:
        client, _ = viewer_client
        resp = client.post("/v2/admin/retention/enforce", json={})
        assert resp.status_code == 403

    def test_viewer_export_forbidden(
        self, viewer_client: tuple[TestClient, Path]
    ) -> None:
        client, _ = viewer_client
        resp = client.get("/v2/admin/export")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Retention enforcement
# ---------------------------------------------------------------------------


class TestRetentionEnforcement:
    def test_enforce_purges_out_of_window_records(
        self, admin_client: tuple[TestClient, Path]
    ) -> None:
        client, data_dir = admin_client
        old_count, recent_count = _seed_audit_events(data_dir)

        resp = client.post("/v2/admin/retention/enforce", json={"retention_days": 365})
        assert resp.status_code == 200
        body = resp.json()

        # The old events are purged; recent events remain.
        assert body["events_purged"] == old_count
        # remaining = recent events + the enforcement's own ADMIN_ACTION event.
        assert body["events_remaining"] == recent_count + 1
        assert body["retention_days"] == 365

    def test_enforce_writes_audit_event(
        self, admin_client: tuple[TestClient, Path]
    ) -> None:
        client, data_dir = admin_client
        _seed_audit_events(data_dir)

        resp = client.post("/v2/admin/retention/enforce", json={})
        assert resp.status_code == 200

        store = AuditStore(db_path=data_dir / "audit.db")
        admin_actions = store.query(event_type=AuditEventType.ADMIN_ACTION)
        assert any(
            e["resource_id"] == "audit_retention"
            and e["actor_principal_id"] == "admin-test"
            for e in admin_actions
        )

    def test_enforce_keeps_records_when_window_large(
        self, admin_client: tuple[TestClient, Path]
    ) -> None:
        client, data_dir = admin_client
        old_count, recent_count = _seed_audit_events(data_dir)

        # A 10000-day window keeps everything.
        resp = client.post(
            "/v2/admin/retention/enforce", json={"retention_days": 10000}
        )
        assert resp.status_code == 200
        assert resp.json()["events_purged"] == 0


# ---------------------------------------------------------------------------
# Compliance export + export-controls ceiling
# ---------------------------------------------------------------------------


class TestComplianceExport:
    def test_export_permitted_classification_returns_data(
        self, admin_client: tuple[TestClient, Path]
    ) -> None:
        client, data_dir = admin_client
        _seed_audit_events(data_dir)

        resp = client.get(
            "/v2/admin/export",
            params={"classification": "internal", "export_format": "json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["classification"] == "internal"
        assert body["export_format"] == "json"
        assert body["record_count"] == len(body["events"])
        assert body["record_count"] >= 1

    def test_export_denied_for_restricted_classification(
        self, admin_client: tuple[TestClient, Path]
    ) -> None:
        client, _ = admin_client
        # RESTRICTED requires an approval token; without one the export is denied.
        resp = client.get(
            "/v2/admin/export",
            params={"classification": "restricted", "export_format": "json"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["error"] == "export_forbidden"
        assert detail["approval_required"] is True

    def test_export_denied_for_disallowed_format(
        self, admin_client: tuple[TestClient, Path]
    ) -> None:
        client, _ = admin_client
        # CONFIDENTIAL does not permit MARKDOWN export.
        resp = client.get(
            "/v2/admin/export",
            params={"classification": "confidential", "export_format": "markdown"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "export_forbidden"

    def test_export_writes_audit_event(
        self, admin_client: tuple[TestClient, Path]
    ) -> None:
        client, data_dir = admin_client
        _seed_audit_events(data_dir)

        resp = client.get(
            "/v2/admin/export",
            params={"classification": "internal", "export_format": "json"},
        )
        assert resp.status_code == 200

        store = AuditStore(db_path=data_dir / "audit.db")
        exports = store.query(event_type=AuditEventType.EXPORT_STARTED)
        assert any(
            e["resource_id"] == "compliance_export"
            and e["actor_principal_id"] == "admin-test"
            and e["success"] is True
            for e in exports
        )

    def test_denied_export_records_failed_audit_event(
        self, admin_client: tuple[TestClient, Path]
    ) -> None:
        client, data_dir = admin_client

        resp = client.get(
            "/v2/admin/export",
            params={"classification": "restricted", "export_format": "json"},
        )
        assert resp.status_code == 403

        store = AuditStore(db_path=data_dir / "audit.db")
        exports = store.query(event_type=AuditEventType.EXPORT_STARTED)
        assert any(
            e["resource_id"] == "compliance_export" and e["success"] is False
            for e in exports
        )
