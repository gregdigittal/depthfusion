"""Tests for T-558: Admin CLI + API for role management with audit events.

Covers:
- CLI: cmd_assign, cmd_revoke, cmd_list, main() dispatch
- CLI: audit event written to JSONL on assign/revoke
- CLI: invalid role handling
- REST: POST /v2/admin/roles (assign + revoke)
- REST: GET /v2/admin/roles
- REST: 403 when principal lacks ASSIGN_ROLES capability
- REST: audit event written on role change
- REST: 422 on invalid role
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from depthfusion.authz.roles import Capability, Role, RoleStore
from depthfusion.cli.roles import cmd_assign, cmd_revoke, cmd_list, main
from depthfusion.identity.models import Principal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "identity.db"


@pytest.fixture()
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture()
def role_store(db_path: Path) -> RoleStore:
    return RoleStore(db_path=db_path)


@pytest.fixture()
def api_client(tmp_path: Path):
    """FastAPI TestClient with mocked principal dependency."""
    from depthfusion.api.rest import app
    from depthfusion.api.auth import _require_principal_dep

    # Override the DB path for the test
    import os
    os.environ["DEPTHFUSION_DATA_DIR"] = str(tmp_path)

    # Owner principal — has ASSIGN_ROLES + VIEW_AUDIT_LOG
    owner = Principal(
        principal_id="owner-test",
        groups=["owner"],
    )

    app.dependency_overrides[_require_principal_dep] = lambda: owner

    client = TestClient(app, raise_server_exceptions=True)
    yield client, tmp_path

    app.dependency_overrides.clear()
    if "DEPTHFUSION_DATA_DIR" in os.environ:
        del os.environ["DEPTHFUSION_DATA_DIR"]


@pytest.fixture()
def api_client_admin(tmp_path: Path):
    """FastAPI TestClient with admin principal (has VIEW_AUDIT_LOG, no ASSIGN_ROLES)."""
    from depthfusion.api.rest import app
    from depthfusion.api.auth import _require_principal_dep

    import os
    os.environ["DEPTHFUSION_DATA_DIR"] = str(tmp_path)

    admin = Principal(
        principal_id="admin-test",
        groups=["admin"],
    )

    app.dependency_overrides[_require_principal_dep] = lambda: admin

    client = TestClient(app, raise_server_exceptions=True)
    yield client, tmp_path

    app.dependency_overrides.clear()
    if "DEPTHFUSION_DATA_DIR" in os.environ:
        del os.environ["DEPTHFUSION_DATA_DIR"]


@pytest.fixture()
def api_client_viewer(tmp_path: Path):
    """FastAPI TestClient with viewer principal (no ASSIGN_ROLES, no VIEW_AUDIT_LOG)."""
    from depthfusion.api.rest import app
    from depthfusion.api.auth import _require_principal_dep

    import os
    os.environ["DEPTHFUSION_DATA_DIR"] = str(tmp_path)

    viewer = Principal(
        principal_id="viewer-test",
        groups=["viewer"],
    )

    app.dependency_overrides[_require_principal_dep] = lambda: viewer

    client = TestClient(app, raise_server_exceptions=True)
    yield client, tmp_path

    app.dependency_overrides.clear()
    if "DEPTHFUSION_DATA_DIR" in os.environ:
        del os.environ["DEPTHFUSION_DATA_DIR"]


# ---------------------------------------------------------------------------
# CLI: cmd_assign
# ---------------------------------------------------------------------------


class TestCmdAssign:
    def test_assign_valid_role_returns_zero(
        self, db_path: Path, audit_path: Path
    ) -> None:
        rc = cmd_assign("user-1", "member", db_path=db_path, audit_path=audit_path)
        assert rc == 0

    def test_assign_creates_role_assignment(
        self, db_path: Path, audit_path: Path
    ) -> None:
        cmd_assign("user-2", "viewer", db_path=db_path, audit_path=audit_path)
        store = RoleStore(db_path=db_path)
        roles = store.get_roles("user-2")
        assert Role.VIEWER in roles

    def test_assign_invalid_role_returns_two(
        self, db_path: Path, audit_path: Path
    ) -> None:
        rc = cmd_assign("user-3", "superadmin", db_path=db_path, audit_path=audit_path)
        assert rc == 2

    def test_assign_writes_audit_event(
        self, db_path: Path, audit_path: Path
    ) -> None:
        cmd_assign("user-4", "admin", db_path=db_path, audit_path=audit_path)
        assert audit_path.exists()
        lines = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        ev = lines[0]
        assert ev["action"] == "role_assigned"
        assert ev["principal_id"] == "user-4"
        assert ev["role"] == "admin"
        assert ev["result"] == "ok"
        assert ev["actor"] == "cli"

    def test_assign_all_valid_roles(
        self, db_path: Path, audit_path: Path
    ) -> None:
        for i, role in enumerate(Role):
            rc = cmd_assign(f"user-role-{i}", role.value, db_path=db_path, audit_path=audit_path)
            assert rc == 0, f"assign failed for role {role.value}"


# ---------------------------------------------------------------------------
# CLI: cmd_revoke
# ---------------------------------------------------------------------------


class TestCmdRevoke:
    def test_revoke_existing_role_returns_zero(
        self, db_path: Path, audit_path: Path
    ) -> None:
        store = RoleStore(db_path=db_path)
        store.grant("user-5", Role.MEMBER, granted_by="setup")
        rc = cmd_revoke("user-5", "member", db_path=db_path, audit_path=audit_path)
        assert rc == 0

    def test_revoke_removes_role(
        self, db_path: Path, audit_path: Path
    ) -> None:
        store = RoleStore(db_path=db_path)
        store.grant("user-6", Role.ADMIN, granted_by="setup")
        cmd_revoke("user-6", "admin", db_path=db_path, audit_path=audit_path)
        assert Role.ADMIN not in store.get_roles("user-6")

    def test_revoke_nonexistent_role_returns_one(
        self, db_path: Path, audit_path: Path
    ) -> None:
        rc = cmd_revoke("nobody", "owner", db_path=db_path, audit_path=audit_path)
        assert rc == 1

    def test_revoke_invalid_role_returns_two(
        self, db_path: Path, audit_path: Path
    ) -> None:
        rc = cmd_revoke("user-7", "god", db_path=db_path, audit_path=audit_path)
        assert rc == 2

    def test_revoke_writes_audit_event_ok(
        self, db_path: Path, audit_path: Path
    ) -> None:
        store = RoleStore(db_path=db_path)
        store.grant("user-8", Role.VIEWER, granted_by="setup")
        cmd_revoke("user-8", "viewer", db_path=db_path, audit_path=audit_path)
        lines = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
        ev = lines[-1]
        assert ev["action"] == "role_revoked"
        assert ev["result"] == "ok"

    def test_revoke_writes_audit_event_not_found(
        self, db_path: Path, audit_path: Path
    ) -> None:
        cmd_revoke("ghost", "member", db_path=db_path, audit_path=audit_path)
        lines = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
        ev = lines[-1]
        assert ev["action"] == "role_revoked"
        assert ev["result"] == "not_found"


# ---------------------------------------------------------------------------
# CLI: cmd_list
# ---------------------------------------------------------------------------


class TestCmdList:
    def test_list_empty_returns_one(self, db_path: Path) -> None:
        rc = cmd_list(db_path=db_path)
        assert rc == 1

    def test_list_with_assignments_returns_zero(self, db_path: Path) -> None:
        store = RoleStore(db_path=db_path)
        store.grant("user-9", Role.MEMBER, granted_by="admin-0")
        rc = cmd_list(db_path=db_path)
        assert rc == 0

    def test_list_prints_assignments(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store = RoleStore(db_path=db_path)
        store.grant("user-10", Role.VIEWER, granted_by="admin-0")
        cmd_list(db_path=db_path)
        captured = capsys.readouterr()
        assert "user-10" in captured.out
        assert "viewer" in captured.out


# ---------------------------------------------------------------------------
# CLI: main() dispatch
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_help_returns_zero(self) -> None:
        assert main(["--help"]) == 0

    def test_main_no_args_returns_zero(self) -> None:
        assert main([]) == 0

    def test_main_unknown_subcommand_returns_two(self) -> None:
        assert main(["frobble"]) == 2

    def test_main_assign_missing_args_returns_two(self) -> None:
        assert main(["assign"]) == 2
        assert main(["assign", "only-one"]) == 2

    def test_main_revoke_missing_args_returns_two(self) -> None:
        assert main(["revoke"]) == 2
        assert main(["revoke", "only-one"]) == 2

    def test_main_assign_dispatches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os
        monkeypatch.setenv("DEPTHFUSION_DATA_DIR", str(tmp_path))
        rc = main(["assign", "main-test-user", "viewer"])
        assert rc == 0

    def test_main_list_dispatches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os
        monkeypatch.setenv("DEPTHFUSION_DATA_DIR", str(tmp_path))
        rc = main(["list"])
        # Returns 1 when no assignments — that is correct behaviour
        assert rc in (0, 1)


# ---------------------------------------------------------------------------
# REST: POST /v2/admin/roles
# ---------------------------------------------------------------------------


class TestRoleAdminEndpointPost:
    def test_assign_role_returns_200(self, api_client) -> None:
        client, tmp_path = api_client
        resp = client.post(
            "/v2/admin/roles",
            json={"principal_id": "rest-user-1", "role": "member", "action": "assign"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "ok"
        assert data["action"] == "assign"
        assert data["role"] == "member"

    def test_assign_creates_db_record(self, api_client) -> None:
        client, tmp_path = api_client
        client.post(
            "/v2/admin/roles",
            json={"principal_id": "rest-user-2", "role": "viewer", "action": "assign"},
        )
        store = RoleStore(db_path=tmp_path / "identity.db")
        assert Role.VIEWER in store.get_roles("rest-user-2")

    def test_revoke_existing_role_returns_ok(self, api_client) -> None:
        client, tmp_path = api_client
        store = RoleStore(db_path=tmp_path / "identity.db")
        store.grant("rest-user-3", Role.ADMIN, granted_by="setup")

        resp = client.post(
            "/v2/admin/roles",
            json={"principal_id": "rest-user-3", "role": "admin", "action": "revoke"},
        )
        assert resp.status_code == 200
        assert resp.json()["result"] == "ok"

    def test_revoke_nonexistent_returns_not_found(self, api_client) -> None:
        client, _ = api_client
        resp = client.post(
            "/v2/admin/roles",
            json={"principal_id": "ghost-user", "role": "member", "action": "revoke"},
        )
        assert resp.status_code == 200
        assert resp.json()["result"] == "not_found"

    def test_invalid_role_returns_422(self, api_client) -> None:
        client, _ = api_client
        resp = client.post(
            "/v2/admin/roles",
            json={"principal_id": "rest-user-4", "role": "superadmin", "action": "assign"},
        )
        assert resp.status_code == 422

    def test_requires_assign_roles_capability(self, api_client_admin) -> None:
        """Admin has VIEW_AUDIT_LOG but NOT ASSIGN_ROLES — must get 403."""
        client, _ = api_client_admin
        resp = client.post(
            "/v2/admin/roles",
            json={"principal_id": "rest-user-5", "role": "member", "action": "assign"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["error"] == "forbidden"

    def test_viewer_cannot_assign_roles(self, api_client_viewer) -> None:
        client, _ = api_client_viewer
        resp = client.post(
            "/v2/admin/roles",
            json={"principal_id": "rest-user-6", "role": "viewer", "action": "assign"},
        )
        assert resp.status_code == 403

    def test_assign_writes_audit_event(self, api_client) -> None:
        client, tmp_path = api_client
        client.post(
            "/v2/admin/roles",
            json={"principal_id": "rest-user-audit", "role": "member", "action": "assign"},
        )
        audit_path = tmp_path / "audit.jsonl"
        assert audit_path.exists()
        lines = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
        assert any(
            ev["action"] == "role_assigned"
            and ev["principal_id"] == "rest-user-audit"
            for ev in lines
        )

    def test_revoke_writes_audit_event(self, api_client) -> None:
        client, tmp_path = api_client
        store = RoleStore(db_path=tmp_path / "identity.db")
        store.grant("rest-user-rev-audit", Role.VIEWER, granted_by="setup")

        client.post(
            "/v2/admin/roles",
            json={"principal_id": "rest-user-rev-audit", "role": "viewer", "action": "revoke"},
        )
        audit_path = tmp_path / "audit.jsonl"
        lines = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
        assert any(
            ev["action"] == "role_revoked"
            and ev["principal_id"] == "rest-user-rev-audit"
            for ev in lines
        )

    def test_audit_event_contains_actor_principal_id(self, api_client) -> None:
        client, tmp_path = api_client
        client.post(
            "/v2/admin/roles",
            json={"principal_id": "rest-user-actor", "role": "member", "action": "assign"},
        )
        audit_path = tmp_path / "audit.jsonl"
        lines = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
        ev = next(e for e in lines if e["principal_id"] == "rest-user-actor")
        assert ev["actor"] == "owner-test"


# ---------------------------------------------------------------------------
# REST: GET /v2/admin/roles
# ---------------------------------------------------------------------------


class TestRoleAdminEndpointGet:
    def test_list_returns_200(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/v2/admin/roles")
        assert resp.status_code == 200

    def test_list_returns_assignments(self, api_client) -> None:
        client, tmp_path = api_client
        store = RoleStore(db_path=tmp_path / "identity.db")
        store.grant("list-user-1", Role.MEMBER, granted_by="setup")

        resp = client.get("/v2/admin/roles")
        assert resp.status_code == 200
        data = resp.json()
        assert "assignments" in data
        assert "count" in data
        ids = [a["principal_id"] for a in data["assignments"]]
        assert "list-user-1" in ids

    def test_list_includes_role_fields(self, api_client) -> None:
        client, tmp_path = api_client
        store = RoleStore(db_path=tmp_path / "identity.db")
        store.grant("list-user-2", Role.VIEWER, granted_by="admin-0")

        resp = client.get("/v2/admin/roles")
        data = resp.json()
        entry = next(
            a for a in data["assignments"] if a["principal_id"] == "list-user-2"
        )
        assert entry["role"] == "viewer"
        assert entry["granted_by"] == "admin-0"
        assert isinstance(entry["granted_at"], float)

    def test_admin_can_list_roles(self, api_client_admin) -> None:
        """Admin has VIEW_AUDIT_LOG — listing is allowed."""
        client, _ = api_client_admin
        resp = client.get("/v2/admin/roles")
        assert resp.status_code == 200

    def test_viewer_cannot_list_roles(self, api_client_viewer) -> None:
        """Viewer lacks VIEW_AUDIT_LOG — listing is forbidden."""
        client, _ = api_client_viewer
        resp = client.get("/v2/admin/roles")
        assert resp.status_code == 403
