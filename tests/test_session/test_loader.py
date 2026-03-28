"""Tests for session/loader.py — SessionLoader."""
from pathlib import Path

import yaml

from depthfusion.core.types import SessionBlock
from depthfusion.session.loader import SessionLoader


def write_session(sessions_dir: Path, name: str, content: str, project: str = "unknown") -> Path:
    """Helper to create a .tmp session file and its .meta.yaml sidecar."""
    session_file = sessions_dir / f"{name}.tmp"
    session_file.write_text(content)
    sidecar = sessions_dir / f"{name}.meta.yaml"
    meta = {
        "session_id": name,
        "project": project,
        "category": "feature",
        "keywords": content.split()[:5],
        "entities": [],
        "tagged_at": "2026-03-28T05:00:00+00:00",
    }
    with sidecar.open("w") as f:
        yaml.dump(meta, f)
    return session_file


class TestSessionLoaderLoadRelevant:
    def test_returns_at_most_top_k_blocks(self, tmp_path):
        for i in range(10):
            write_session(tmp_path, f"session-{i:02d}", f"python debugging hooks session {i}")
        loader = SessionLoader(sessions_dir=tmp_path, top_k=3)
        results = loader.load_relevant("python debugging")
        assert len(results) <= 3

    def test_returns_session_blocks(self, tmp_path):
        write_session(tmp_path, "s1", "python debugging content")
        loader = SessionLoader(sessions_dir=tmp_path, top_k=5)
        results = loader.load_relevant("python")
        for item in results:
            assert isinstance(item, SessionBlock)

    def test_empty_dir_returns_empty(self, tmp_path):
        loader = SessionLoader(sessions_dir=tmp_path, top_k=5)
        results = loader.load_relevant("anything")
        assert results == []

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        missing = tmp_path / "no-such-dir"
        loader = SessionLoader(sessions_dir=missing, top_k=5)
        results = loader.load_relevant("anything")
        assert results == []

    def test_relevant_blocks_scored_higher_ranked_first(self, tmp_path):
        write_session(tmp_path, "relevant", "python debugging hooks depthfusion scoring")
        write_session(tmp_path, "irrelevant", "shopping list eggs bread milk")
        loader = SessionLoader(sessions_dir=tmp_path, top_k=5)
        results = loader.load_relevant("python debugging depthfusion")
        session_ids = [b.session_id for b in results]
        assert session_ids[0] == "relevant", "Most relevant block must come first"


class TestSessionLoaderLoadAll:
    def test_load_all_returns_all_blocks(self, tmp_path):
        write_session(tmp_path, "alpha", "content alpha")
        write_session(tmp_path, "beta", "content beta")
        write_session(tmp_path, "gamma", "content gamma")
        loader = SessionLoader(sessions_dir=tmp_path)
        results = loader.load_all()
        session_ids = {b.session_id for b in results}
        assert session_ids == {"alpha", "beta", "gamma"}

    def test_load_all_empty_dir_returns_empty(self, tmp_path):
        loader = SessionLoader(sessions_dir=tmp_path)
        assert loader.load_all() == []


class TestSessionLoaderLoadByProject:
    def test_load_by_project_filters_correctly(self, tmp_path):
        write_session(tmp_path, "ccrs-session", "CCRS content", project="agreement_automation")
        write_session(tmp_path, "va-session", "VA content", project="virtual_analyst")
        write_session(tmp_path, "social-session", "Social content", project="social-media")

        loader = SessionLoader(sessions_dir=tmp_path)
        results = loader.load_by_project("agreement_automation")
        session_ids = {b.session_id for b in results}
        assert session_ids == {"ccrs-session"}, "Must return only agreement_automation sessions"

    def test_load_by_project_no_matches_returns_empty(self, tmp_path):
        write_session(tmp_path, "s1", "content", project="other-project")
        loader = SessionLoader(sessions_dir=tmp_path)
        results = loader.load_by_project("nonexistent-project")
        assert results == []

    def test_load_by_project_uses_meta_yaml_sidecar(self, tmp_path):
        write_session(tmp_path, "tagged-session", "content", project="depthfusion")
        loader = SessionLoader(sessions_dir=tmp_path)
        results = loader.load_by_project("depthfusion")
        assert len(results) == 1
        assert results[0].session_id == "tagged-session"

    def test_load_by_project_tags_include_project(self, tmp_path):
        write_session(tmp_path, "proj-session", "python hooks", project="agreement_automation")
        loader = SessionLoader(sessions_dir=tmp_path)
        results = loader.load_by_project("agreement_automation")
        assert len(results) == 1
        assert "agreement_automation" in results[0].tags
