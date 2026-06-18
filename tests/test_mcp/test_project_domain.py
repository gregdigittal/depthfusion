"""Coverage for depthfusion.mcp.tools.project — error paths and happy paths.

Targets ~50 previously-uncovered lines:
  _tool_register_project  : 36-54 (missing-args, bad-path, success)
  _tool_list_projects     : 57-68 (list + empty)
  _tool_sync_project      : 72-73, 78-83, 85/96-98 (missing-slug, not-found, success)
  _tool_ingest_project    : 104-107, 109/122-135 (validation, github, local, exception)
  register_project stub   : 260-262
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from depthfusion.mcp.tools.project import (
    _tool_ingest_project,
    _tool_list_projects,
    _tool_register_project,
    _tool_session_seed,
    _tool_sync_project,
    register_project,
)

# ── _tool_register_project ────────────────────────────────────────────────────

def test_register_project_all_missing():
    """Lines 42-43: no args → error JSON (slug/name/local_path required)."""
    result = _tool_register_project({})
    data = json.loads(result)
    assert "error" in data
    assert "required" in data["error"]


def test_register_project_missing_name():
    """Lines 42-43: missing name → same validation error."""
    result = _tool_register_project({"slug": "p", "local_path": "/x"})
    data = json.loads(result)
    assert "error" in data


def test_register_project_nonexistent_path(tmp_path):
    """Lines 44-45: path does not exist → error JSON."""
    result = _tool_register_project({
        "slug": "myproject",
        "name": "My Project",
        "local_path": str(tmp_path / "does-not-exist"),
    })
    data = json.loads(result)
    assert "error" in data
    assert "does not exist" in data["error"]


def test_register_project_success(tmp_path):
    """Lines 46-54: valid args + existing path + mocked registry → success JSON."""
    mock_entry = MagicMock()
    mock_entry.slug = "myproject"
    mock_entry.name = "My Project"
    mock_entry.local_path = str(tmp_path)

    with patch("depthfusion.mcp.tools.project.ProjectRegistry") as mock_reg:
        mock_reg.return_value.register.return_value = mock_entry
        result = _tool_register_project({
            "slug": "myproject",
            "name": "My Project",
            "local_path": str(tmp_path),
        })

    data = json.loads(result)
    assert data["registered"] is True
    assert data["slug"] == "myproject"
    assert data["name"] == "My Project"


# ── _tool_list_projects ───────────────────────────────────────────────────────

def test_list_projects_returns_entries():
    """Lines 57-68: one entry → list JSON with slug/name/path."""
    mock_entry = MagicMock()
    mock_entry.slug = "proj-a"
    mock_entry.name = "Project A"
    mock_entry.local_path = "/home/user/proj-a"
    mock_entry.github_url = "https://github.com/owner/proj-a"
    mock_entry.last_synced = None
    mock_entry.description = "Test project"

    with patch("depthfusion.mcp.tools.project.ProjectRegistry") as mock_reg:
        mock_reg.return_value.list_projects.return_value = [mock_entry]
        result = _tool_list_projects({})

    data = json.loads(result)
    assert "projects" in data
    assert len(data["projects"]) == 1
    assert data["projects"][0]["slug"] == "proj-a"


def test_list_projects_empty():
    """Lines 57-68: empty registry → empty list."""
    with patch("depthfusion.mcp.tools.project.ProjectRegistry") as mock_reg:
        mock_reg.return_value.list_projects.return_value = []
        result = _tool_list_projects({})

    data = json.loads(result)
    assert data["projects"] == []


# ── _tool_sync_project ────────────────────────────────────────────────────────

def test_sync_project_missing_slug():
    """Lines 72-73: empty slug → error JSON."""
    result = _tool_sync_project({})
    data = json.loads(result)
    assert data["error"] == "slug is required"


def test_sync_project_not_registered():
    """Lines 78-83: slug not in registry → error JSON.

    Patches at depthfusion.core.project_registry so the function-level
    re-import inside _tool_sync_project also gets the mock.
    """
    with patch("depthfusion.core.project_registry.ProjectRegistry") as mock_reg:
        mock_reg.return_value.get.return_value = None
        result = _tool_sync_project({"slug": "project-that-does-not-exist"})

    data = json.loads(result)
    assert "error" in data
    assert "not registered" in data["error"]


def test_sync_project_success():
    """Lines 85, 96-98: happy path — mocked registry entry + sync impl."""
    mock_entry = MagicMock()
    mock_entry.local_path = "/some/project"

    with patch("depthfusion.core.project_registry.ProjectRegistry") as mock_reg, \
         patch("depthfusion.mcp.tools.project._sync_project_impl") as mock_sync:
        mock_reg.return_value.get.return_value = mock_entry
        mock_sync.return_value = {"files": 3}
        result = _tool_sync_project({"slug": "my-project"})

    data = json.loads(result)
    assert data["synced"] is True
    assert data["slug"] == "my-project"
    assert data["results"] == {"files": 3}


# ── _tool_ingest_project ──────────────────────────────────────────────────────

def test_ingest_project_missing_slug_and_source():
    """Lines 104-105: both missing → error JSON."""
    result = _tool_ingest_project({})
    data = json.loads(result)
    assert "error" in data
    assert "required" in data["error"]


def test_ingest_project_missing_source():
    """Lines 104-105: slug present but source absent → error JSON."""
    result = _tool_ingest_project({"slug": "proj"})
    data = json.loads(result)
    assert "error" in data


def test_ingest_project_bad_mode():
    """Lines 106-107: mode not in (structural, full) → error JSON."""
    result = _tool_ingest_project({
        "slug": "proj",
        "source": "/some/path",
        "mode": "invalid-mode",
    })
    data = json.loads(result)
    assert "error" in data
    assert "mode" in data["error"]


def test_ingest_project_github_source():
    """Lines 109, 122-130, 133: github URL → ingest_github called, success JSON."""
    mock_ingestor = MagicMock()
    mock_ingestor.ingest_github.return_value = {"files_ingested": 5}

    with patch("depthfusion.mcp.tools.project.ProjectIngestor", return_value=mock_ingestor):
        result = _tool_ingest_project({
            "slug": "proj",
            "source": "https://github.com/owner/repo",
            "mode": "structural",
        })

    data = json.loads(result)
    assert data["ingested"] is True
    assert data["slug"] == "proj"
    mock_ingestor.ingest_github.assert_called_once()


def test_ingest_project_local_source(tmp_path):
    """Lines 109, 122-128, 132-133: local path → ingest_local called, success JSON."""
    mock_ingestor = MagicMock()
    mock_ingestor.ingest_local.return_value = {"files_ingested": 3}

    with patch("depthfusion.mcp.tools.project.ProjectIngestor", return_value=mock_ingestor):
        result = _tool_ingest_project({
            "slug": "proj",
            "source": str(tmp_path),
            "mode": "full",
        })

    data = json.loads(result)
    assert data["ingested"] is True
    mock_ingestor.ingest_local.assert_called_once()


def test_ingest_project_exception():
    """Lines 134-135: ProjectIngestor raises → error JSON, ingested=False."""
    mock_ingestor = MagicMock()
    mock_ingestor.ingest_local.side_effect = RuntimeError("filesystem error")

    with patch("depthfusion.mcp.tools.project.ProjectIngestor", return_value=mock_ingestor):
        result = _tool_ingest_project({
            "slug": "proj",
            "source": "/tmp/local-source",
            "mode": "structural",
        })

    data = json.loads(result)
    assert data["ingested"] is False
    assert "filesystem error" in data["error"]


# ── register_project stub ─────────────────────────────────────────────────────

def test_register_project_callable():
    """Lines 260-262: stub must not raise."""
    register_project()


# ── _tool_session_seed early returns ─────────────────────────────────────────

def test_session_seed_empty_session_id():
    """Lines 139-141, 177-181, 183-187: empty session_id → immediate error JSON.

    Covers the leading setup lines (139-181) plus the early-exit branch.
    project_slug absent so the project-context block (142-175) is skipped.
    """
    result = _tool_session_seed({"session_id": ""})
    data = json.loads(result)
    assert data["error"] == "session_id required"
    assert data["published"] == 0
    assert "project_slug" not in data


def test_session_seed_empty_session_id_with_project_slug():
    """Lines 185-186: project_slug present in early-exit result."""
    result = _tool_session_seed({"session_id": "", "project_slug": "my-proj"})
    data = json.loads(result)
    assert data["error"] == "session_id required"
    assert data["project_slug"] == "my-proj"


def test_session_seed_fabric_mode_no_projects():
    """Lines 189-194: fabric_seed mode with no projects list → error JSON."""
    result = _tool_session_seed({"session_id": "s1", "mode": "fabric_seed"})
    data = json.loads(result)
    assert "error" in data
    assert "projects" in data["error"]
    assert data["session_id"] == "s1"
