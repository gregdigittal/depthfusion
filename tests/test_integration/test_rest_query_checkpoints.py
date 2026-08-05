"""Integration tests for GET /query/checkpoints — E-73 S-253 / T-860."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT = "depthfusion"

# Expected wire keys — exactly CheckpointRecord.to_dict() (AC-4: no renaming,
# no extra fields).
_EXPECTED_KEYS = {
    "checkpoint_id",
    "session_id",
    "project_slug",
    "created_at",
    "files_modified",
    "plan_state",
    "git_stash_ref",
    "context_pct_at_checkpoint",
    # T-863: best-effort side-channel (git diffs). Additive — always present in
    # to_dict(), defaulting to {} for records written before the field existed.
    "metadata",
}


def _install_fake_principal(app):
    """Override _require_principal_dep with a no-op that returns a test principal.

    Without this, _UnconfiguredPrincipalDep raises 503 for every protected route
    when DEPTHFUSION_JWKS_URI / OIDC_ISSUER / OIDC_AUDIENCE are absent (always
    true in the test environment).  Returns the original overrides so callers
    can restore state.
    """
    from depthfusion.api.auth import _require_principal_dep
    from depthfusion.identity.models import Principal

    fake = Principal(principal_id="greg", upn="greg@test.local")
    original = dict(app.dependency_overrides)
    app.dependency_overrides[_require_principal_dep] = lambda: fake
    return original


@pytest.fixture
def checkpoint_dir(tmp_path, monkeypatch) -> Path:
    """Point DEPTHFUSION_CHECKPOINT_DIR at a temp root and return it."""
    root = tmp_path / "checkpoints"
    root.mkdir()
    monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_DIR", str(root))
    return root


@pytest.fixture
def client(tmp_path, monkeypatch, checkpoint_dir):
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))
    monkeypatch.delenv("DEPTHFUSION_QUERY_API_KEY", raising=False)
    from importlib import reload

    import depthfusion.api.rest as rest_module
    reload(rest_module)

    original = _install_fake_principal(rest_module.app)
    from fastapi.testclient import TestClient
    yield TestClient(rest_module.app)
    rest_module.app.dependency_overrides.clear()
    rest_module.app.dependency_overrides.update(original)


def _seed(checkpoint_dir: Path, project: str = PROJECT) -> list[str]:
    """Write 3 CheckpointRecord JSON files directly to disk, oldest first.

    Deliberately bypasses EventStore.publish_checkpoint (async — it would need
    a running event loop) and writes the persisted layout
    ``<root>/<project_slug>/<checkpoint_id>.json`` that list_checkpoints reads.
    Returns the checkpoint ids in newest-first order.
    """
    d = checkpoint_dir / project
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        ("cp-oldest", "2026-08-01T10:00:00+00:00", "plan A"),
        ("cp-middle", "2026-08-02T10:00:00+00:00", "plan B"),
        ("cp-newest", "2026-08-03T10:00:00+00:00", "plan C"),
    ]
    for cp_id, created_at, plan_state in rows:
        (d / f"{cp_id}.json").write_text(
            json.dumps(
                {
                    "checkpoint_id": cp_id,
                    "session_id": f"sess-{cp_id}",
                    "project_slug": project,
                    "created_at": created_at,
                    "plan_state": plan_state,
                    "files_modified": ["src/depthfusion/api/query.py"],
                    "git_stash_ref": None,
                    "context_pct_at_checkpoint": 42.5,
                }
            ),
            encoding="utf-8",
        )
    return ["cp-newest", "cp-middle", "cp-oldest"]


# ---------------------------------------------------------------------------
# GET /query/checkpoints — route contract
# ---------------------------------------------------------------------------

def test_checkpoints_returns_seeded_records_newest_first(client, checkpoint_dir):
    expected_order = _seed(checkpoint_dir)

    resp = client.get(f"/query/checkpoints?project={PROJECT}")
    assert resp.status_code == 200
    data = resp.json()

    # AC-6: 3 seeded → 3 returned, with the aggregate counters agreeing.
    assert data["count"] == 3
    assert data["total"] == 3
    assert len(data["items"]) == 3

    # AC-6: newest-first ordering (list_checkpoints sorts created_at DESC).
    assert [i["checkpoint_id"] for i in data["items"]] == expected_order
    created = [i["created_at"] for i in data["items"]]
    assert created == sorted(created, reverse=True)


def test_checkpoints_item_keys_are_exactly_to_dict_keys(client, checkpoint_dir):
    _seed(checkpoint_dir)

    resp = client.get(f"/query/checkpoints?project={PROJECT}")
    assert resp.status_code == 200
    item = resp.json()["items"][0]

    # AC-4: no renaming, no extra wire fields.
    assert set(item.keys()) == _EXPECTED_KEYS
    assert item["plan_state"] == "plan C"
    assert item["session_id"] == "sess-cp-newest"
    assert item["project_slug"] == PROJECT
    assert item["files_modified"] == ["src/depthfusion/api/query.py"]
    assert item["git_stash_ref"] is None
    assert item["context_pct_at_checkpoint"] == 42.5


def test_checkpoints_limit_applied(client, checkpoint_dir):
    _seed(checkpoint_dir)

    resp = client.get(f"/query/checkpoints?project={PROJECT}&limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert [i["checkpoint_id"] for i in data["items"]] == ["cp-newest", "cp-middle"]


def test_checkpoints_limit_bounds_enforced(client, checkpoint_dir):
    _seed(checkpoint_dir)
    assert client.get("/query/checkpoints?limit=0").status_code == 422
    assert client.get("/query/checkpoints?limit=1001").status_code == 422


def test_checkpoints_unknown_project_returns_empty(client, checkpoint_dir):
    _seed(checkpoint_dir)

    resp = client.get("/query/checkpoints?project=no-such-project")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "count": 0}


def test_checkpoints_missing_project_param_lists_every_project(client, checkpoint_dir):
    """An absent project slug means all projects — never an empty page.

    Replaces an earlier assertion that an absent slug returned 0 records. That
    behaviour made the dashboard tile (which sends no project, having no project
    selector) permanently empty, so it was a defect rather than a contract.
    """
    _seed(checkpoint_dir)

    resp = client.get("/query/checkpoints")
    assert resp.status_code == 200
    assert resp.json()["count"] == 3


def test_checkpoints_skips_malformed_records(client, checkpoint_dir):
    _seed(checkpoint_dir)
    (checkpoint_dir / PROJECT / "broken.json").write_text("{not json", encoding="utf-8")
    (checkpoint_dir / PROJECT / "no-id.json").write_text('{"session_id": "x"}', encoding="utf-8")

    resp = client.get(f"/query/checkpoints?project={PROJECT}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert "broken" not in [i["checkpoint_id"] for i in data["items"]]


def test_checkpoints_route_is_registered(client):
    """AC-7: fails if the /query/checkpoints route is removed."""
    import depthfusion.api.rest as rest_module

    paths = {r.path for r in rest_module.app.routes if hasattr(r, "path")}
    assert "/query/checkpoints" in paths


# ---------------------------------------------------------------------------
# list_checkpoints — storage primitive contract
# ---------------------------------------------------------------------------

def test_list_checkpoints_exists_and_sorts_descending(checkpoint_dir):
    """AC-7: fails if the module-level list_checkpoints is removed."""
    from depthfusion.core.event_store import CheckpointRecord, list_checkpoints

    expected_order = _seed(checkpoint_dir)

    records = list_checkpoints(PROJECT)
    assert all(isinstance(r, CheckpointRecord) for r in records)
    assert [r.checkpoint_id for r in records] == expected_order

    # limit slice keeps the newest records.
    assert [r.checkpoint_id for r in list_checkpoints(PROJECT, limit=1)] == ["cp-newest"]

    # cursor is accepted but not interpreted (single limit-bounded page).
    assert list_checkpoints(PROJECT, limit=3, cursor="anything") == records


def test_list_checkpoints_missing_dir_returns_empty(checkpoint_dir):
    from depthfusion.core.event_store import list_checkpoints

    assert list_checkpoints("never-seen") == []


def test_list_checkpoints_is_graph_free(checkpoint_dir, monkeypatch):
    """Regression guard: the read path must not construct a GraphBackend.

    Before the fix, api.query.query_checkpoints built an ``EventStore()`` whose
    lazy ``graph`` property called ``graph.store.get_store()``.  Detonating
    get_store() proves the filesystem read path never reaches it.
    """
    import depthfusion.graph.store as graph_store

    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("checkpoint read path opened a graph backend")

    monkeypatch.setattr(graph_store, "get_store", _boom)

    from depthfusion.api.query import query_checkpoints
    from depthfusion.core.event_store import list_checkpoints

    _seed(checkpoint_dir)
    assert len(list_checkpoints(PROJECT)) == 3
    assert query_checkpoints(project_slug=PROJECT)["count"] == 3


# ---------------------------------------------------------------------------
# Unscoped (all-projects) listing — the dashboard tile has no project selector
# ---------------------------------------------------------------------------

def test_list_checkpoints_without_project_merges_all_projects(checkpoint_dir):
    """An omitted project must NOT yield an always-empty list.

    ``Path(base) / ""`` collapses to ``base`` itself, whose top level holds no
    ``*.json`` — so a per-project-only implementation returns [] for every
    unscoped caller.  Unscoped means all projects, sorted globally.
    """
    from depthfusion.core.event_store import list_checkpoints

    _seed(checkpoint_dir, project=PROJECT)
    _seed(checkpoint_dir, project="other-project")

    for unscoped in (None, ""):
        records = list_checkpoints(unscoped)
        assert len(records) == 6, unscoped
        assert {r.project_slug for r in records} == {PROJECT, "other-project"}
        created = [r.created_at for r in records]
        assert created == sorted(created, reverse=True)

    # limit still applies across the merged set.
    assert len(list_checkpoints(None, limit=2)) == 2


def test_checkpoints_route_without_project_returns_all_projects(client, checkpoint_dir):
    """AC-2 end to end: the tile's default (no project) shows real rows."""
    _seed(checkpoint_dir, project=PROJECT)
    _seed(checkpoint_dir, project="other-project")

    resp = client.get("/query/checkpoints")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 6
    assert {i["project_slug"] for i in data["items"]} == {PROJECT, "other-project"}
