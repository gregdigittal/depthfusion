"""POST /session/seed resume-from-checkpoint contract — E-73 T-862.

Proves the ``project`` → ``project_slug`` remap in the REST handler is wired:
``_tool_session_seed`` reads ``arguments["project_slug"]`` (project.py) and
``_tool_session_seed_resume`` short-circuits with
``"project_slug required for resume mode"`` without it, so a resume request
that only carried ``project`` used to be unreachable over REST.
"""
from __future__ import annotations

import json

import pytest

_PROJECT_SLUG_REQUIRED = "project_slug required for resume mode"


def _install_fake_principal(app):
    from depthfusion.api.auth import _require_principal_dep
    from depthfusion.identity.models import Principal

    fake = Principal(principal_id="greg", upn="greg@test.local")
    original = dict(app.dependency_overrides)
    app.dependency_overrides[_require_principal_dep] = lambda: fake
    return original


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))
    monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.delenv("DEPTHFUSION_QUERY_API_KEY", raising=False)
    from importlib import reload

    import depthfusion.api.rest as rest_module
    reload(rest_module)

    original = _install_fake_principal(rest_module.app)
    from fastapi.testclient import TestClient
    yield TestClient(rest_module.app)
    rest_module.app.dependency_overrides.clear()
    rest_module.app.dependency_overrides.update(original)


@pytest.fixture
def captured_args(monkeypatch) -> list[dict]:
    """Record every ``arguments`` dict handed to the real ``_tool_session_seed``."""
    import depthfusion.mcp.server as server_module

    seen: list[dict] = []
    real = server_module._tool_session_seed

    def _spy(arguments: dict) -> str:
        seen.append(dict(arguments))
        return real(arguments)

    monkeypatch.setattr(server_module, "_tool_session_seed", _spy)
    return seen


@pytest.fixture
def stub_recall(monkeypatch):
    """Keep resume mode off the recall/embedding path — it is not under test."""
    import depthfusion.hooks.session_start as session_start

    monkeypatch.setattr(session_start, "_recall_and_seed", lambda *a, **k: 0)
    monkeypatch.setattr(session_start, "_recent_git_messages", lambda *a, **k: [])


def _write_checkpoint(tmp_path, project_slug: str, checkpoint_id: str) -> None:
    d = tmp_path / "checkpoints" / project_slug
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{checkpoint_id}.json").write_text(
        json.dumps({
            "checkpoint_id": checkpoint_id,
            "session_id": "sess-1",
            "project_slug": project_slug,
            "created_at": "2026-08-05T10:00:00+00:00",
            "plan_state": "midway through T-862",
            "files_modified": ["src/depthfusion/api/rest.py"],
            "git_stash_ref": "stash@{0}",
            "context_pct_at_checkpoint": 71.5,
        }),
        encoding="utf-8",
    )


# --- AC-1 ------------------------------------------------------------------

def test_session_seed_body_declares_mode_and_checkpoint_id():
    from depthfusion.api.rest import SessionSeedBody

    fields = SessionSeedBody.model_fields
    assert "mode" in fields
    assert "checkpoint_id" in fields
    body = SessionSeedBody(project="depthfusion")
    assert body.mode is None
    assert body.checkpoint_id is None


# --- AC-2 / AC-4 -----------------------------------------------------------

def test_resume_reaches_resume_path_with_project_slug(
    client, captured_args, stub_recall, tmp_path,
):
    """mode='resume' + checkpoint_id must NOT bounce off the project_slug guard."""
    _write_checkpoint(tmp_path, "depthfusion", "cp-abc123")

    resp = client.post("/session/seed", json={
        "project": "depthfusion",
        "mode": "resume",
        "checkpoint_id": "cp-abc123",
    })
    assert resp.status_code == 200
    payload = resp.json()

    # AC-4: the guard did not fire.
    assert payload.get("error") != _PROJECT_SLUG_REQUIRED
    # The remap actually landed — resume echoes the slug it resolved.
    assert payload["project_slug"] == "depthfusion"
    # And the named checkpoint was resolved through it.
    assert payload["checkpoint"]["checkpoint_id"] == "cp-abc123"
    assert payload["recovery_command"] == "git stash pop stash@{0}"

    # AC-2: both keys present, neither replaced.
    args = captured_args[-1]
    assert args["project"] == "depthfusion"
    assert args["project_slug"] == "depthfusion"


def test_resume_without_matching_checkpoint_still_passes_the_guard(
    client, captured_args, stub_recall,
):
    """No checkpoint on disk is a *different* error than a missing project_slug."""
    resp = client.post("/session/seed", json={
        "project": "depthfusion",
        "mode": "resume",
        "checkpoint_id": "cp-missing",
    })
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("error") != _PROJECT_SLUG_REQUIRED
    assert "No checkpoint found" in payload["error"]
    assert payload["project_slug"] == "depthfusion"
    assert captured_args[-1]["project"] == "depthfusion"
    assert captured_args[-1]["project_slug"] == "depthfusion"


# --- AC-3 ------------------------------------------------------------------

def test_mode_and_checkpoint_id_omitted_leaves_recall_default_intact(
    client, captured_args, stub_recall,
):
    resp = client.post("/session/seed", json={"project": "depthfusion"})
    assert resp.status_code == 200

    args = captured_args[-1]
    assert "mode" not in args
    assert "checkpoint_id" not in args
    # Recall mode (the tool's default) ran, not resume.
    assert "checkpoint" not in resp.json()


def test_mode_and_checkpoint_id_are_forwarded(client, captured_args, stub_recall):
    client.post("/session/seed", json={
        "project": "depthfusion",
        "branch": "feat/e73-s253",
        "context": "seeded by test",
        "mode": "resume",
        "checkpoint_id": "cp-fwd",
    })
    args = captured_args[-1]
    assert args["mode"] == "resume"
    assert args["checkpoint_id"] == "cp-fwd"
    assert args["branch"] == "feat/e73-s253"
    assert args["context"] == "seeded by test"
