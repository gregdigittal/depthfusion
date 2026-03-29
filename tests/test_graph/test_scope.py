"""Tests for scope module."""
import json
import pytest
from pathlib import Path

from depthfusion.graph.scope import read_scope, write_scope, default_scope, _DEFAULT_SCOPE_PATH
from depthfusion.graph.types import GraphScope


def test_default_scope_returns_project_mode():
    scope = default_scope(project="depthfusion", session_id="sess001")
    assert scope.mode == "project"
    assert scope.active_projects == ["depthfusion"]
    assert scope.session_id == "sess001"


def test_write_and_read_roundtrip(tmp_path):
    scope_file = tmp_path / ".depthfusion-session-scope.json"
    scope = GraphScope(
        mode="cross_project",
        active_projects=["depthfusion", "skillforge"],
        session_id="sess002",
        set_at="2026-03-28T10:00:00",
    )
    write_scope(scope, path=scope_file)
    loaded = read_scope(path=scope_file)
    assert loaded.mode == "cross_project"
    assert loaded.active_projects == ["depthfusion", "skillforge"]


def test_read_missing_file_returns_none(tmp_path):
    result = read_scope(path=tmp_path / "nonexistent.json")
    assert result is None


def test_write_creates_parent_dirs(tmp_path):
    nested = tmp_path / "subdir" / "scope.json"
    scope = default_scope(project="depthfusion", session_id="s1")
    write_scope(scope, path=nested)
    assert nested.exists()


def test_scope_project_filter():
    scope = GraphScope(
        mode="project",
        active_projects=["depthfusion"],
        session_id="sess003",
        set_at="2026-03-28T10:00:00",
    )
    assert "skillforge" not in scope.active_projects


def test_global_scope_empty_projects():
    scope = GraphScope(
        mode="global",
        active_projects=[],
        session_id="sess004",
        set_at="2026-03-28T10:00:00",
    )
    assert scope.mode == "global"
    assert scope.active_projects == []


def test_invalid_json_returns_none(tmp_path):
    bad_file = tmp_path / "scope.json"
    bad_file.write_text("not json", encoding="utf-8")
    result = read_scope(path=bad_file)
    assert result is None


def test_default_scope_set_at_is_iso():
    import re
    scope = default_scope(project="depthfusion", session_id="s1")
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", scope.set_at)


def test_write_scope_serializes_all_fields(tmp_path):
    scope_file = tmp_path / "scope.json"
    scope = GraphScope(
        mode="project",
        active_projects=["depthfusion"],
        session_id="sess-abc",
        set_at="2026-03-28T00:00:00",
    )
    write_scope(scope, path=scope_file)
    data = json.loads(scope_file.read_text())
    assert data["mode"] == "project"
    assert data["session_id"] == "sess-abc"


def test_read_scope_from_default_path_when_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "depthfusion.graph.scope._DEFAULT_SCOPE_PATH",
        tmp_path / "scope.json"
    )
    scope = default_scope("depthfusion", "s1")
    write_scope(scope)
    loaded = read_scope()
    assert loaded is not None
    assert loaded.mode == "project"
