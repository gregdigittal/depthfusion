"""Tests for session/tagger.py — SessionTagger.

C1 SAFETY CONSTRAINT: tagger must NEVER modify the source .tmp file.
"""
import hashlib

import pytest
import yaml

from depthfusion.session.tagger import SessionTagger

VALID_CATEGORIES = {"debugging", "feature", "refactor", "planning", "research", "other"}


class TestSessionTaggerSidecar:
    def test_tag_session_creates_meta_yaml(self, tmp_path):
        session_file = tmp_path / "my-session-abc123.tmp"
        session_file.write_text("Debugging a hook in depthfusion/core/types.py")
        tagger = SessionTagger()
        tagger.tag_session(session_file)
        sidecar = tmp_path / "my-session-abc123.meta.yaml"
        assert sidecar.exists(), "Sidecar .meta.yaml must be created alongside the .tmp file"

    def test_c1_tmp_file_byte_identical_after_tagging(self, tmp_path):
        """C1 SAFETY: The .tmp file must not be modified in any way."""
        session_file = tmp_path / "session-c1-check.tmp"
        content = b"Some session content with hooks and depthfusion paths"
        session_file.write_bytes(content)
        digest_before = hashlib.sha256(session_file.read_bytes()).hexdigest()
        mtime_before = session_file.stat().st_mtime

        tagger = SessionTagger()
        tagger.tag_session(session_file)

        digest_after = hashlib.sha256(session_file.read_bytes()).hexdigest()
        mtime_after = session_file.stat().st_mtime

        assert digest_before == digest_after, "C1 VIOLATION: .tmp file content was modified"
        assert mtime_before == mtime_after, "C1 VIOLATION: .tmp file mtime was changed"

    def test_sidecar_contains_required_keys(self, tmp_path):
        session_file = tmp_path / "session-keys-check.tmp"
        session_file.write_text("Refactoring the agreement_automation project")
        tagger = SessionTagger()
        meta = tagger.tag_session(session_file)

        required_keys = {"session_id", "project", "category", "keywords", "entities", "tagged_at"}
        assert required_keys.issubset(meta.keys()), (
            f"Missing keys: {required_keys - meta.keys()}"
        )

    def test_sidecar_yaml_matches_returned_dict(self, tmp_path):
        session_file = tmp_path / "session-yaml-match.tmp"
        session_file.write_text("Planning a new feature for the virtual_analyst project")
        tagger = SessionTagger()
        returned_meta = tagger.tag_session(session_file)

        sidecar = tmp_path / "session-yaml-match.meta.yaml"
        with sidecar.open() as f:
            disk_meta = yaml.safe_load(f)

        assert returned_meta == disk_meta

    def test_session_id_matches_stem(self, tmp_path):
        session_file = tmp_path / "my-fancy-session-001.tmp"
        session_file.write_text("Some content")
        tagger = SessionTagger()
        meta = tagger.tag_session(session_file)
        assert meta["session_id"] == "my-fancy-session-001"

    def test_category_is_valid_value(self, tmp_path):
        session_file = tmp_path / "cat-check.tmp"
        session_file.write_text("Writing unit tests and implementing a new feature in Python")
        tagger = SessionTagger()
        meta = tagger.tag_session(session_file)
        assert meta["category"] in VALID_CATEGORIES, (
            f"category '{meta['category']}' not in {VALID_CATEGORIES}"
        )

    def test_keywords_is_list(self, tmp_path):
        session_file = tmp_path / "keywords-check.tmp"
        session_file.write_text("Python hooks session scoring depthfusion core types")
        tagger = SessionTagger()
        meta = tagger.tag_session(session_file)
        assert isinstance(meta["keywords"], list)
        assert len(meta["keywords"]) <= 5

    def test_entities_is_list(self, tmp_path):
        session_file = tmp_path / "entities-check.tmp"
        session_file.write_text(
            "Found bug in src/depthfusion/core/types.py inside class FeedbackStore"
        )
        tagger = SessionTagger()
        meta = tagger.tag_session(session_file)
        assert isinstance(meta["entities"], list)

    def test_tagged_at_is_iso_string(self, tmp_path):
        session_file = tmp_path / "tagged-at.tmp"
        session_file.write_text("Some content")
        tagger = SessionTagger()
        meta = tagger.tag_session(session_file)
        assert isinstance(meta["tagged_at"], str)
        # Must be parseable as ISO 8601
        from datetime import datetime
        datetime.fromisoformat(meta["tagged_at"])

    def test_nonexistent_session_raises_file_not_found(self, tmp_path):
        missing = tmp_path / "does-not-exist.tmp"
        tagger = SessionTagger()
        with pytest.raises(FileNotFoundError):
            tagger.tag_session(missing)

    def test_idempotent_overwrites_existing_sidecar(self, tmp_path):
        session_file = tmp_path / "idempotent-session.tmp"
        session_file.write_text("Debugging hooks in depthfusion")
        sidecar = tmp_path / "idempotent-session.meta.yaml"

        tagger = SessionTagger()
        tagger.tag_session(session_file)
        # write stale content to sidecar
        sidecar.write_text("stale: true\n")
        meta2 = tagger.tag_session(session_file)

        with sidecar.open() as f:
            disk_meta = yaml.safe_load(f)

        assert disk_meta == meta2, "Second call must overwrite the sidecar"
        assert "stale" not in disk_meta, "Stale data must be gone after idempotent re-tag"


class TestExtractTags:
    def test_project_detected_agreement_automation(self, tmp_path):
        session_file = tmp_path / "proj-detect.tmp"
        session_file.write_text(
            "Working on /home/user/projects/agreement_automation/src/models.py"
        )
        tagger = SessionTagger()
        meta = tagger.tag_session(session_file)
        assert meta["project"] == "agreement_automation"

    def test_project_detected_social_media(self, tmp_path):
        session_file = tmp_path / "proj-social.tmp"
        session_file.write_text("Publishing to social-media agent pipeline")
        tagger = SessionTagger()
        meta = tagger.tag_session(session_file)
        assert meta["project"] == "social-media"

    def test_unknown_project_fallback(self, tmp_path):
        session_file = tmp_path / "proj-unknown.tmp"
        session_file.write_text("Some random content without any known project name")
        tagger = SessionTagger()
        meta = tagger.tag_session(session_file)
        assert meta["project"] == "unknown"

    def test_category_debugging_detected(self, tmp_path):
        session_file = tmp_path / "cat-debug.tmp"
        session_file.write_text("Debugging error: AttributeError in traceback line 42 fix bug")
        tagger = SessionTagger()
        meta = tagger.tag_session(session_file)
        assert meta["category"] == "debugging"

    def test_category_planning_detected(self, tmp_path):
        session_file = tmp_path / "cat-plan.tmp"
        session_file.write_text(
            "Planning the roadmap and architecture design for the next sprint milestones"
        )
        tagger = SessionTagger()
        meta = tagger.tag_session(session_file)
        assert meta["category"] == "planning"
