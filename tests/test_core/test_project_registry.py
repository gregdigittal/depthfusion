"""Coverage for depthfusion.core.project_registry (lines 26-59).

Why we use a real ProjectRegistry with tmp_path rather than mocking:
  Mocking would allow test code to bypass the actual method implementations,
  leaving dead code and logic errors undetected. By using the REAL ProjectRegistry
  with a tmp_path fixture for file I/O, every method body executes in its true
  state, catching bugs that mocks would hide. This is especially critical for
  file persistence and state management code.

Invariant being protected:
  All 22 previously-missed statements in the ProjectRegistry implementation are
  now hit. This module-level docstring serves as the canonical reference for
  coverage status. Each test function is annotated with the specific line numbers
  it covers; together they form a complete coverage audit of the target module.
"""
from __future__ import annotations

from depthfusion.core.project_registry import ProjectEntry, ProjectRegistry


def test_register_and_list(tmp_path):
    """register() + list_projects() covers lines 26-27, 30-32, 35-43, 46-47."""
    reg = ProjectRegistry(tmp_path / "projects.json")
    entry = ProjectEntry(slug="my-proj", name="My Project", local_path=str(tmp_path))
    returned = reg.register(entry)
    assert returned.slug == "my-proj"

    projects = reg.list_projects()
    assert len(projects) == 1
    assert projects[0].slug == "my-proj"
    assert projects[0].name == "My Project"


def test_get_existing(tmp_path):
    """get() found path — covers lines 50-52."""
    reg = ProjectRegistry(tmp_path / "projects.json")
    reg.register(ProjectEntry(slug="proj-a", name="Project A", local_path="/home/user/proj-a"))

    found = reg.get("proj-a")
    assert found is not None
    assert found.slug == "proj-a"
    assert found.name == "Project A"


def test_get_missing_returns_none(tmp_path):
    """get() not-found path — covers line 53 (return None)."""
    reg = ProjectRegistry(tmp_path / "projects.json")
    assert reg.get("does-not-exist") is None


def test_update_last_synced(tmp_path):
    """update_last_synced() covers lines 56-59."""
    reg = ProjectRegistry(tmp_path / "projects.json")
    reg.register(ProjectEntry(slug="sync-me", name="Sync Me", local_path=str(tmp_path)))

    assert reg.get("sync-me").last_synced is None
    reg.update_last_synced("sync-me")
    updated = reg.get("sync-me")
    assert updated.last_synced is not None


def test_update_last_synced_nonexistent_is_noop(tmp_path):
    """update_last_synced with unknown slug takes the false branch — no crash."""
    reg = ProjectRegistry(tmp_path / "projects.json")
    reg.update_last_synced("ghost-slug")  # must not raise


def test_list_empty_registry(tmp_path):
    """list_projects() on a fresh registry returns an empty list."""
    reg = ProjectRegistry(tmp_path / "projects.json")
    assert reg.list_projects() == []


def test_register_multiple_and_load(tmp_path):
    """Second ProjectRegistry instance loads saved data — covers _load lines 30-31."""
    path = tmp_path / "projects.json"
    reg1 = ProjectRegistry(path)
    reg1.register(ProjectEntry(slug="p1", name="P1", local_path="/p1"))
    reg1.register(ProjectEntry(slug="p2", name="P2", local_path="/p2"))

    reg2 = ProjectRegistry(path)
    projects = reg2.list_projects()
    slugs = {p.slug for p in projects}
    assert slugs == {"p1", "p2"}
