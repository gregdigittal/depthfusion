"""Tests for E-60 admin console policy/compliance routes.

T-671: Retention enforcement + compliance export.
T-675: Policy + classification editors.

Covers, for every route:
- Authorized principal (owner / admin holding MANAGE_SETTINGS) → 200 + payload
- Unauthorized principal (viewer lacking MANAGE_SETTINGS) → 403

The admin_console router gates every one of these on
``Capability.MANAGE_SETTINGS`` via the shared ``_enforce`` helper, so a viewer
(who has only read capabilities) must be rejected.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from depthfusion.audit.log import AuditEvent, AuditEventType, AuditStore
from depthfusion.identity.models import Principal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_client(tmp_path: Path, groups: list[str]):
    from depthfusion.api.auth import _require_principal_dep
    from depthfusion.api.rest import app

    os.environ["DEPTHFUSION_DATA_DIR"] = str(tmp_path)

    principal = Principal(principal_id=f"{groups[0]}-test", groups=groups)
    app.dependency_overrides[_require_principal_dep] = lambda: principal

    client = TestClient(app, raise_server_exceptions=True)
    return client


@pytest.fixture()
def owner_client(tmp_path: Path):
    """Owner holds every capability, including MANAGE_SETTINGS."""
    client = _make_client(tmp_path, ["owner"])
    yield client, tmp_path
    from depthfusion.api.rest import app

    app.dependency_overrides.clear()
    os.environ.pop("DEPTHFUSION_DATA_DIR", None)


@pytest.fixture()
def admin_client(tmp_path: Path):
    """Admin holds MANAGE_SETTINGS."""
    client = _make_client(tmp_path, ["admin"])
    yield client, tmp_path
    from depthfusion.api.rest import app

    app.dependency_overrides.clear()
    os.environ.pop("DEPTHFUSION_DATA_DIR", None)


@pytest.fixture()
def viewer_client(tmp_path: Path):
    """Viewer lacks MANAGE_SETTINGS — must be rejected with 403."""
    client = _make_client(tmp_path, ["viewer"])
    yield client, tmp_path
    from depthfusion.api.rest import app

    app.dependency_overrides.clear()
    os.environ.pop("DEPTHFUSION_DATA_DIR", None)


@pytest.fixture(autouse=True)
def _reset_policy_state():
    """Restore the in-process policy state after each test."""
    from depthfusion.api import admin_console

    admin_console.policy_state.__init__()  # type: ignore[misc]
    yield
    admin_console.policy_state.__init__()  # type: ignore[misc]


def _seed_audit(tmp_path: Path, count: int, *, old: bool = False) -> None:
    store = AuditStore(db_path=tmp_path / "audit.db")
    ts = time.time() - (400 * 86400) if old else time.time()
    for i in range(count):
        store.log(
            AuditEvent(
                event_type=AuditEventType.RECORD_READ,
                actor_principal_id=f"seed-{i}",
                resource_id=f"res-{i}",
                timestamp=ts,
            )
        )


# ---------------------------------------------------------------------------
# T-671: Retention enforcement
# ---------------------------------------------------------------------------


class TestRetentionEnforce:
    def test_owner_enforce_returns_200(self, owner_client) -> None:
        client, tmp_path = owner_client
        _seed_audit(tmp_path, 3, old=True)
        resp = client.post("/v2/admin/retention/enforce", json={"retention_days": 30})
        assert resp.status_code == 200
        data = resp.json()
        assert data["retention_days"] == 30
        # 3 old events purged; the enforcement event itself remains
        assert data["events_purged"] == 3
        assert data["events_remaining"] >= 1

    def test_admin_enforce_returns_200(self, admin_client) -> None:
        client, _ = admin_client
        resp = client.post("/v2/admin/retention/enforce", json={})
        assert resp.status_code == 200

    def test_enforce_keeps_recent_events(self, owner_client) -> None:
        client, tmp_path = owner_client
        _seed_audit(tmp_path, 5, old=False)
        resp = client.post("/v2/admin/retention/enforce", json={"retention_days": 365})
        assert resp.status_code == 200
        assert resp.json()["events_purged"] == 0

    def test_viewer_enforce_returns_403(self, viewer_client) -> None:
        client, _ = viewer_client
        resp = client.post("/v2/admin/retention/enforce", json={"retention_days": 30})
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "forbidden"

    def test_invalid_retention_days_returns_422(self, owner_client) -> None:
        client, _ = owner_client
        resp = client.post("/v2/admin/retention/enforce", json={"retention_days": -5})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# T-671: Compliance export
# ---------------------------------------------------------------------------


class TestComplianceExport:
    def test_owner_export_returns_200(self, owner_client) -> None:
        client, tmp_path = owner_client
        _seed_audit(tmp_path, 4)
        resp = client.get("/v2/admin/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["record_count"] >= 4
        assert isinstance(data["events"], list)
        assert "exported_at" in data

    def test_admin_export_returns_200(self, admin_client) -> None:
        client, _ = admin_client
        resp = client.get("/v2/admin/export")
        assert resp.status_code == 200

    def test_viewer_export_returns_403(self, viewer_client) -> None:
        client, _ = viewer_client
        resp = client.get("/v2/admin/export")
        assert resp.status_code == 403

    def test_export_invalid_since_returns_422(self, owner_client) -> None:
        client, _ = owner_client
        resp = client.get("/v2/admin/export", params={"since": "not-a-date"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# T-675: Export-policy editor
# ---------------------------------------------------------------------------


class TestExportPolicyEditor:
    def test_owner_get_policy_returns_200(self, owner_client) -> None:
        client, _ = owner_client
        resp = client.get("/v2/admin/policy")
        assert resp.status_code == 200
        data = resp.json()
        assert "policies" in data
        assert "public" in data["policies"]

    def test_admin_get_policy_returns_200(self, admin_client) -> None:
        client, _ = admin_client
        resp = client.get("/v2/admin/policy")
        assert resp.status_code == 200

    def test_viewer_get_policy_returns_403(self, viewer_client) -> None:
        client, _ = viewer_client
        resp = client.get("/v2/admin/policy")
        assert resp.status_code == 403

    def test_owner_put_policy_replaces_matrix(self, owner_client) -> None:
        client, _ = owner_client
        body = {
            "policies": {
                "public": {
                    "allowed_export_formats": ["json"],
                    "watermark_required": True,
                    "approval_required": False,
                }
            }
        }
        resp = client.put("/v2/admin/policy", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["policies"]["public"]["watermark_required"] is True
        assert data["policies"]["public"]["allowed_export_formats"] == ["json"]
        # GET reflects the edit
        get = client.get("/v2/admin/policy")
        assert get.json()["policies"]["public"]["watermark_required"] is True

    def test_viewer_put_policy_returns_403(self, viewer_client) -> None:
        client, _ = viewer_client
        resp = client.put(
            "/v2/admin/policy",
            json={"policies": {"public": {"allowed_export_formats": ["json"]}}},
        )
        assert resp.status_code == 403

    def test_put_policy_invalid_level_returns_422(self, owner_client) -> None:
        client, _ = owner_client
        resp = client.put(
            "/v2/admin/policy",
            json={"policies": {"top-secret": {"allowed_export_formats": ["json"]}}},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# T-675: Classification editor
# ---------------------------------------------------------------------------


class TestClassificationEditor:
    def test_owner_get_classification_returns_200(self, owner_client) -> None:
        client, _ = owner_client
        resp = client.get("/v2/admin/classification")
        assert resp.status_code == 200
        data = resp.json()
        assert "rules" in data
        assert "restricted" in data["rules"]

    def test_admin_get_classification_returns_200(self, admin_client) -> None:
        client, _ = admin_client
        resp = client.get("/v2/admin/classification")
        assert resp.status_code == 200

    def test_viewer_get_classification_returns_403(self, viewer_client) -> None:
        client, _ = viewer_client
        resp = client.get("/v2/admin/classification")
        assert resp.status_code == 403

    def test_owner_put_classification_replaces_rules(self, owner_client) -> None:
        client, _ = owner_client
        body = {
            "rules": {
                "internal": {
                    "export_allowed": True,
                    "cache_allowed": False,
                    "redact_in_search": True,
                    "allowed_roles": ["admin", "analyst"],
                }
            }
        }
        resp = client.put("/v2/admin/classification", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["rules"]["internal"]["export_allowed"] is True
        assert data["rules"]["internal"]["allowed_roles"] == ["admin", "analyst"]

    def test_viewer_put_classification_returns_403(self, viewer_client) -> None:
        client, _ = viewer_client
        resp = client.put(
            "/v2/admin/classification",
            json={"rules": {"internal": {"export_allowed": True}}},
        )
        assert resp.status_code == 403

    def test_put_classification_invalid_role_returns_422(self, owner_client) -> None:
        client, _ = owner_client
        resp = client.put(
            "/v2/admin/classification",
            json={"rules": {"internal": {"allowed_roles": ["superuser"]}}},
        )
        assert resp.status_code == 422
